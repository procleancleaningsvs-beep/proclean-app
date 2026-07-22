from __future__ import annotations

import json
import sqlite3
from typing import Any

from modules.exportacion_imss.exportacion_service import mapear_headcount_a_movimiento
from modules.gestion_idse_sua.nominas import repository as nom_repo
from modules.gestion_idse_sua.nominas.trajectory_service import build_trajectories_for_workers
from modules.gestion_idse_sua.nominas.text_utils import normalize_upper
from modules.gestion_idse_sua.nominas.trajectory_service import resolve_worker_identity
from modules.gestion_idse_sua.reportes import repository as repo
from modules.gestion_idse_sua.reportes.date_utils import clip_iso_dates_to_month, days_in_calendar_month
from modules.gestion_idse_sua.reportes.monthly_status import (
    classify_monthly_status,
    compute_totals,
    first_and_last_a,
    person_has_active_evidence,
)
from modules.gestion_idse_sua.reportes.period_selection import validate_week_selection


def _parse_hc_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, list) and data:
        return data[0] if isinstance(data[0], dict) else None
    return data if isinstance(data, dict) else None


def _worker_cliente(worker: dict[str, Any]) -> str:
    return normalize_upper(worker.get("cliente_confirmado") or "")


def _build_daily_grid(daily: list[dict[str, Any]], *, mes: int, anio: int) -> list[dict[str, Any]]:
    by_date = {str(d.get("fecha_iso") or ""): d for d in daily}
    grid: list[dict[str, Any]] = []
    for day in days_in_calendar_month(mes=mes, anio=anio):
        iso = day.isoformat()
        record = by_date.get(iso)
        grid.append(
            {
                "fecha_iso": iso,
                "code": (record or {}).get("code_normalized") or "",
                "status": (record or {}).get("interpretation_status") or "",
                "warning": (record or {}).get("warning") or "",
            }
        )
    return grid


def _trajectory_events_to_report_events(
    identity_key: str,
    traj_events: list[dict[str, Any]],
    *,
    mes: int,
    anio: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in traj_events:
        event_type = str(event.get("event_type") or "")
        if event_type not in {"posible_baja", "posible_reingreso"}:
            continue
        fecha = str(event.get("fecha_sugerida") or "")
        if fecha:
            try:
                from datetime import datetime

                day = datetime.strptime(fecha, "%Y-%m-%d").date()
                if day.month != mes or day.year != anio:
                    continue
            except ValueError:
                continue
        suggested = "BAJA" if event_type == "posible_baja" else "ALTA"
        estado = "propuesto"
        if str(event.get("status") or "") == "review" or not fecha:
            estado = "incompleto"
        out.append(
            {
                "identity_key": identity_key,
                "event_type_suggested": suggested,
                "fecha_suggested": fecha,
                "estado": estado,
                "motivo": event.get("reason") or "",
                "segment": event,
            }
        )
    return out


def generate_monthly_report(
    conn: sqlite3.Connection,
    *,
    report_id: int,
    period_ids: list[int],
    cliente: str,
    mes: int,
    anio: int,
) -> dict[str, Any]:
    report = repo.get_report(conn, report_id)
    if report is None:
        raise ValueError("Reporte no encontrado.")

    validation = validate_week_selection(conn, period_ids, cliente=cliente, mes=mes, anio=anio)
    if not validation["ok"]:
        raise ValueError("; ".join(validation["errors"]))

    cliente_norm = normalize_upper(cliente)
    period_ids_ordered = [w["period_id"] for w in validation["weeks"]]
    repo.replace_report_weeks(conn, report_id, validation["weeks"])

    workers: list[dict[str, Any]] = []
    worker_meta: dict[int, dict[str, Any]] = {}
    attendance_rows: list[dict[str, Any]] = []
    matches: dict[int, dict[str, Any] | None] = {}
    for period_id in period_ids_ordered:
        for worker in nom_repo.list_workers(conn, period_id):
            wid = int(worker["id"])
            worker_meta[wid] = worker
            if _worker_cliente(worker) == cliente_norm:
                workers.append(worker)
                for row in nom_repo.list_attendance_for_period(conn, period_id):
                    if int(row["worker_id"]) == wid:
                        attendance_rows.append(row)
                matches[wid] = nom_repo.get_match(conn, wid)

    trajectory_payload = build_trajectories_for_workers(workers, attendance_rows, matches)
    trajectories: dict[str, Any] = trajectory_payload.get("trajectories") or {}
    unresolved_ids = set(trajectory_payload.get("unresolved_worker_ids") or [])

    persons: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    pendientes: list[dict[str, Any]] = []
    warnings = list(validation["warnings"])

    identity_workers: dict[str, list[int]] = {}
    for worker in workers:
        wid = int(worker["id"])
        match = nom_repo.get_match(conn, wid)
        identity_key, resolved = resolve_worker_identity(worker, match)
        if not identity_key or not resolved:
            if wid in unresolved_ids or not _worker_cliente(worker):
                pendientes.append(
                    {
                        "tipo": "identidad_no_resuelta",
                        "worker_id": wid,
                        "nombre": worker.get("nombre_normalizado"),
                        "num_empleado": worker.get("num_empleado"),
                        "detalle": "Sin identidad confirmada para consolidación mensual.",
                    }
                )
            continue
        identity_workers.setdefault(identity_key, []).append(wid)

    selected_week_count = len(period_ids_ordered)
    for identity_key, worker_ids in identity_workers.items():
        traj = trajectories.get(identity_key) or {}
        daily_all = traj.get("daily") or []
        daily_month = clip_iso_dates_to_month(daily_all, mes=mes, anio=anio)
        if not person_has_active_evidence(daily_month):
            continue
        totals = compute_totals(daily_month)
        primera_a, ultima_a = first_and_last_a(daily_month)
        weeks_present = sorted(
            {
                int(d.get("period_id") or 0)
                for d in daily_month
                if str(d.get("code_normalized") or "") in {"A", "I", "V", "F", "D"}
            }
        )
        rep_workers = [worker_meta[wid] for wid in worker_ids if wid in worker_meta]
        sample = rep_workers[0]
        match = nom_repo.get_match(conn, int(sample["id"]))
        hc = _parse_hc_json((match or {}).get("hc_json"))
        mapped = mapear_headcount_a_movimiento(hc) if hc else {}
        plantas = sorted({normalize_upper(w.get("planta_normalizada") or "") for w in rep_workers if w.get("planta_normalizada")})
        clientes = sorted({normalize_upper(w.get("cliente_confirmado") or "") for w in rep_workers if w.get("cliente_confirmado")})
        semanas = [
            {
                "period_id": pid,
                "fecha_inicio": next(
                    (w["fecha_inicio"] for w in repo.list_report_weeks(conn, report_id) if w["period_id"] == pid),
                    "",
                ),
            }
            for pid in weeks_present
        ]
        traj_events = traj.get("events") or []
        estado = classify_monthly_status(
            daily=daily_month,
            events=traj_events,
            selected_week_count=selected_week_count,
            weeks_with_presence=len(weeks_present),
        )
        person_warnings = list(traj.get("warnings") or [])
        persons.append(
            {
                "identity_key": identity_key,
                "worker_ids": worker_ids,
                "num_empleado": sample.get("num_empleado") or mapped.get("numero_empleado"),
                "nombre_nomina": sample.get("nombre_normalizado") or "",
                "nombre_hc": (match or {}).get("hc_nombre") or "",
                "match_method": (match or {}).get("match_method") or "",
                "match_status": (match or {}).get("status") or "",
                "nss": (match or {}).get("nss") or mapped.get("nss") or "",
                "rfc": (match or {}).get("rfc") or mapped.get("rfc") or "",
                "curp": (match or {}).get("curp") or mapped.get("curp") or "",
                "sbc": str(mapped.get("sbc") or ""),
                "clientes": clientes,
                "plantas": plantas,
                "semanas": semanas,
                "estado_mensual": estado,
                "totals": totals,
                "primera_a": primera_a,
                "ultima_a": ultima_a,
                "afiliatorios": mapped,
                "warnings": person_warnings,
                "daily": _build_daily_grid(daily_month, mes=mes, anio=anio),
                "trajectory": {
                    "events": traj_events,
                    "sequence": traj.get("sequence") or [],
                    "totals": traj.get("totals") or {},
                },
            }
        )
        events.extend(
            _trajectory_events_to_report_events(identity_key, traj_events, mes=mes, anio=anio)
        )

    for wid in unresolved_ids:
        worker = worker_meta.get(wid)
        if not worker or _worker_cliente(worker) != cliente_norm:
            continue
        pendientes.append(
            {
                "tipo": "identidad_no_resuelta",
                "worker_id": wid,
                "nombre": worker.get("nombre_normalizado"),
                "num_empleado": worker.get("num_empleado"),
                "detalle": "Trabajador visible pero sin identidad confirmada.",
            }
        )

    person_id_map = repo.replace_report_persons(conn, report_id, persons)
    repo.insert_report_events(conn, report_id, events, person_id_map=person_id_map)
    snapshot = {
        "period_ids": period_ids_ordered,
        "person_count": len(persons),
        "pending_count": len(pendientes),
        "pendientes": pendientes,
    }
    repo.update_report_status(
        conn,
        report_id,
        estado="generado",
        warnings=warnings,
        snapshot=snapshot,
    )
    conn.commit()
    return {
        "report_id": report_id,
        "persons": len(persons),
        "events": len(events),
        "pendientes": len(pendientes),
        "warnings": warnings,
    }
