"""Métricas operativas del Master de Asistencia (solo columnas diarias dinámicas)."""

from __future__ import annotations

from typing import Any

from modules.nomina.config import VALID_DAILY_KEYS

DAILY_VALUE_FIELDS: tuple[str, ...] = tuple(f"dia_{i}_value" for i in range(1, 8))


def _norm_daily_cell(value: Any) -> str:
    s = " ".join(str(value or "").replace("\u00a0", " ").upper().split()).strip()
    return s


def compute_operative_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Cuenta ocurrencias por clave en dia_1_value..dia_7_value (no otras columnas)."""
    keys_order = [
        "A",
        "F",
        "V",
        "PSS",
        "PCS",
        "I",
        "D",
        "DL",
        "FE",
        "FL",
        "NI",
        "B",
        "R",
        "S",
        "OT",
    ]
    per_key: dict[str, int] = {k: 0 for k in keys_order}
    unknown = 0
    for row in rows:
        for field in DAILY_VALUE_FIELDS:
            code = _norm_daily_cell(row.get(field))
            if not code:
                continue
            if code in VALID_DAILY_KEYS:
                per_key[code] = per_key.get(code, 0) + 1
            else:
                unknown += 1
    permisos_total = per_key.get("PSS", 0) + per_key.get("PCS", 0)
    errors_total = sum(len(row.get("errors") or []) for row in rows)
    warnings_total = sum(len(row.get("warnings") or []) for row in rows)
    return {
        "total_registros": len(rows),
        "per_key": per_key,
        "permisos_total": permisos_total,
        "unknown_keys_total": unknown,
        "errors_total": errors_total,
        "warnings_total": warnings_total,
    }
