from __future__ import annotations

import json
from typing import Any


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
        }
        norm = str(row.get("code_normalized") or "")
        if norm in bucket["totals"]:
            bucket["totals"][norm] += 1
    return sorted(grouped.values(), key=lambda item: str(item.get("nombre_normalizado") or ""))
