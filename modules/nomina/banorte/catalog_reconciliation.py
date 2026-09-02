from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from modules.nomina.banorte.beneficiary_material import beneficiary_material_fingerprint
from modules.nomina.banorte.catalog_parser import (
    catalog_name_key_v1,
    catalog_name_normalized_v1,
)
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables


class CatalogReconciliationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _open(db_path: str | Path) -> sqlite3.Connection:
    conn = connect(db_path)
    ensure_banorte_tables(conn)
    conn.commit()
    return conn


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record_event(
    conn: sqlite3.Connection,
    *,
    version_id: int,
    person_id: int,
    reconciliation_id: int | None,
    event_type: str,
    actor: str,
    reason_code: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO nomina_banorte_catalog_events (
            version_id,person_id,reconciliation_id,event_type,reason_code,
            metadata_json,actor,created_at
        ) VALUES (?,?,?,?,?,'{}',?,?)
        """,
        (
            version_id,
            person_id,
            reconciliation_id,
            event_type,
            reason_code,
            actor,
            _now(),
        ),
    )


def _usable(beneficiary: dict[str, Any]) -> bool:
    # Reuse the Release 1 operational invariant: active records may participate in
    # matching. MANUAL_PENDIENTE_VALIDACION is not discarded here; the existing
    # draft/readiness flow owns its explicit confirmation semantics.
    return beneficiary["record_status"] == "ACTIVO"


def _raw_name(value: Any) -> str:
    return str(value or "").strip().casefold()


def _name_method(catalog_name: str, beneficiary_name: str) -> str | None:
    if _raw_name(catalog_name) == _raw_name(beneficiary_name):
        return "EXACT_EMPLOYEE_ACCOUNT_RAW_NAME"
    if catalog_name_normalized_v1(catalog_name) == catalog_name_normalized_v1(beneficiary_name):
        return "EXACT_EMPLOYEE_ACCOUNT_CANONICAL_NAME"
    if catalog_name_key_v1(catalog_name) == catalog_name_key_v1(beneficiary_name):
        return "EXACT_EMPLOYEE_ACCOUNT_CONTROLLED_MA"
    return None


def _curp_birth_conflicts(curp: Any, birth_date_iso: str) -> bool:
    raw = str(curp or "").strip().upper()
    if len(raw) < 10 or not raw[4:10].isdigit():
        return False
    compact = birth_date_iso.replace("-", "")
    return len(compact) != 8 or raw[4:10] != compact[2:]


def _decision(
    person: dict[str, Any],
    row: dict[str, Any],
    beneficiaries: list[dict[str, Any]],
) -> dict[str, Any]:
    employee = str(row["employee_number_normalized"] or "")
    account = str(row["account_number_normalized"] or "")
    exact_any = [
        beneficiary
        for beneficiary in beneficiaries
        if str(beneficiary["employee_number_effective"]) == employee
        and str(beneficiary["account_number"]) == account
    ]
    exact_usable = [beneficiary for beneficiary in exact_any if _usable(beneficiary)]
    if len(exact_usable) > 1:
        return {
            "status": "MULTIPLE_CANDIDATES",
            "method": "NONE",
            "beneficiary": None,
            "candidate_count": len(exact_usable),
            "reason": "MULTIPLE_EXACT_PAIR",
        }
    if len(exact_usable) == 1:
        beneficiary = exact_usable[0]
        method = _name_method(str(row["name_original"]), str(beneficiary["nombre_original"]))
        if method is None or _curp_birth_conflicts(
            beneficiary.get("curp"), str(person["birth_date_iso"])
        ):
            return {
                "status": "IDENTITY_CONFLICT",
                "method": "NONE",
                "beneficiary": None,
                "candidate_count": 1,
                "reason": "MATERIAL_IDENTITY_CONFLICT",
            }
        return {
            "status": "AUTO_MATCHED",
            "method": method,
            "beneficiary": beneficiary,
            "candidate_count": 1,
            "reason": None,
        }
    if exact_any:
        return {
            "status": "LEGACY_NOT_USABLE",
            "method": "NONE",
            "beneficiary": None,
            "candidate_count": len(exact_any),
            "reason": "EXACT_PAIR_NOT_USABLE",
        }
    employee_candidates = [
        beneficiary
        for beneficiary in beneficiaries
        if _usable(beneficiary)
        and str(beneficiary["employee_number_effective"]) == employee
    ]
    account_candidates = [
        beneficiary
        for beneficiary in beneficiaries
        if _usable(beneficiary) and str(beneficiary["account_number"]) == account
    ]
    candidate_ids = {
        int(beneficiary["id"]) for beneficiary in employee_candidates + account_candidates
    }
    if employee_candidates and account_candidates:
        status = "MULTIPLE_CANDIDATES"
        reason = "SPLIT_IDENTIFIERS"
    elif employee_candidates:
        status = "ACCOUNT_MISMATCH"
        reason = "EMPLOYEE_ONLY"
    elif account_candidates:
        status = "EMPLOYEE_MISMATCH"
        reason = "ACCOUNT_ONLY"
    else:
        name_suggestions = [
            beneficiary
            for beneficiary in beneficiaries
            if _usable(beneficiary)
            and _name_method(str(row["name_original"]), str(beneficiary["nombre_original"]))
            is not None
        ]
        candidate_ids = {int(beneficiary["id"]) for beneficiary in name_suggestions}
        if len(name_suggestions) == 1:
            status = "IDENTITY_CONFLICT"
            reason = "IDENTIFIERS_BOTH_MISMATCH"
        elif len(name_suggestions) > 1:
            status = "MULTIPLE_CANDIDATES"
            reason = "MULTIPLE_NAME_SUGGESTIONS"
        else:
            status = "UNMATCHED"
            reason = "NO_IDENTIFIER_CANDIDATE"
    return {
        "status": status,
        "method": "NONE",
        "beneficiary": None,
        "candidate_count": len(candidate_ids),
        "reason": reason,
    }


def _supersede_current(
    conn: sqlite3.Connection,
    *,
    version_id: int,
    person_id: int,
    actor: str,
) -> int | None:
    current = conn.execute(
        """
        SELECT id FROM nomina_banorte_catalog_reconciliations
        WHERE person_id=? AND is_current=1
        """,
        (person_id,),
    ).fetchone()
    if current is None:
        return None
    reconciliation_id = int(current["id"])
    conn.execute(
        """
        UPDATE nomina_banorte_catalog_reconciliations
        SET is_current=0,superseded_by=?,superseded_at=?
        WHERE id=? AND is_current=1
        """,
        (actor, _now(), reconciliation_id),
    )
    _record_event(
        conn,
        version_id=version_id,
        person_id=person_id,
        reconciliation_id=reconciliation_id,
        event_type="RECONCILIATION_SUPERSEDED",
        actor=actor,
    )
    return reconciliation_id


def _insert_reconciliation(
    conn: sqlite3.Connection,
    *,
    version_id: int,
    person_id: int,
    beneficiary: dict[str, Any] | None,
    status: str,
    method: str,
    candidate_count: int,
    reason_code: str | None,
    actor: str,
    supersedes_id: int | None,
    manual_reason: str | None = None,
    lineage_status: str | None = None,
    lineage_predecessor_person_id: int | None = None,
    lineage_predecessor_beneficiary_id: int | None = None,
    lineage_evidence_json: str | None = None,
    lineage_evidence_sha256: str | None = None,
) -> int:
    fingerprint = beneficiary_material_fingerprint(beneficiary) if beneficiary else None
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_catalog_reconciliations (
            version_id,person_id,beneficiary_id,reconciliation_status,match_method,
            candidate_count,reason_code,beneficiary_material_fingerprint_version,
            beneficiary_material_state_json,beneficiary_material_fingerprint,
            beneficiary_updated_at_seen,is_current,supersedes_reconciliation_id,
            manual_reason,created_by,created_at,lineage_status,
            lineage_predecessor_person_id,lineage_predecessor_beneficiary_id,
            lineage_evidence_json,lineage_evidence_sha256
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?)
        """,
        (
            version_id,
            person_id,
            int(beneficiary["id"]) if beneficiary else None,
            status,
            method,
            candidate_count,
            reason_code,
            fingerprint.version if fingerprint else None,
            fingerprint.state_json if fingerprint else None,
            fingerprint.sha256 if fingerprint else None,
            beneficiary.get("updated_at") if beneficiary else None,
            supersedes_id,
            manual_reason,
            actor,
            _now(),
            lineage_status,
            lineage_predecessor_person_id,
            lineage_predecessor_beneficiary_id,
            lineage_evidence_json,
            lineage_evidence_sha256,
        ),
    )
    reconciliation_id = int(cur.lastrowid)
    _record_event(
        conn,
        version_id=version_id,
        person_id=person_id,
        reconciliation_id=reconciliation_id,
        event_type="RECONCILIATION_CREATED",
        actor=actor,
        reason_code=reason_code,
    )
    return reconciliation_id


def pre_reconcile_catalog_version(
    db_path: str | Path,
    version_id: int,
    *,
    actor: str,
) -> dict[str, Any]:
    conn = _open(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        version = conn.execute(
            "SELECT status FROM nomina_banorte_catalog_versions WHERE id=?", (int(version_id),)
        ).fetchone()
        if version is None:
            raise CatalogReconciliationError("version_not_found")
        if version["status"] not in {"ANALYZED", "READY_FOR_REVIEW"}:
            raise CatalogReconciliationError("version_not_analyzed")
        persons = [
            dict(row)
            for row in conn.execute(
                """
                SELECT p.*,r.employee_number_normalized,r.account_number_normalized,
                       r.name_original
                FROM nomina_banorte_catalog_persons p
                JOIN nomina_banorte_catalog_rows r ON r.id=p.current_row_id
                WHERE p.version_id=? AND p.person_status='CATALOG_READY'
                ORDER BY p.id
                """,
                (int(version_id),),
            )
        ]
        beneficiaries = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM nomina_banorte_beneficiaries ORDER BY id"
            )
        ]
        by_status: Counter[str] = Counter()
        by_method: Counter[str] = Counter()
        for person in persons:
            decision = _decision(person, person, beneficiaries)
            supersedes_id = _supersede_current(
                conn,
                version_id=int(version_id),
                person_id=int(person["id"]),
                actor=actor,
            )
            _insert_reconciliation(
                conn,
                version_id=int(version_id),
                person_id=int(person["id"]),
                beneficiary=decision["beneficiary"],
                status=decision["status"],
                method=decision["method"],
                candidate_count=int(decision["candidate_count"]),
                reason_code=decision["reason"],
                actor=actor,
                supersedes_id=supersedes_id,
            )
            by_status[decision["status"]] += 1
            by_method[decision["method"]] += 1
        conn.commit()
        return {
            "version_id": int(version_id),
            "total": len(persons),
            "by_status": dict(sorted(by_status.items())),
            "by_method": dict(sorted(by_method.items())),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def manual_reconcile_catalog_person(
    db_path: str | Path,
    person_id: int,
    beneficiary_id: int,
    *,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    manual_reason = str(reason or "").strip()
    if not manual_reason:
        raise CatalogReconciliationError("manual_reason_required")
    conn = _open(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        person_row = conn.execute(
            """
            SELECT p.*,r.employee_number_normalized,r.account_number_normalized,r.name_original
            FROM nomina_banorte_catalog_persons p
            LEFT JOIN nomina_banorte_catalog_rows r ON r.id=p.current_row_id
            WHERE p.id=?
            """,
            (int(person_id),),
        ).fetchone()
        beneficiary_row = conn.execute(
            "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?", (int(beneficiary_id),)
        ).fetchone()
        if person_row is None or person_row["person_status"] != "CATALOG_READY":
            raise CatalogReconciliationError("person_not_catalog_ready")
        if beneficiary_row is None:
            raise CatalogReconciliationError("beneficiary_not_found")
        person = dict(person_row)
        beneficiary = dict(beneficiary_row)
        if not _usable(beneficiary):
            raise CatalogReconciliationError("beneficiary_not_usable")
        if str(person["employee_number_normalized"] or "") != str(
            beneficiary["employee_number_effective"]
        ):
            raise CatalogReconciliationError("employee_incompatible")
        if str(person["account_number_normalized"] or "") != str(beneficiary["account_number"]):
            raise CatalogReconciliationError("account_incompatible")
        if _name_method(str(person["name_original"]), str(beneficiary["nombre_original"])) is None:
            raise CatalogReconciliationError("name_incompatible")
        if _curp_birth_conflicts(beneficiary.get("curp"), str(person["birth_date_iso"])):
            raise CatalogReconciliationError("identity_incompatible")
        version_id = int(person["version_id"])
        supersedes_id = _supersede_current(
            conn,
            version_id=version_id,
            person_id=int(person_id),
            actor=actor,
        )
        reconciliation_id = _insert_reconciliation(
            conn,
            version_id=version_id,
            person_id=int(person_id),
            beneficiary=beneficiary,
            status="MANUAL_MATCHED",
            method="MANUAL_SELECTION",
            candidate_count=1,
            reason_code="ADMIN_CONFIRMED",
            actor=actor,
            supersedes_id=supersedes_id,
            manual_reason=manual_reason,
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM nomina_banorte_catalog_reconciliations WHERE id=?",
            (reconciliation_id,),
        ).fetchone()
        return dict(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def manual_confirm_catalog_lineage(
    db_path: str | Path,
    person_id: int,
    predecessor_beneficiary_id: int,
    *,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    """Persist a C2 manual continuity decision against PRIOR CURRENT only."""
    from modules.nomina.banorte.catalog_application_plan import build_new_baseline_plan
    from modules.nomina.banorte.catalog_lineage_service import manual_confirmed_lineage

    manual_reason = str(reason or "").strip()
    if not manual_reason:
        raise CatalogReconciliationError("manual_reason_required")
    conn = _open(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        person = conn.execute(
            """
            SELECT p.id,p.version_id,p.person_status,r.row_content_sha256
            FROM nomina_banorte_catalog_persons p
            LEFT JOIN nomina_banorte_catalog_rows r ON r.id=p.current_row_id
            WHERE p.id=?
            """,
            (int(person_id),),
        ).fetchone()
        if person is None or person["person_status"] != "CATALOG_READY":
            raise CatalogReconciliationError("person_not_catalog_ready")
        plan = build_new_baseline_plan(conn, int(person["version_id"]))
        candidate = next(
            (
                item
                for item in plan.prior_candidates
                if item.beneficiary_id == int(predecessor_beneficiary_id)
            ),
            None,
        )
        if candidate is None:
            raise CatalogReconciliationError("predecessor_not_prior_current")
        for blocker in plan.operational_blockers:
            if blocker.get("person_id") == int(person_id):
                raise CatalogReconciliationError(str(blocker.get("code") or "manual_collision"))
        if any(
            action.person_id != int(person_id)
            and action.lineage_status == "CONFIRMED"
            and action.predecessor_beneficiary_id == int(predecessor_beneficiary_id)
            for action in plan.actions
        ):
            raise CatalogReconciliationError("predecessor_already_used")
        decision = manual_confirmed_lineage(
            target_version_id=int(person["version_id"]),
            target_person_id=int(person_id),
            target_row_hash=str(person["row_content_sha256"]),
            candidate=candidate,
        )
        beneficiary_row = conn.execute(
            "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
            (int(predecessor_beneficiary_id),),
        ).fetchone()
        if beneficiary_row is None:
            raise CatalogReconciliationError("predecessor_not_prior_current")
        supersedes_id = _supersede_current(
            conn,
            version_id=int(person["version_id"]),
            person_id=int(person_id),
            actor=actor,
        )
        reconciliation_id = _insert_reconciliation(
            conn,
            version_id=int(person["version_id"]),
            person_id=int(person_id),
            beneficiary=dict(beneficiary_row),
            status="UNMATCHED",
            method=decision.method,
            candidate_count=1,
            reason_code="MANUAL_CONTINUITY_CONFIRMED",
            actor=actor,
            supersedes_id=supersedes_id,
            manual_reason=manual_reason,
            lineage_status=decision.status,
            lineage_predecessor_person_id=decision.predecessor_person_id,
            lineage_predecessor_beneficiary_id=decision.predecessor_beneficiary_id,
            lineage_evidence_json=decision.evidence_json,
            lineage_evidence_sha256=decision.evidence_sha256,
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM nomina_banorte_catalog_reconciliations WHERE id=?",
            (reconciliation_id,),
        ).fetchone()
        return dict(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def refresh_stale_reconciliations(
    db_path: str | Path,
    version_id: int,
    *,
    actor: str,
) -> int:
    conn = _open(db_path)
    stale_count = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        reconciliations = [
            dict(row)
            for row in conn.execute(
                """
                SELECT r.*,b.*,
                       r.id AS reconciliation_id,r.version_id AS reconciliation_version_id,
                       r.person_id AS reconciliation_person_id,
                       r.match_method AS reconciliation_match_method
                FROM nomina_banorte_catalog_reconciliations r
                JOIN nomina_banorte_beneficiaries b ON b.id=r.beneficiary_id
                WHERE r.version_id=? AND r.is_current=1
                  AND r.reconciliation_status IN ('AUTO_MATCHED','MANUAL_MATCHED','CATALOG_BOUND')
                ORDER BY r.id
                """,
                (int(version_id),),
            )
        ]
        for joined in reconciliations:
            fingerprint = beneficiary_material_fingerprint(joined)
            if fingerprint.sha256 == joined["beneficiary_material_fingerprint"]:
                continue
            old_id = int(joined["reconciliation_id"])
            person_id = int(joined["reconciliation_person_id"])
            conn.execute(
                """
                UPDATE nomina_banorte_catalog_reconciliations
                SET is_current=0,superseded_by=?,superseded_at=? WHERE id=?
                """,
                (actor, _now(), old_id),
            )
            new_id = _insert_reconciliation(
                conn,
                version_id=int(version_id),
                person_id=person_id,
                beneficiary=joined,
                status="STALE_RECONCILIATION",
                method=str(joined["reconciliation_match_method"]),
                candidate_count=int(joined["candidate_count"]),
                reason_code="BENEFICIARY_MATERIAL_CHANGED",
                actor=actor,
                supersedes_id=old_id,
            )
            _record_event(
                conn,
                version_id=int(version_id),
                person_id=person_id,
                reconciliation_id=new_id,
                event_type="STALE_DETECTED",
                actor=actor,
                reason_code="BENEFICIARY_MATERIAL_CHANGED",
            )
            stale_count += 1
        conn.commit()
        return stale_count
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
