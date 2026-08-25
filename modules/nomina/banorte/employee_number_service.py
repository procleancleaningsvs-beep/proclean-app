"""Available Banorte employee numbers from current operational occupancy."""

from __future__ import annotations

from typing import Any

from modules.nomina.banorte.post_catalog_authority import (
    beneficiary_created_after_snapshot,
    load_active_catalog_context,
)
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


def _normalize_effective(raw: Any) -> str | None:
    digits = digits_only(raw)
    if len(digits) != 10 or digits == "0000000000":
        return None
    return digits


def collect_occupied_employee_numbers(
    conn,
    *,
    exclude_batch_id: int | None = None,
) -> set[str]:
    occupied: set[str] = set(BANORTE_RESERVED_EMPLOYEE_NUMBERS)

    # Every row in the CURRENT ACTIVE TXT occupies, regardless of payment readiness.
    for row in conn.execute(
        """
        SELECT r.employee_number_normalized
        FROM nomina_banorte_catalog_rows r
        JOIN nomina_banorte_catalog_versions v ON v.id=r.version_id
        WHERE v.status='ACTIVE'
        """
    ):
        norm = _normalize_occupied(row["employee_number_normalized"])
        if norm:
            occupied.add(norm)

    # Operational additions after the ACTIVE snapshot occupy while their lifecycle is active.
    # validation_status intentionally does not participate in occupancy.
    ctx = load_active_catalog_context(conn)
    if ctx is not None:
        for row in conn.execute(
            """
            SELECT employee_number_effective, created_at
            FROM nomina_banorte_beneficiaries
            WHERE record_status='ACTIVO'
              AND source_kind IN ('REPORTE_DETALLADO','ALTA_MANUAL')
            """
        ):
            beneficiary = dict(row)
            if beneficiary_created_after_snapshot(beneficiary, report_date=ctx.report_date):
                norm = _normalize_effective(row["employee_number_effective"])
                if norm:
                    occupied.add(norm)

    # OPEN staging reserves only the effective identifier. In account-substituted mode,
    # the account is effective; employee_number remains requested provenance.
    sql = """
        SELECT r.employee_number, r.cuenta, r.use_account_as_employee_number
        FROM nomina_banorte_beneficiary_batch_rows r
        JOIN nomina_banorte_beneficiary_batches b ON b.id=r.batch_id
        WHERE b.status='OPEN'
    """
    params: tuple[int, ...] = ()
    if exclude_batch_id is not None:
        sql += " AND b.id<>?"
        params = (int(exclude_batch_id),)
    for row in conn.execute(sql, params):
        raw = row["cuenta"] if int(row["use_account_as_employee_number"] or 0) == 1 else row[
            "employee_number"
        ]
        norm = _normalize_effective(raw)
        if norm:
            occupied.add(norm)

    # Fail closed against the existing partial UNIQUE constraint. This is a technical
    # guard only; it does not grant business validity or payment authority.
    for row in conn.execute(
        """
        SELECT employee_number_effective
        FROM nomina_banorte_beneficiaries
        WHERE record_status='ACTIVO'
        """
    ):
        norm = _normalize_effective(row["employee_number_effective"])
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
