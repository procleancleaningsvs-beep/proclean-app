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
    """Backward-compatible wrapper; prefer evaluate_pag_export_blockers."""
    from modules.nomina.banorte.export_readiness import evaluate_pag_export_blockers

    return evaluate_pag_export_blockers(conn, draft_rows)
