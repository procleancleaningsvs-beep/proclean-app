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
