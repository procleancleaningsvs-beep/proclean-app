"""Drift checks between Banorte draft origin and current SQLite state."""

from __future__ import annotations

from typing import Any

from modules.nomina.banorte.calculo_adapter import origin_hash_for_run
from modules.nomina.banorte.calculo_queries import get_calculo_run_readonly, list_calculo_rows_readonly


class DriftError(Exception):
    def __init__(self, code: str, detail: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail or {}


def check_calculo_origin_drift(db_path: str, draft: dict[str, Any]) -> None:
    if draft.get("origin_kind") != "CALCULO_RUN":
        return
    cid = draft.get("calculo_id")
    if cid is None:
        raise DriftError("calculo_missing_on_draft")
    run = get_calculo_run_readonly(db_path, int(cid))
    if run is None:
        raise DriftError("calculo_not_found")
    if str(run.get("updated_at") or "") != str(draft.get("origin_updated_at") or ""):
        raise DriftError("calculo_updated_at_changed")
    rows = list_calculo_rows_readonly(db_path, int(cid))
    oh = origin_hash_for_run(run, rows)
    if oh != str(draft.get("origin_hash") or ""):
        raise DriftError("calculo_origin_hash_changed")


def check_beneficiary_snapshots(conn, draft_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocked: list[dict[str, Any]] = []
    for r in draft_rows:
        if int(r.get("included") or 0) != 1:
            continue
        bid = r.get("beneficiary_id")
        if bid is None:
            blocked.append({"position": r.get("position"), "reason": "beneficiary_missing"})
            continue
        ben = conn.execute(
            "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
            (int(bid),),
        ).fetchone()
        if ben is None:
            blocked.append({"position": r.get("position"), "reason": "beneficiary_missing"})
            continue
        if ben["record_status"] != "ACTIVO":
            blocked.append(
                {
                    "position": r.get("position"),
                    "reason": "beneficiary_not_active",
                    "record_status": ben["record_status"],
                }
            )
            continue
        snap_acct = r.get("account_number_snapshot")
        if snap_acct is not None and str(snap_acct) != str(ben["account_number"]):
            blocked.append({"position": r.get("position"), "reason": "account_changed_since_preview"})
            continue
        snap_emp = r.get("employee_number_snapshot")
        if snap_emp is not None and str(snap_emp) != str(ben["employee_number_effective"]):
            blocked.append({"position": r.get("position"), "reason": "employee_changed_since_preview"})
            continue
        # manual_effective requires explicit confirmation before export
        if int(ben["manual_effective_from_account"] or 0) == 1:
            ud = r.get("user_decision") or {}
            if not ud.get("confirm_manual_effective_from_account"):
                blocked.append(
                    {
                        "position": r.get("position"),
                        "reason": "manual_effective_confirmation_required",
                    }
                )
    return blocked
