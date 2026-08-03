from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

_MONTHS_ES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
_WEEKDAY_ES = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")

RESULT_BADGE_CLASS = {
    "Coincidencia": "coincidencia",
    "Posible alta": "alta",
    "Posible baja": "baja",
    "Revisión": "revision",
}


def parse_suggested_period(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def format_period_hint(period: dict[str, Any]) -> str:
    if not period.get("detected"):
        return "Sin detección — captura manual"
    start = period.get("fecha_inicio") or "?"
    end = period.get("fecha_fin") or "?"
    week = period.get("semana_num")
    suffix = f" (sem. {week})" if week else ""
    warning = f" — {period['cut_warning']}" if period.get("cut_warning") else ""
    return f"{start} → {end}{suffix}{warning}"


def group_attendance_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        worker_id = int(row["worker_id"])
        bucket = grouped.setdefault(
            worker_id,
            {
                "worker_id": worker_id,
                "num_empleado": row.get("num_empleado") or "",
                "nombre_normalizado": row.get("nombre_normalizado") or "",
                "days": {},
                "day_meta": {},
                "totals": {"A": 0, "F": 0, "I": 0, "V": 0, "D": 0},
            },
        )
        col = int(row["column_index"])
        code = str(row.get("code_normalized") or row.get("code_original") or "")
        bucket["days"][col] = code
        bucket["day_meta"][col] = {
            "attendance_id": int(row["id"]),
            "fecha_iso": row.get("fecha_iso") or "",
            "header_original": row.get("header_original") or "",
            "interpretation_status": row.get("interpretation_status") or "",
            "warning": row.get("warning") or "",
            "correction_count": int(row.get("correction_count") or 0),
        }
        norm = str(row.get("code_normalized") or "")
        if norm in bucket["totals"]:
            bucket["totals"][norm] += 1
    return sorted(grouped.values(), key=lambda item: str(item.get("nombre_normalizado") or ""))


def period_day_headers(fecha_inicio: str, *, count: int = 7) -> list[dict[str, Any]]:
    start = datetime.strptime(fecha_inicio, "%d/%m/%Y").date()
    headers: list[dict[str, Any]] = []
    for idx in range(count):
        day = start + timedelta(days=idx)
        headers.append(
            {
                "index": idx + 1,
                "fecha_iso": day.isoformat(),
                "label": f"{_WEEKDAY_ES[day.weekday()]} {day.day} {_MONTHS_ES[day.month - 1]}",
            }
        )
    return headers


def attendance_totals_label(totals: dict[str, int]) -> str:
    return f"A:{totals.get('A', 0)} · F:{totals.get('F', 0)} · I:{totals.get('I', 0)} · V:{totals.get('V', 0)} · D:{totals.get('D', 0)}"


def compact_trajectory_for_worker(
    worker_id: int,
    match: dict[str, Any] | None,
    trajectory_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "primera_a": "",
        "ultima_a": "",
        "posible_baja": "",
        "posible_reingreso": "",
        "fecha_sugerida": "",
        "warning": "",
        "detail_events": [],
    }
    if not trajectory_payload:
        return out
    trajectories = trajectory_payload.get("trajectories") or {}
    identity_key = ""
    if match:
        nss = str(match.get("nss") or "").strip()
        if nss:
            identity_key = f"nss:{nss}"
    if not identity_key:
        return out
    traj = trajectories.get(identity_key) or {}
    daily = traj.get("daily") or []
    for day in daily:
        if day.get("code_normalized") == "A":
            iso = str(day.get("fecha_iso") or "")
            if iso and not out["primera_a"]:
                out["primera_a"] = iso
            if iso:
                out["ultima_a"] = iso
    for event in traj.get("events") or []:
        et = str(event.get("event_type") or "")
        fecha = str(event.get("fecha_sugerida") or "")
        out["detail_events"].append(event)
        if et == "posible_baja" and not out["posible_baja"]:
            out["posible_baja"] = fecha
        if et == "posible_reingreso" and not out["posible_reingreso"]:
            out["posible_reingreso"] = fecha
        if fecha and not out["fecha_sugerida"]:
            out["fecha_sugerida"] = fecha
    warnings = traj.get("warnings") or []
    if warnings:
        out["warning"] = " · ".join(str(w) for w in warnings[:2])
    return out


def build_weekly_workspace_rows(
    *,
    workers: list[dict[str, Any]],
    results: list[dict[str, Any]],
    attendance_rows: list[dict[str, Any]],
    client_inferences: dict[int, dict[str, Any]],
    trajectory_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    attendance_by_worker = {int(row["worker_id"]): row for row in group_attendance_rows(attendance_rows)}
    results_by_worker = {int(r["worker_id"]): r for r in results if r.get("worker_id")}
    rows: list[dict[str, Any]] = []
    for worker in workers:
        wid = int(worker["id"])
        match = worker.get("match") or {}
        att = attendance_by_worker.get(wid, {})
        result = results_by_worker.get(wid, {})
        inference = client_inferences.get(wid, {})
        cliente = worker.get("cliente_confirmado") or inference.get("cliente") or worker.get("cliente_sugerido") or ""
        resultado = str(result.get("decision_final") or result.get("resultado") or "")
        match_status = match.get("status") or "unmatched"
        confirmed_match = match_status in {"auto", "confirmed", "manual"}
        display_name = (match.get("hc_nombre") or result.get("match_hc_nombre") or "") if confirmed_match else ""
        display_name = display_name or worker.get("nombre_normalizado") or ""
        name_badge = "" if confirmed_match else ("Sin match" if match_status == "unmatched" else "Revisión")
        try:
            original_values = json.loads(worker.get("row_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            original_values = {}
        rows.append(
            {
                "worker_id": wid,
                "result_id": result.get("id"),
                "num_empleado": worker.get("num_empleado") or "",
                "nombre_nomina": worker.get("nombre_normalizado") or "",
                "nombre_hc": match.get("hc_nombre") or result.get("match_hc_nombre") or "",
                "display_name": display_name,
                "name_badge": name_badge,
                "planta": worker.get("planta_normalizada") or worker.get("planta_original") or "",
                "cliente": cliente,
                "cliente_source": inference.get("source") or "",
                "cliente_confidence": inference.get("confidence") or 0,
                "cliente_requires_review": inference.get("requires_review", False),
                "nss": match.get("nss") or result.get("nss") or "",
                "rfc": match.get("rfc") or result.get("rfc") or "",
                "curp": match.get("curp") or result.get("curp") or "",
                "match_status": match_status,
                "match_method": match.get("match_method") or "",
                "warning": inference.get("requires_review") and "Revisar cliente" or "",
                "days": att.get("days") or {},
                "day_meta": att.get("day_meta") or {},
                "totals_label": attendance_totals_label(att.get("totals") or {}),
                "totals": att.get("totals") or {},
                "resultado": resultado,
                "result_badge": RESULT_BADGE_CLASS.get(resultado, "revision"),
                "tipo_movimiento": result.get("tipo_sugerido") or "",
                "fecha_sugerida": result.get("fecha_sugerida") or "",
                "decision_final": resultado,
                "conversion_status": result.get("conversion_status") or "none",
                "observaciones": result.get("observaciones") or "",
                "original_values": original_values,
                "hidden": bool(result.get("hidden_at")),
                "hidden_at": result.get("hidden_at") or "",
                "hidden_by": result.get("hidden_by") or "",
                "trajectory": compact_trajectory_for_worker(wid, match, trajectory_payload),
            }
        )
    for result in results:
        if result.get("worker_id"):
            continue
        resultado = str(result.get("decision_final") or result.get("resultado") or "Posible baja")
        rows.append(
            {
                "worker_id": None,
                "result_id": result.get("id"),
                "num_empleado": "",
                "nombre_nomina": "",
                "nombre_hc": result.get("hc_nombre") or "",
                "display_name": result.get("hc_nombre") or "",
                "name_badge": "",
                "planta": "",
                "cliente": "",
                "cliente_source": "headcount_only",
                "cliente_confidence": 1.0,
                "cliente_requires_review": False,
                "nss": result.get("nss") or "",
                "match_status": "headcount_only",
                "warning": "",
                "days": {},
                "day_meta": {},
                "totals_label": "",
                "totals": {},
                "resultado": resultado,
                "result_badge": RESULT_BADGE_CLASS.get(resultado, "baja"),
                "tipo_movimiento": result.get("tipo_sugerido") or "BAJA",
                "fecha_sugerida": result.get("fecha_sugerida") or "",
                "decision_final": resultado,
                "conversion_status": result.get("conversion_status") or "none",
                "observaciones": result.get("observaciones") or "",
                "original_values": {},
                "hidden": bool(result.get("hidden_at")),
                "hidden_at": result.get("hidden_at") or "",
                "hidden_by": result.get("hidden_by") or "",
                "trajectory": {},
            }
        )
    return rows
