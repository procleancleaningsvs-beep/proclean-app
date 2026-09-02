"""Single C2 application plan used by preview and activation."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from modules.nomina.banorte.beneficiary_material import beneficiary_material_fingerprint
from modules.nomina.banorte.catalog_lineage_service import (
    LineageDecision,
    PriorCurrentCandidate,
    decide_automatic_lineage,
    unconfirmed_lineage,
)
from modules.nomina.banorte.catalog_parser import catalog_name_key_v1, catalog_name_normalized_v1
from modules.nomina.banorte.post_catalog_authority import (
    ActiveCatalogContext,
    beneficiary_created_after_snapshot,
    evaluate_post_catalog_addition,
)
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.validators import is_valid_account_number, is_valid_employee_number


PLAN_VERSION = "NEW_BASELINE_PLAN_V1"
_OPERATIONAL_CONFLICT_CODES = frozenset(
    {
        "PROJECTION_BLOCKERS",
        "PROJECTION_COUNT_MISMATCH",
        "TARGET_CURRENT_ROW_INVALID",
        "TARGET_EMPLOYEE_INVALID",
        "TARGET_ACCOUNT_INVALID",
        "TARGET_EMPLOYEE_DUPLICATE",
        "TARGET_ACCOUNT_DUPLICATE",
        "SPLIT_PRIOR_CURRENT_IDENTIFIERS",
        "PREDECESSOR_REUSED",
        "ACTIVE_NON_CURRENT_IDENTIFIER_COLLISION",
    }
)


@dataclass(frozen=True)
class TargetApplication:
    person_id: int
    row_id: int
    row_sha256: str
    employee: str
    account: str
    name_original: str
    rfc: str
    birth_date: str
    lineage_status: str
    match_method: str
    predecessor_person_id: int | None
    predecessor_beneficiary_id: int | None
    lineage_evidence_json: str | None
    lineage_evidence_sha256: str | None
    candidate_count: int
    reuse_beneficiary_id: int | None
    identifier_change: str
    predecessor_authority_kind: str | None
    manual_reason: str | None


@dataclass(frozen=True)
class NewBaselinePlan:
    target_version_id: int
    target_file_sha256: str
    base_active_version_id: int | None
    base_active_file_sha256: str | None
    target_report_date: str
    actions: tuple[TargetApplication, ...]
    prior_candidates: tuple[PriorCurrentCandidate, ...]
    post_addition_ids: tuple[int, ...]
    post_additions_absorbed: tuple[int, ...]
    post_additions_dropped: tuple[int, ...]
    post_additions_remaining: tuple[int, ...]
    operational_blockers: tuple[dict[str, Any], ...]
    stale_evidence: tuple[dict[str, Any], ...]
    manual_resolution_conflicts: tuple[dict[str, Any], ...]
    incompatible_open_operations: tuple[int, ...]
    preview_fingerprint: str

    @property
    def can_apply(self) -> bool:
        return not self.operational_blockers

    def preview(self) -> dict[str, Any]:
        lineage = Counter(action.lineage_status for action in self.actions)
        methods = Counter(action.match_method for action in self.actions)
        changes = Counter(action.identifier_change for action in self.actions)
        prior_catalog_ids = {
            candidate.beneficiary_id
            for candidate in self.prior_candidates
            if candidate.authority_kind == "CATALOG"
        }
        reused = {
            int(action.reuse_beneficiary_id)
            for action in self.actions
            if action.reuse_beneficiary_id is not None
        }
        leaving = prior_catalog_ids - reused
        entering = sum(1 for action in self.actions if action.reuse_beneficiary_id is None)
        operational_conflicts = sum(
            1
            for blocker in self.operational_blockers
            if blocker.get("code") in _OPERATIONAL_CONFLICT_CODES
        )
        return {
            "plan_version": PLAN_VERSION,
            "target_version_id": self.target_version_id,
            "target_file_sha256": self.target_file_sha256,
            "base_active_version_id": self.base_active_version_id,
            "base_active_file_sha256": self.base_active_file_sha256,
            "final_catalog_people": len(self.actions),
            "final_post_snapshot_additions": len(self.post_additions_remaining),
            "final_current_total": len(self.actions) + len(self.post_additions_remaining),
            "catalog_bound_planned": len(self.actions),
            "projection_valid": not any(
                blocker.get("code")
                in {"PROJECTION_BLOCKERS", "PROJECTION_COUNT_MISMATCH", "TARGET_CURRENT_ROW_INVALID"}
                for blocker in self.operational_blockers
            ),
            "entering": entering,
            "leaving": len(leaving) + len(self.post_additions_dropped),
            "unchanged": len(reused),
            "account_changes": changes["ACCOUNT"],
            "employee_changes": changes["EMPLOYEE"],
            "both_identifiers_changed": changes["BOTH"],
            "post_additions_absorbed": len(self.post_additions_absorbed),
            "post_additions_dropped": len(self.post_additions_dropped),
            "post_additions_remaining": len(self.post_additions_remaining),
            "lineage_confirmed_automatic": (
                lineage["CONFIRMED"] - methods["MANUAL_CONTINUITY_CONFIRMED"]
            ),
            "lineage_confirmed_manual": methods["MANUAL_CONTINUITY_CONFIRMED"],
            "lineage_unconfirmed_count": lineage["UNCONFIRMED"],
            "operational_conflict_count": operational_conflicts,
            "stale_evidence_count": len(self.stale_evidence),
            "stale_evidence": list(self.stale_evidence),
            "manual_resolution_conflicts": list(self.manual_resolution_conflicts),
            "incompatible_open_operations": list(self.incompatible_open_operations),
            "operational_blockers": list(self.operational_blockers),
            "base_changed": False,
            "preview_valid": not self.operational_blockers,
            "preview_fingerprint": self.preview_fingerprint,
            "can_apply": self.can_apply,
            "actions": [asdict(action) for action in self.actions],
        }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _block(code: str, **metadata: Any) -> dict[str, Any]:
    return {"code": code, **metadata}


def _load_target(conn: sqlite3.Connection, version_id: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    version_row = conn.execute(
        "SELECT * FROM nomina_banorte_catalog_versions WHERE id=?", (int(version_id),)
    ).fetchone()
    if version_row is None:
        raise ValueError("version_not_found")
    people = [
        dict(row)
        for row in conn.execute(
            """
            SELECT p.id AS person_id,p.person_status,p.rfc_normalized,p.birth_date_iso,
                   p.name_normalized,p.name_controlled_key,p.current_row_id,
                   r.id AS row_id,r.row_content_sha256,r.row_business_status,r.eligibility,
                   r.employee_number_normalized,r.account_number_normalized,r.name_original
            FROM nomina_banorte_catalog_persons p
            LEFT JOIN nomina_banorte_catalog_rows r ON r.id=p.current_row_id
            WHERE p.version_id=?
            ORDER BY p.id
            """,
            (int(version_id),),
        )
    ]
    return dict(version_row), people


def _active_version(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM nomina_banorte_catalog_versions WHERE status='ACTIVE' LIMIT 1"
    ).fetchone()
    return dict(row) if row is not None else None


def _load_prior_catalog_candidates(
    conn: sqlite3.Connection, base: dict[str, Any]
) -> tuple[list[PriorCurrentCandidate], list[dict[str, Any]]]:
    candidates: list[PriorCurrentCandidate] = []
    stale: list[dict[str, Any]] = []
    rows = conn.execute(
        """
        SELECT p.id AS prior_person_id,p.rfc_normalized,p.birth_date_iso,
               pr.name_original,pr.name_normalized,pr.name_controlled_key,
               rec.id AS reconciliation_id,rec.beneficiary_material_fingerprint AS seen_fp,
               rec.beneficiary_material_fingerprint_version AS seen_fp_version,
               b.*
        FROM nomina_banorte_catalog_persons p
        JOIN nomina_banorte_catalog_rows pr ON pr.id=p.current_row_id
        JOIN nomina_banorte_catalog_reconciliations rec
          ON rec.person_id=p.id AND rec.version_id=p.version_id AND rec.is_current=1
        JOIN nomina_banorte_beneficiaries b ON b.id=rec.beneficiary_id
        WHERE p.version_id=? AND p.person_status='CATALOG_READY'
          AND rec.reconciliation_status IN ('AUTO_MATCHED','MANUAL_MATCHED','CATALOG_BOUND')
        ORDER BY p.id
        """,
        (int(base["id"]),),
    ).fetchall()
    for joined in rows:
        row = dict(joined)
        live = beneficiary_material_fingerprint(row)
        valid = (
            row["record_status"] == "ACTIVO"
            and row["validation_status"] == "IMPORTADO_EXITOSO"
            and int(row.get("manual_effective_from_account") or 0) == 0
            and str(row.get("seen_fp") or "") == live.sha256
            and str(row.get("seen_fp_version") or "") == live.version
        )
        if not valid:
            stale.append(
                {
                    "authority_kind": "CATALOG",
                    "person_id": int(row["prior_person_id"]),
                    "beneficiary_id": int(row["id"]),
                    "reconciliation_id": int(row["reconciliation_id"]),
                    "reason": "PRIOR_AUTHORITY_MATERIAL_STALE",
                }
            )
            continue
        candidates.append(
            PriorCurrentCandidate(
                authority_kind="CATALOG",
                authority_version_id=int(base["id"]),
                person_id=int(row["prior_person_id"]),
                beneficiary_id=int(row["id"]),
                employee=str(row["employee_number_effective"]),
                account=str(row["account_number"]),
                name_original=str(row["name_original"]),
                name_normalized=str(row["name_normalized"]),
                name_controlled_key=str(row["name_controlled_key"]),
                beneficiary_name_normalized=str(row["nombre_normalizado"]),
                beneficiary_employee_requested=(
                    str(row.get("employee_number_requested"))
                    if row.get("employee_number_requested") is not None
                    else None
                ),
                beneficiary_substituted=int(row.get("banorte_employee_substituted") or 0),
                beneficiary_manual_effective=int(row.get("manual_effective_from_account") or 0),
                rfc=str(row["rfc_normalized"]),
                birth_date=str(row["birth_date_iso"]),
                material_fingerprint_version=live.version,
                material_fingerprint=live.sha256,
            )
        )
    return candidates, stale


def _load_post_additions(
    conn: sqlite3.Connection, base: dict[str, Any]
) -> list[PriorCurrentCandidate]:
    ctx = ActiveCatalogContext(int(base["id"]), str(base["activated_at"]), str(base["report_date"]))
    out: list[PriorCurrentCandidate] = []
    for raw in conn.execute(
        "SELECT * FROM nomina_banorte_beneficiaries WHERE record_status='ACTIVO' ORDER BY id"
    ):
        beneficiary = dict(raw)
        authority = evaluate_post_catalog_addition(conn, beneficiary, ctx=ctx)
        if not authority.get("payment_enabled"):
            continue
        live = beneficiary_material_fingerprint(beneficiary)
        name = str(beneficiary["nombre_original"])
        out.append(
            PriorCurrentCandidate(
                authority_kind="POST_CATALOG_ADDITION",
                authority_version_id=int(base["id"]),
                person_id=None,
                beneficiary_id=int(beneficiary["id"]),
                employee=str(beneficiary["employee_number_effective"]),
                account=str(beneficiary["account_number"]),
                name_original=name,
                name_normalized=catalog_name_normalized_v1(name),
                name_controlled_key=catalog_name_key_v1(name),
                beneficiary_name_normalized=str(beneficiary["nombre_normalizado"]),
                beneficiary_employee_requested=(
                    str(beneficiary.get("employee_number_requested"))
                    if beneficiary.get("employee_number_requested") is not None
                    else None
                ),
                beneficiary_substituted=int(beneficiary.get("banorte_employee_substituted") or 0),
                beneficiary_manual_effective=int(beneficiary.get("manual_effective_from_account") or 0),
                rfc=None,
                birth_date=None,
                material_fingerprint_version=live.version,
                material_fingerprint=live.sha256,
            )
        )
    return out


def _manual_decision(
    conn: sqlite3.Connection,
    *,
    person_id: int,
    candidates_by_beneficiary: dict[int, PriorCurrentCandidate],
) -> tuple[LineageDecision | None, dict[str, Any] | None, dict[str, Any] | None]:
    row = conn.execute(
        """
        SELECT * FROM nomina_banorte_catalog_reconciliations
        WHERE person_id=? AND is_current=1
          AND (match_method='MANUAL_CONTINUITY_CONFIRMED'
               OR reason_code='MANUAL_DISTINCT_CONFIRMED')
        LIMIT 1
        """,
        (int(person_id),),
    ).fetchone()
    if row is None:
        return None, None, None
    decision = dict(row)
    fingerprint_input = {
        key: decision.get(key)
        for key in (
            "id", "person_id", "beneficiary_id", "match_method", "reason_code",
            "manual_reason", "lineage_status", "lineage_predecessor_person_id",
            "lineage_predecessor_beneficiary_id", "lineage_evidence_sha256",
        )
    }
    if not str(decision.get("manual_reason") or "").strip():
        return None, _block("MANUAL_REASON_REQUIRED", person_id=int(person_id)), fingerprint_input
    if decision.get("reason_code") == "MANUAL_DISTINCT_CONFIRMED":
        return unconfirmed_lineage(), None, fingerprint_input
    predecessor_id = decision.get("lineage_predecessor_beneficiary_id")
    candidate = candidates_by_beneficiary.get(int(predecessor_id or 0))
    evidence_json = str(decision.get("lineage_evidence_json") or "")
    evidence_sha = str(decision.get("lineage_evidence_sha256") or "")
    if (
        candidate is None
        or not evidence_json
        or hashlib.sha256(evidence_json.encode("utf-8")).hexdigest() != evidence_sha
        or str(decision.get("beneficiary_material_fingerprint") or "")
        != candidate.material_fingerprint
    ):
        return None, _block("MANUAL_DECISION_STALE", person_id=int(person_id)), fingerprint_input
    return (
        LineageDecision(
            status="CONFIRMED",
            method="MANUAL_CONTINUITY_CONFIRMED",
            predecessor_person_id=candidate.person_id,
            predecessor_beneficiary_id=candidate.beneficiary_id,
            evidence_json=evidence_json,
            evidence_sha256=evidence_sha,
            matched_signals=("manual_prior_current",),
            different_signals=(),
            candidate_count=1,
        ),
        None,
        fingerprint_input,
    )


def _exact_material(target: dict[str, Any], candidate: PriorCurrentCandidate) -> bool:
    return (
        candidate.employee == str(target["employee_number_normalized"] or "")
        and candidate.account == str(target["account_number_normalized"] or "")
        and candidate.beneficiary_name_normalized
        == catalog_name_normalized_v1(str(target["name_original"]))
        and str(candidate.beneficiary_employee_requested or "")
        == str(target["employee_number_normalized"] or "")
        and candidate.beneficiary_substituted == 0
        and candidate.beneficiary_manual_effective == 0
    )


def _identifier_change(target: dict[str, Any], candidate: PriorCurrentCandidate | None) -> str:
    if candidate is None:
        return "NEW"
    employee = candidate.employee != str(target["employee_number_normalized"] or "")
    account = candidate.account != str(target["account_number_normalized"] or "")
    if employee and account:
        return "BOTH"
    if employee:
        return "EMPLOYEE"
    if account:
        return "ACCOUNT"
    return "NONE"


def _plan_fingerprint_payload(
    *,
    version: dict[str, Any],
    base: dict[str, Any] | None,
    target_people: list[dict[str, Any]],
    candidates: list[PriorCurrentCandidate],
    post_ids: list[int],
    post_absorbed: list[int],
    post_dropped: list[int],
    post_remaining: list[int],
    manual_inputs: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    open_operations: list[int],
    actions: list[TargetApplication],
) -> dict[str, Any]:
    return {
        "plan_version": PLAN_VERSION,
        "target": {
            "version_id": int(version["id"]),
            "file_sha256": str(version["file_sha256"]),
            "status": str(version["status"]),
            "projection_version": int(version["projection_version"]),
            "people": [
                {
                    "person_id": int(row["person_id"]),
                    "person_status": row["person_status"],
                    "row_id": row.get("row_id"),
                    "row_sha256": row.get("row_content_sha256"),
                    "employee": row.get("employee_number_normalized"),
                    "account": row.get("account_number_normalized"),
                    "rfc": row.get("rfc_normalized"),
                    "birth_date": row.get("birth_date_iso"),
                }
                for row in target_people
            ],
        },
        "base": None
        if base is None
        else {"version_id": int(base["id"]), "file_sha256": str(base["file_sha256"])},
        "prior_current": [
            {
                "kind": candidate.authority_kind,
                "person_id": candidate.person_id,
                "beneficiary_id": candidate.beneficiary_id,
                "fingerprint_version": candidate.material_fingerprint_version,
                "fingerprint": candidate.material_fingerprint,
            }
            for candidate in candidates
        ],
        "post_addition_ids": post_ids,
        "post_additions_absorbed": post_absorbed,
        "post_additions_dropped": post_dropped,
        "post_additions_remaining": post_remaining,
        "manual_decisions": manual_inputs,
        "operational_blockers": blockers,
        "open_operation_ids": open_operations,
        "actions": [
            {
                "person_id": action.person_id,
                "lineage_status": action.lineage_status,
                "match_method": action.match_method,
                "predecessor_person_id": action.predecessor_person_id,
                "predecessor_beneficiary_id": action.predecessor_beneficiary_id,
                "evidence_sha256": action.lineage_evidence_sha256,
                "reuse_beneficiary_id": action.reuse_beneficiary_id,
            }
            for action in actions
        ],
    }


def build_new_baseline_plan(conn: sqlite3.Connection, target_version_id: int) -> NewBaselinePlan:
    """Build the authoritative, side-effect-free C2 plan from current DB state."""
    version, target_people = _load_target(conn, int(target_version_id))
    base = _active_version(conn)
    blockers: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    manual_conflicts: list[dict[str, Any]] = []

    if version["status"] != "READY_FOR_REVIEW":
        blockers.append(_block("VERSION_NOT_READY_FOR_REVIEW"))
    ready_people = [row for row in target_people if row["person_status"] == "CATALOG_READY"]
    if len(ready_people) != len(target_people) or int(version.get("blocked_person_count") or 0):
        blockers.append(_block("PROJECTION_BLOCKERS", count=len(target_people) - len(ready_people)))
    if int(version.get("catalog_ready_count") or 0) != len(ready_people):
        blockers.append(_block("PROJECTION_COUNT_MISMATCH"))

    employees: Counter[str] = Counter()
    accounts: Counter[str] = Counter()
    target_rfcs: Counter[str] = Counter(str(row["rfc_normalized"]) for row in ready_people)
    for row in ready_people:
        employee = str(row.get("employee_number_normalized") or "")
        account = str(row.get("account_number_normalized") or "")
        employees[employee] += 1
        accounts[account] += 1
        if row.get("row_id") is None or row.get("row_business_status") != "VALID" or row.get("eligibility") != "ELIGIBLE":
            blockers.append(_block("TARGET_CURRENT_ROW_INVALID", person_id=int(row["person_id"])))
        if not is_valid_employee_number(employee):
            blockers.append(_block("TARGET_EMPLOYEE_INVALID", person_id=int(row["person_id"])))
        if not is_valid_account_number(account):
            blockers.append(_block("TARGET_ACCOUNT_INVALID", person_id=int(row["person_id"])))
    for employee, count in sorted(employees.items()):
        if count > 1:
            blockers.append(_block("TARGET_EMPLOYEE_DUPLICATE", identifier_sha256=hashlib.sha256(employee.encode()).hexdigest(), count=count))
    for account, count in sorted(accounts.items()):
        if count > 1:
            blockers.append(_block("TARGET_ACCOUNT_DUPLICATE", identifier_sha256=hashlib.sha256(account.encode()).hexdigest(), count=count))

    candidates: list[PriorCurrentCandidate] = []
    if base is not None and int(base["id"]) != int(target_version_id):
        catalog_candidates, stale = _load_prior_catalog_candidates(conn, base)
        candidates.extend(catalog_candidates)
        candidates.extend(_load_post_additions(conn, base))
    for item in stale:
        live_row = conn.execute(
            "SELECT employee_number_effective,account_number FROM nomina_banorte_beneficiaries WHERE id=?",
            (int(item["beneficiary_id"]),),
        ).fetchone()
        relevant = bool(
            live_row is not None
            and (
                str(live_row["employee_number_effective"]) in employees
                or str(live_row["account_number"]) in accounts
            )
        )
        item["relevant"] = relevant
        if relevant:
            blockers.append(_block("MATERIAL_FINGERPRINT_STALE", **item))
    candidates.sort(key=lambda item: (item.authority_kind, item.person_id or 0, item.beneficiary_id))
    by_beneficiary = {candidate.beneficiary_id: candidate for candidate in candidates}
    prior_rfcs: Counter[str] = Counter(
        str(candidate.rfc)
        for candidate in candidates
        if candidate.person_id is not None and candidate.rfc
    )

    incompatible_open = [
        int(row[0])
        for row in conn.execute(
            """
            SELECT id FROM nomina_banorte_export_drafts
            WHERE status='OPEN'
              AND (
                COALESCE(catalog_mode,'LEGACY')<>'CATALOG'
                OR catalog_version_id IS NULL
                OR catalog_version_id<>?
              )
            ORDER BY id
            """,
            (int(target_version_id),),
        )
    ]
    if incompatible_open:
        blockers.append(_block("INCOMPATIBLE_OPEN_OPERATIONS", count=len(incompatible_open)))

    candidate_ids = set(by_beneficiary)
    for row in conn.execute(
        """
        SELECT id,employee_number_effective,account_number
        FROM nomina_banorte_beneficiaries
        WHERE record_status='ACTIVO'
        ORDER BY id
        """
    ):
        beneficiary_id = int(row["id"])
        if beneficiary_id in candidate_ids:
            continue
        employee_collision = str(row["employee_number_effective"]) in employees
        account_collision = str(row["account_number"]) in accounts
        if employee_collision or account_collision:
            blockers.append(
                _block(
                    "ACTIVE_NON_CURRENT_IDENTIFIER_COLLISION",
                    beneficiary_id=beneficiary_id,
                    employee_collision=employee_collision,
                    account_collision=account_collision,
                )
            )

    actions: list[TargetApplication] = []
    manual_inputs: list[dict[str, Any]] = []
    for target in ready_people:
        employee = str(target["employee_number_normalized"] or "")
        account = str(target["account_number_normalized"] or "")
        employee_candidates = [candidate for candidate in candidates if candidate.employee == employee]
        account_candidates = [candidate for candidate in candidates if candidate.account == account]
        employee_ids = {candidate.beneficiary_id for candidate in employee_candidates}
        account_ids = {candidate.beneficiary_id for candidate in account_candidates}
        split = bool(employee_ids and account_ids and not employee_ids.intersection(account_ids))
        if split:
            blockers.append(_block("SPLIT_PRIOR_CURRENT_IDENTIFIERS", person_id=int(target["person_id"])))

        manual, manual_error, manual_input = _manual_decision(
            conn, person_id=int(target["person_id"]), candidates_by_beneficiary=by_beneficiary
        )
        if manual_input is not None:
            manual_inputs.append(manual_input)
        if manual_error is not None:
            manual_conflicts.append(manual_error)
            blockers.append(manual_error)
        decision = manual or decide_automatic_lineage(
            target_version_id=int(target_version_id),
            target_person_id=int(target["person_id"]),
            target_row_hash=str(target["row_content_sha256"]),
            target_employee=employee,
            target_account=account,
            target_name=str(target["name_original"]),
            target_rfc=str(target["rfc_normalized"]),
            target_birth_date=str(target["birth_date_iso"]),
            candidates=candidates,
            target_rfc_count=target_rfcs[str(target["rfc_normalized"])],
            prior_rfc_count=prior_rfcs[str(target["rfc_normalized"])],
        )
        predecessor = by_beneficiary.get(int(decision.predecessor_beneficiary_id or 0))
        reuse = predecessor.beneficiary_id if predecessor is not None and _exact_material(target, predecessor) else None
        actions.append(
            TargetApplication(
                person_id=int(target["person_id"]),
                row_id=int(target["row_id"]),
                row_sha256=str(target["row_content_sha256"]),
                employee=employee,
                account=account,
                name_original=str(target["name_original"]),
                rfc=str(target["rfc_normalized"]),
                birth_date=str(target["birth_date_iso"]),
                lineage_status=decision.status,
                match_method=decision.method,
                predecessor_person_id=decision.predecessor_person_id,
                predecessor_beneficiary_id=decision.predecessor_beneficiary_id,
                lineage_evidence_json=decision.evidence_json,
                lineage_evidence_sha256=decision.evidence_sha256,
                candidate_count=decision.candidate_count,
                reuse_beneficiary_id=reuse,
                identifier_change=_identifier_change(target, predecessor),
                predecessor_authority_kind=predecessor.authority_kind if predecessor else None,
                manual_reason=(
                    str(manual_input.get("manual_reason") or "")
                    if manual is not None and manual_input is not None
                    else None
                ),
            )
        )

    predecessor_counts: Counter[int] = Counter(
        int(action.predecessor_beneficiary_id)
        for action in actions
        if action.lineage_status == "CONFIRMED" and action.predecessor_beneficiary_id is not None
    )
    for predecessor_id, count in sorted(predecessor_counts.items()):
        if count > 1:
            blockers.append(_block("PREDECESSOR_REUSED", beneficiary_id=predecessor_id, count=count))

    post_candidates = [candidate for candidate in candidates if candidate.authority_kind == "POST_CATALOG_ADDITION"]
    absorbed = sorted(
        {
            int(action.predecessor_beneficiary_id)
            for action in actions
            if action.predecessor_authority_kind == "POST_CATALOG_ADDITION"
            and action.predecessor_beneficiary_id is not None
        }
    )
    remaining: list[int] = []
    dropped: list[int] = []
    if base is not None:
        for candidate in post_candidates:
            if candidate.beneficiary_id in absorbed:
                continue
            beneficiary = conn.execute(
                "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?", (candidate.beneficiary_id,)
            ).fetchone()
            if beneficiary is not None and beneficiary_created_after_snapshot(
                dict(beneficiary), report_date=str(version["report_date"])
            ):
                remaining.append(candidate.beneficiary_id)
            else:
                dropped.append(candidate.beneficiary_id)

    blockers = sorted(blockers, key=_canonical_json)
    actions.sort(key=lambda action: action.person_id)
    post_ids = sorted(candidate.beneficiary_id for candidate in post_candidates)
    payload = _plan_fingerprint_payload(
        version=version,
        base=base,
        target_people=target_people,
        candidates=candidates,
        post_ids=post_ids,
        post_absorbed=absorbed,
        post_dropped=sorted(dropped),
        post_remaining=sorted(remaining),
        manual_inputs=manual_inputs,
        blockers=blockers,
        open_operations=incompatible_open,
        actions=actions,
    )
    fingerprint = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return NewBaselinePlan(
        target_version_id=int(target_version_id),
        target_file_sha256=str(version["file_sha256"]),
        base_active_version_id=int(base["id"]) if base is not None else None,
        base_active_file_sha256=str(base["file_sha256"]) if base is not None else None,
        target_report_date=str(version["report_date"]),
        actions=tuple(actions),
        prior_candidates=tuple(candidates),
        post_addition_ids=tuple(post_ids),
        post_additions_absorbed=tuple(absorbed),
        post_additions_dropped=tuple(sorted(dropped)),
        post_additions_remaining=tuple(sorted(remaining)),
        operational_blockers=tuple(blockers),
        stale_evidence=tuple(stale),
        manual_resolution_conflicts=tuple(manual_conflicts),
        incompatible_open_operations=tuple(incompatible_open),
        preview_fingerprint=fingerprint,
    )


def catalog_apply_preview(db_path: str | Path, target_version_id: int) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        return build_new_baseline_plan(conn, int(target_version_id)).preview()
    finally:
        conn.close()


class CatalogApplicationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def apply_new_baseline_plan(
    conn: sqlite3.Connection,
    plan: NewBaselinePlan,
    *,
    actor: str,
) -> dict[str, Any]:
    """Apply a freshly revalidated plan inside the caller's open transaction."""
    from modules.nomina.banorte.catalog_legacy_sync import (
        PersonSyncAction,
        _create_catalog_mirror,
        _mark_manual_inactive,
        _mark_replaced,
    )
    from modules.nomina.banorte.catalog_reconciliation import (
        _insert_reconciliation,
        _supersede_current,
    )

    if not conn.in_transaction:
        raise CatalogApplicationError("transaction_required")
    live = build_new_baseline_plan(conn, plan.target_version_id)
    if live.preview_fingerprint != plan.preview_fingerprint:
        raise CatalogApplicationError("PREVIEW_FINGERPRINT_DRIFT")
    if not live.can_apply:
        first = live.operational_blockers[0]
        raise CatalogApplicationError(str(first.get("code") or "APPLICATION_BLOCKED"))

    keep_ids = {
        int(action.reuse_beneficiary_id)
        for action in live.actions
        if action.reuse_beneficiary_id is not None
    }
    keep_ids.update(live.post_additions_remaining)
    confirmed_replacements = {
        int(action.predecessor_beneficiary_id)
        for action in live.actions
        if action.lineage_status == "CONFIRMED"
        and action.predecessor_beneficiary_id is not None
        and action.reuse_beneficiary_id is None
    }
    for candidate in sorted(live.prior_candidates, key=lambda item: item.beneficiary_id, reverse=True):
        beneficiary_id = int(candidate.beneficiary_id)
        if beneficiary_id in keep_ids:
            continue
        if beneficiary_id in confirmed_replacements:
            _mark_replaced(
                conn,
                beneficiary_id=beneficiary_id,
                actor=actor,
                reason="C2_CONFIRMED_SUCCESSOR",
            )
        else:
            _mark_manual_inactive(
                conn,
                beneficiary_id=beneficiary_id,
                actor=actor,
                reason="C2_NEW_BASELINE_NOT_CURRENT",
            )

    bound_ids: list[int] = []
    created_ids: list[int] = []
    for action in live.actions:
        if action.reuse_beneficiary_id is not None:
            row = conn.execute(
                "SELECT * FROM nomina_banorte_beneficiaries WHERE id=? AND record_status='ACTIVO'",
                (int(action.reuse_beneficiary_id),),
            ).fetchone()
            if row is None:
                raise CatalogApplicationError("REUSE_BENEFICIARY_NOT_ACTIVE")
            beneficiary = dict(row)
        else:
            sync_action = PersonSyncAction(
                person_id=action.person_id,
                action="SUPERSEDE" if action.predecessor_beneficiary_id else "CREATE",
                employee=action.employee,
                account=action.account,
                name_original=action.name_original,
                rfc=action.rfc,
                birth_date_iso=action.birth_date,
                beneficiary_id=None,
                supersede_ids=[action.predecessor_beneficiary_id]
                if action.predecessor_beneficiary_id is not None
                else [],
                match_method=action.match_method,
                reason_code="C2_CATALOG_BOUND",
            )
            beneficiary = _create_catalog_mirror(
                conn,
                action=sync_action,
                actor=actor,
                use_catalog_rfc_as_curp=False,
                replaces_id=(
                    int(action.predecessor_beneficiary_id)
                    if action.lineage_status == "CONFIRMED"
                    and action.predecessor_beneficiary_id is not None
                    else None
                ),
            )
            created_ids.append(int(beneficiary["id"]))
        supersedes_id = _supersede_current(
            conn,
            version_id=live.target_version_id,
            person_id=action.person_id,
            actor=actor,
        )
        _insert_reconciliation(
            conn,
            version_id=live.target_version_id,
            person_id=action.person_id,
            beneficiary=beneficiary,
            status="CATALOG_BOUND",
            method=action.match_method,
            candidate_count=action.candidate_count,
            reason_code=(
                "LINEAGE_CONFIRMED" if action.lineage_status == "CONFIRMED" else "LINEAGE_UNCONFIRMED"
            ),
            actor=actor,
            supersedes_id=supersedes_id,
            manual_reason=action.manual_reason,
            lineage_status=action.lineage_status,
            lineage_predecessor_person_id=action.predecessor_person_id,
            lineage_predecessor_beneficiary_id=action.predecessor_beneficiary_id,
            lineage_evidence_json=action.lineage_evidence_json,
            lineage_evidence_sha256=action.lineage_evidence_sha256,
        )
        bound_ids.append(int(beneficiary["id"]))

    if len(set(bound_ids)) != len(live.actions):
        raise CatalogApplicationError("BOUND_BENEFICIARY_NOT_UNIQUE")
    bound_count = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM nomina_banorte_catalog_reconciliations
            WHERE version_id=? AND is_current=1 AND reconciliation_status='CATALOG_BOUND'
            """,
            (live.target_version_id,),
        ).fetchone()[0]
    )
    if bound_count != len(live.actions):
        raise CatalogApplicationError("CATALOG_BOUND_COUNT_MISMATCH")
    return {
        "preview_fingerprint": live.preview_fingerprint,
        "catalog_bound_count": bound_count,
        "created_beneficiary_ids": created_ids,
        "reused_beneficiary_ids": sorted(keep_ids.intersection(bound_ids)),
        "post_additions_absorbed": list(live.post_additions_absorbed),
        "post_additions_dropped": list(live.post_additions_dropped),
        "post_additions_remaining": list(live.post_additions_remaining),
    }
