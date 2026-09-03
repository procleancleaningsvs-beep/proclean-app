from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from modules.gestion_idse_sua.nominas import repository as repo
from modules.gestion_idse_sua.nominas.match_service import (
    _hc_key,
    build_review_match,
    load_full_headcount,
    match_headcount_keys,
    match_worker,
    rejected_candidate_keys,
)
from modules.gestion_idse_sua.nominas.planta_cliente_service import (
    period_cut_warnings,
)
from modules.gestion_idse_sua.nominas.text_utils import json_dumps, normalize_name, normalize_upper


def _hc_cliente(match: dict[str, Any]) -> str:
    direct = normalize_upper(match.get("hc_cliente"))
    if direct:
        return direct
    raw = match.get("hc_json")
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if isinstance(data, list):
        data = data[0] if data else {}
    if isinstance(data, dict):
        return normalize_upper(data.get("cliente"))
    return ""


def _first_attendance_dates(conn: sqlite3.Connection, period_id: int) -> dict[int, str]:
    first_by_worker: dict[int, str] = {}
    for row in repo.list_attendance_for_period(conn, period_id):
        if str(row.get("code_normalized") or "").upper() != "A":
            continue
        if str(row.get("interpretation_status") or "ok") not in {"ok", "corrected"}:
            continue
        worker_id = int(row["worker_id"])
        if worker_id in first_by_worker:
            continue
        try:
            first_by_worker[worker_id] = datetime.strptime(
                str(row.get("fecha_iso") or ""), "%Y-%m-%d"
            ).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return first_by_worker


RESULTADO_LABELS = {
    "coincidencia": ("Coincidencia", "azul"),
    "posible_alta": ("Posible alta", "verde"),
    "posible_baja": ("Posible baja", "rojo"),
    "reingreso": ("Reingreso", "verde"),
    "revision": ("Revisión", "amarillo"),
}


def _match_record(match: dict[str, Any]) -> dict[str, Any]:
    raw = match.get("hc_json")
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _candidate_records(match: dict[str, Any]) -> list[dict[str, Any]]:
    raw = match.get("hc_json")
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _operation_state(row: dict[str, Any]) -> str:
    status = normalize_upper(row.get("status_operacion"))
    if status in {"BAJA", "INACTIVO", "INACTIVA"}:
        return "baja"
    if status in {"ALTA", "ACTIVO", "ACTIVA"}:
        return "active"
    # Los fixtures históricos inyectaban únicamente filas activas sin el campo.
    if not status:
        return "active"
    return "unknown"


def apply_identity_resolution_to_result(
    conn: sqlite3.Connection,
    *,
    worker_id: int,
    result_id: int,
    match: dict[str, Any],
    changed_by: str | None,
) -> None:
    status = str(match.get("status") or "unmatched")
    record = _match_record(match)
    operation_state = _operation_state(record) if record else "unknown"
    if status in {"confirmed", "manual"} and operation_state == "baja":
        resultado = "Reingreso"
        semaforo = "verde"
        movimiento = "ALTA"
        observaciones = "Identidad confirmada; antecedente Headcount en BAJA."
    elif status in {"confirmed", "manual"}:
        resultado = "Coincidencia"
        semaforo = "azul"
        movimiento = ""
        observaciones = "Identidad confirmada manualmente."
    elif status in {"review", "suggested"}:
        resultado = "Revisión"
        semaforo = "amarillo"
        movimiento = ""
        observaciones = "Candidatos de identidad pendientes de resolución."
    else:
        resultado = "Posible alta"
        semaforo = "verde"
        movimiento = "ALTA"
        observaciones = "Sin coincidencias después de la resolución manual."
    repo.update_result_identity_outcome(
        conn,
        result_id=result_id,
        worker_id=worker_id,
        resultado=resultado,
        semaforo=semaforo,
        tipo_sugerido=movimiento,
        observaciones=observaciones,
        changed_by=changed_by,
    )


def _reconcile_worker_matches(
    conn: sqlite3.Connection,
    workers: list[dict[str, Any]],
    headcount_rows: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    rows_by_key: dict[str, dict[str, Any]] = {}
    for row in headcount_rows:
        rows_by_key.setdefault(_hc_key(row), row)

    proposals: dict[int, dict[str, Any]] = {}
    persisted_by_worker: dict[int, dict[str, Any]] = {}
    claims: dict[str, list[int]] = {}
    worker_by_id = {int(worker["id"]): worker for worker in workers}

    for worker_id, worker in worker_by_id.items():
        persisted = repo.get_match(conn, worker_id) or {}
        persisted_by_worker[worker_id] = persisted
        persisted_status = str(persisted.get("status") or "")
        persisted_keys = match_headcount_keys(persisted) & rows_by_key.keys()
        if persisted_status in {"confirmed", "manual"} and len(persisted_keys) == 1:
            proposal = dict(persisted)
            proposal["headcount_key"] = next(iter(persisted_keys))
        else:
            proposal = match_worker(
                worker,
                headcount_rows,
                cliente=normalize_upper(
                    worker.get("cliente_confirmado") or worker.get("cliente_sugerido")
                )
                or None,
                include_candidates=False,
            )
        proposals[worker_id] = proposal
        if str(proposal.get("status") or "") in {"auto", "confirmed", "manual"}:
            key = str(proposal.get("headcount_key") or "").strip()
            if key:
                claims.setdefault(key, []).append(worker_id)

    reconciled: dict[int, dict[str, Any]] = {}
    consumed: set[str] = set()
    for key, worker_ids in claims.items():
        if len(worker_ids) == 1:
            worker_id = worker_ids[0]
            reconciled[worker_id] = proposals[worker_id]
            consumed.add(key)
            continue
        candidate = rows_by_key.get(key)
        for worker_id in worker_ids:
            reconciled[worker_id] = build_review_match(
                [candidate] if candidate else [],
                method="registro_headcount_compartido",
                reason="El mismo registro Headcount coincide con varias filas de nómina",
                confidence=1.0,
            )

    for worker_id, worker in worker_by_id.items():
        if worker_id in reconciled:
            continue
        rejected_keys = rejected_candidate_keys(
            persisted_by_worker.get(worker_id) or {}, worker, headcount_rows
        )
        match = match_worker(
            worker,
            headcount_rows,
            cliente=normalize_upper(
                worker.get("cliente_confirmado") or worker.get("cliente_sugerido")
            )
            or None,
            include_candidates=True,
            excluded_headcount_keys=consumed | rejected_keys,
        )
        if match.get("status") == "unmatched" and rejected_keys:
            match = persisted_by_worker[worker_id]
        reconciled[worker_id] = match

    for worker_id, match in reconciled.items():
        repo.upsert_match(conn, worker_id, match)
    return reconciled


def enrich_workers(conn: sqlite3.Connection, period_id: int, headcount_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = headcount_rows if headcount_rows is not None else load_full_headcount()
    workers = repo.list_workers(conn, period_id)
    matches = _reconcile_worker_matches(conn, workers, rows)
    enriched = sum(
        1 for match in matches.values() if match.get("status") not in {"unmatched", None}
    )
    conn.commit()
    return {"workers": len(workers), "enriched": enriched}


def run_comparative(
    conn: sqlite3.Connection,
    *,
    period_id: int,
    cliente: str,
    generated_by: str | None,
    headcount_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cliente_norm = normalize_upper(cliente)
    if not cliente_norm:
        raise ValueError("Cliente obligatorio para comparar.")

    hc_rows = headcount_rows if headcount_rows is not None else load_full_headcount()
    all_workers = repo.list_workers(conn, period_id)
    matches = _reconcile_worker_matches(conn, all_workers, hc_rows)
    workers = [
        worker
        for worker in all_workers
        if normalize_upper(worker.get("cliente_confirmado")) == cliente_norm
    ]
    if not workers:
        raise ValueError(
            f"No hay trabajadores con cliente confirmado para comparar contra {cliente_norm}."
        )

    warnings: list[str] = []
    period = conn.execute("SELECT * FROM gis_nomina_periods WHERE id = ?", (period_id,)).fetchone()
    if period and period["fecha_inicio"] and period["fecha_fin"]:
        warnings.extend(
            period_cut_warnings(
                conn,
                cliente_norm,
                str(period["fecha_inicio"]),
                str(period["fecha_fin"]),
            )
        )

    first_attendance_by_worker = _first_attendance_dates(conn, period_id)

    comparative_id = repo.create_comparative(
        conn,
        period_id=period_id,
        cliente=cliente_norm,
        generated_by=generated_by,
        warnings=warnings,
    )

    totals = {
        "nomina": len(workers),
        "coincidencias": 0,
        "altas": 0,
        "bajas": 0,
        "reingresos": 0,
        "revisiones": 0,
        "sin_match": 0,
    }

    for worker in workers:
        wid = int(worker["id"])
        match = matches.get(wid) or {}
        status = str(match.get("status") or "unmatched")
        hc_name = normalize_name(match.get("hc_nombre"))
        hc_cliente = _hc_cliente(match)
        match_record = _match_record(match)
        operation_state = _operation_state(match_record) if match_record else "unknown"

        if status in {"suggested", "review"}:
            tipo = "Revisión"
            sem = "amarillo"
            totals["revisiones"] += 1
            candidates = _candidate_records(match)
            possible_reentry = any(_operation_state(candidate) == "baja" for candidate in candidates)
            observaciones = (
                "Posible reingreso; candidato histórico BAJA pendiente de confirmación."
                if possible_reentry
                else f"Match {match.get('match_method')} pendiente de confirmación."
            )
            tipo_mov = ""
        elif status in {"auto", "confirmed", "manual"} and hc_name:
            if operation_state == "baja":
                tipo = "Reingreso"
                sem = "verde"
                totals["reingresos"] += 1
                tipo_mov = "ALTA"
                context = []
                if hc_cliente:
                    context.append(f"cliente anterior {hc_cliente}")
                previous_location = normalize_upper(
                    match_record.get("ubicacion") or match_record.get("planta")
                )
                if previous_location:
                    context.append(f"ubicación anterior {previous_location}")
                suffix = f" ({'; '.join(context)})" if context else ""
                observaciones = f"Baja histórica — posible reingreso detectado{suffix}."
            elif operation_state == "active" and (
                status in {"confirmed", "manual"}
                or not hc_cliente
                or hc_cliente == cliente_norm
            ):
                tipo = "Coincidencia"
                sem = "azul"
                totals["coincidencias"] += 1
                observaciones = (
                    f"Identidad confirmada; Headcount registrado en {hc_cliente}."
                    if status in {"confirmed", "manual"}
                    and hc_cliente
                    and hc_cliente != cliente_norm
                    else ""
                )
                tipo_mov = ""
            elif operation_state == "active":
                tipo = "Revisión"
                sem = "amarillo"
                totals["revisiones"] += 1
                observaciones = f"Empleado activo en otro cliente: {hc_cliente or 'sin cliente'}."
                tipo_mov = ""
            else:
                tipo = "Revisión"
                sem = "amarillo"
                totals["revisiones"] += 1
                observaciones = "Estado operativo de Headcount pendiente de confirmar."
                tipo_mov = ""
        elif status == "unmatched" or not hc_name:
            tipo = "Posible alta"
            sem = "verde"
            totals["altas"] += 1
            totals["sin_match"] += 1
            observaciones = "Presente en nómina sin match confirmado en Headcount."
            tipo_mov = "ALTA"
            if wid not in first_attendance_by_worker:
                observaciones += " Sin asistencia A en el periodo; fecha pendiente de revisión manual."
        else:
            tipo = "Revisión"
            sem = "amarillo"
            totals["revisiones"] += 1
            observaciones = "Conflicto de cliente/planta o match ambiguo."
            tipo_mov = ""

        repo.insert_result(
            conn,
            comparative_id,
            {
                "worker_id": wid,
                "resultado": tipo,
                "semaforo": sem,
                "tipo_sugerido": tipo_mov,
                "fecha_sugerida": first_attendance_by_worker.get(wid, "") if tipo_mov == "ALTA" else "",
                "decision_final": tipo,
                "observaciones": observaciones,
            },
        )

    period_fin = str(period["fecha_fin"]) if period else ""
    unavailable_keys: set[str] = set()
    for match in matches.values():
        if str(match.get("status") or "") in {
            "auto",
            "confirmed",
            "manual",
            "suggested",
            "review",
        }:
            unavailable_keys.update(match_headcount_keys(match))

    active_free: dict[str, dict[str, Any]] = {}
    for row in hc_rows:
        if normalize_upper(row.get("cliente")) != cliente_norm:
            continue
        if _operation_state(row) != "active":
            continue
        key = _hc_key(row)
        if key in unavailable_keys:
            continue
        active_free.setdefault(key, row)

    for row in sorted(
        active_free.values(), key=lambda item: normalize_name(item.get("nombre_completo"))
    ):
        nombre = normalize_name(row.get("nombre_completo"))
        if not nombre:
            continue
        totals["bajas"] += 1
        repo.insert_result(
            conn,
            comparative_id,
            {
                "worker_id": None,
                "headcount_only": True,
                "hc_nombre": nombre,
                "resultado": "Posible baja",
                "semaforo": "rojo",
                "tipo_sugerido": "BAJA",
                "fecha_sugerida": period_fin,
                "decision_final": "Posible baja",
                "observaciones": "Activo en Headcount, ausente en nómina.",
            },
        )

    conn.commit()
    return {
        "comparative_id": comparative_id,
        "warnings": warnings,
        "totals": totals,
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, int]:
    out = {
        "nomina": 0,
        "coincidencias": 0,
        "altas": 0,
        "bajas": 0,
        "reingresos": 0,
        "revisiones": 0,
        "sin_match": 0,
    }
    for row in results:
        res = str(row.get("resultado") or "")
        if res == "Coincidencia":
            out["coincidencias"] += 1
            out["nomina"] += 1
        elif res == "Posible alta":
            out["altas"] += 1
        elif res == "Posible baja":
            out["bajas"] += 1
        elif res == "Reingreso":
            out["reingresos"] += 1
            out["nomina"] += 1
        elif res == "Revisión":
            out["revisiones"] += 1
            if str(row.get("match_status") or row.get("status") or "") == "unmatched":
                out["sin_match"] += 1
            out["nomina"] += 1 if row.get("worker_id") else 0
    return out
