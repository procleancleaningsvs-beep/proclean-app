"""Canonical export readiness for Banorte draft rows (.pag generation)."""

from __future__ import annotations

import sqlite3
from typing import Any

from modules.nomina.banorte.draft_repository import is_pag_included_row, pag_included_rows


def manual_effective_confirmed(row: dict[str, Any]) -> bool:
    ud = row.get("user_decision") or {}
    return bool(ud.get("confirm_manual_effective_from_account"))


def pag_row_export_blockers(
    row: dict[str, Any],
    beneficiary: dict[str, Any] | None,
) -> list[str]:
    """Return blocking reason codes for one pag-included row. Empty => export-ready."""
    if not is_pag_included_row(row):
        return []

    reasons: list[str] = []
    state = str(row.get("row_state") or "")
    if state != "OK":
        reasons.append("row_not_ok")
        return reasons

    if beneficiary is None:
        return ["beneficiary_missing"]

    if beneficiary.get("record_status") != "ACTIVO":
        reasons.append("beneficiary_not_active")
        return reasons

    snap_acct = row.get("account_number_snapshot")
    if snap_acct is not None and str(snap_acct) != str(beneficiary["account_number"]):
        reasons.append("account_changed_since_preview")
    snap_emp = row.get("employee_number_snapshot")
    if snap_emp is not None and str(snap_emp) != str(beneficiary["employee_number_effective"]):
        reasons.append("employee_changed_since_preview")

    if int(beneficiary.get("manual_effective_from_account") or 0) == 1:
        if not manual_effective_confirmed(row):
            reasons.append("manual_effective_confirmation_required")

    if str(row.get("row_origin") or "") == "MANUAL_ADD":
        if int(row.get("amount_final_cents") or 0) <= 0:
            reasons.append("manual_add_amount_invalid")
        if row.get("beneficiary_id") is None:
            reasons.append("manual_add_beneficiary_missing")

    return reasons


def evaluate_pag_export_blockers(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Authoritative pre-export validation for pag-included rows only."""
    blocked: list[dict[str, Any]] = []
    for row in pag_included_rows(rows):
        bid = row.get("beneficiary_id")
        beneficiary = None
        if bid is not None:
            ben_row = conn.execute(
                "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
                (int(bid),),
            ).fetchone()
            if ben_row is not None:
                beneficiary = dict(ben_row)
        for reason in pag_row_export_blockers(row, beneficiary):
            item: dict[str, Any] = {"position": row.get("position"), "reason": reason}
            if reason == "beneficiary_not_active" and beneficiary is not None:
                item["record_status"] = beneficiary.get("record_status")
            blocked.append(item)
    return blocked
