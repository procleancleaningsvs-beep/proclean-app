"""Formato monetario estilo México: $25,450.80 (miles con coma, decimales con punto)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def format_money_mx(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        if isinstance(value, Decimal):
            n = float(value)
        else:
            n = float(value)
    except (TypeError, ValueError, InvalidOperation):
        return "—"
    if n != n:  # NaN
        return "—"
    s = f"${n:,.2f}"
    return s
