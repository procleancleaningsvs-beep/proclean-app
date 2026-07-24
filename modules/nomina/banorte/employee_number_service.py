"""Available Banorte employee numbers (gaps + historical occupied set)."""

from __future__ import annotations

from typing import Any

from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.banorte.validators import digits_only

# Permanently excluded from suggestions (Banorte business-reserved identifiers).
BANORTE_RESERVED_EMPLOYEE_NUMBERS: frozenset[str] = frozenset(
    {
        "0000000154",
        "0000000155",
        "0000000156",
        "0000000157",
        "0000000616",
    }
)


def _normalize_occupied(raw: Any) -> str | None:
    digits = digits_only(raw)
    if not digits or len(digits) > 10:
        return None
    n = int(digits)
    if n < 1:
        return None
    return str(n).zfill(10)


def collect_occupied_employee_numbers(conn) -> set[str]:
    occupied: set[str] = set()
    for row in conn.execute(
        """
        SELECT employee_number_requested, employee_number_effective
        FROM nomina_banorte_beneficiaries
        """
    ):
        for key in ("employee_number_requested", "employee_number_effective"):
            norm = _normalize_occupied(row[key])
            if norm:
                occupied.add(norm)
    for row in conn.execute(
        """
        SELECT employee_number_effective
        FROM nomina_banorte_export_items
        """
    ):
        norm = _normalize_occupied(row["employee_number_effective"])
        if norm:
            occupied.add(norm)
    return occupied


def list_available_employee_numbers(
    db_path: str,
    *,
    limit: int = 20,
    after: str | None = None,
) -> dict[str, Any]:
    lim = min(50, max(1, int(limit)))
    after_n = 0
    if after:
        norm_after = _normalize_occupied(after)
        if norm_after:
            after_n = int(norm_after)
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        occupied = collect_occupied_employee_numbers(conn)
        occupied.update(BANORTE_RESERVED_EMPLOYEE_NUMBERS)
        occupied_ints = sorted(int(x) for x in occupied)
        numbers: list[str] = []
        cursor = after_n + 1 if after_n else 1
        max_scan = (occupied_ints[-1] + lim + 1) if occupied_ints else (lim + 1)
        # Fill internal gaps then continue past max
        while len(numbers) < lim and cursor <= max_scan:
            candidate = str(cursor).zfill(10)
            if candidate not in occupied and cursor >= 1:
                if cursor > after_n:
                    numbers.append(candidate)
            cursor += 1
        # if still short, continue past max_scan
        while len(numbers) < lim and cursor < 10**10:
            candidate = str(cursor).zfill(10)
            if candidate not in occupied:
                numbers.append(candidate)
            cursor += 1
        has_more = False
        if cursor < 10**10:
            # peek one more
            peek = cursor
            while peek < 10**10:
                if str(peek).zfill(10) not in occupied:
                    has_more = True
                    break
                peek += 1
        return {"numbers": numbers, "has_more": has_more}
    finally:
        conn.close()
