"""Authoritative catalog → operational beneficiary mirror sync for first activation."""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from modules.nomina.banorte.catalog_reconciliation import (
    _curp_birth_conflicts,
    _insert_reconciliation,
    _name_method,
    _supersede_current,
    _usable,
)
from modules.nomina.banorte.validators import (
    is_valid_account_number,
    is_valid_employee_number,
    normalize_name,
)

SYNC_REASON_KEEP = "CATALOG_SYNC_KEEP"
SYNC_REASON_CREATE = "CATALOG_SYNC_CREATE"
SYNC_REASON_SUPERSEDE = "CATALOG_SYNC_SUPERSEDE"
SYNC_REASON_EXTRA = "CATALOG_SYNC_EXTRA"
DRAFT_BLOCK_REASON = "FIRST_CATALOG_ACTIVATION"

SOURCE_KIND_CATALOG = "ALTAS_NOMINA_BANORTE"


class CatalogLegacySyncError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class PersonSyncAction:
    person_id: int
    action: str
    employee: str
    account: str
    name_original: str
    rfc: str | None
    birth_date_iso: str
    beneficiary_id: int | None = None
    supersede_ids: list[int] = field(default_factory=list)
    match_method: str = "EXACT_EMPLOYEE_ACCOUNT_RAW_NAME"
    reason_code: str = SYNC_REASON_CREATE


@dataclass
class CatalogLegacySyncPlan:
    version_id: int
    actions: list[PersonSyncAction]
    inactivate_extra_ids: list[int]
    block_draft_ids: list[int]
    aggregates: dict[str, int]
    valid: bool
    errors: list[str]


def _load_desired_persons(conn: sqlite3.Connection, version_id: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT p.id AS person_id, p.version_id, p.birth_date_iso,
                   r.employee_number_normalized, r.account_number_normalized,
                   r.name_original, r.rfc_original
            FROM nomina_banorte_catalog_persons p
            JOIN nomina_banorte_catalog_rows r ON r.id = p.current_row_id
            WHERE p.version_id=? AND p.person_status='CATALOG_READY'
            ORDER BY p.id
            """,
            (int(version_id),),
        )
    ]


def _active_beneficiaries(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM nomina_banorte_beneficiaries WHERE record_status='ACTIVO' ORDER BY id"
        )
    ]


def _conflicting_legacy_ids(
    *,
    employee: str,
    account: str,
    name_original: str,
    birth_date_iso: str,
    actives: list[dict[str, Any]],
    keep_id: int | None,
) -> list[int]:
    ids: set[int] = set()
    for beneficiary in actives:
        if not _usable(beneficiary):
            continue
        bid = int(beneficiary["id"])
        if keep_id is not None and bid == keep_id:
            continue
        matches_emp = str(beneficiary["employee_number_effective"]) == employee
        matches_acct = str(beneficiary["account_number"]) == account
        if matches_emp or matches_acct:
            ids.add(bid)
            continue
        if _name_method(name_original, str(beneficiary["nombre_original"])) is not None:
            ids.add(bid)
    return sorted(ids)


def _resolve_match_method(name_original: str, beneficiary_name: str) -> str:
    method = _name_method(name_original, beneficiary_name)
    return method or "EXACT_EMPLOYEE_ACCOUNT_RAW_NAME"


def _operational_canonical(beneficiary: dict[str, Any]) -> bool:
    """Legacy manual-validation residue must not survive KEEP under catalog authority."""
    if str(beneficiary.get("validation_status") or "") == "MANUAL_PENDIENTE_VALIDACION":
        return False
    if int(beneficiary.get("manual_effective_from_account") or 0) == 1:
        return False
    return True


def _identity_compatible_with_catalog(
    *,
    name_original: str,
    birth_date_iso: str,
    beneficiary: dict[str, Any],
) -> tuple[bool, str | None]:
    method = _name_method(name_original, str(beneficiary["nombre_original"]))
    if method is None:
        return False, None
    if _curp_birth_conflicts(beneficiary.get("curp"), birth_date_iso):
        return False, method
    return True, method


def _classify_person(
    person: dict[str, Any],
    actives: list[dict[str, Any]],
) -> PersonSyncAction:
    employee = str(person["employee_number_normalized"] or "")
    account = str(person["account_number_normalized"] or "")
    name_original = str(person["name_original"] or "")
    birth_date_iso = str(person["birth_date_iso"] or "")
    rfc = str(person.get("rfc_original") or "").strip().upper() or None
    person_id = int(person["person_id"])

    exact_usable = [
        beneficiary
        for beneficiary in actives
        if str(beneficiary["employee_number_effective"]) == employee
        and str(beneficiary["account_number"]) == account
        and _usable(beneficiary)
    ]
    if len(exact_usable) > 1:
        legacy_ids = sorted(int(b["id"]) for b in exact_usable)
        return PersonSyncAction(
            person_id=person_id,
            action="SUPERSEDE",
            employee=employee,
            account=account,
            name_original=name_original,
            rfc=rfc,
            birth_date_iso=birth_date_iso,
            supersede_ids=legacy_ids,
            match_method="EXACT_EMPLOYEE_ACCOUNT_RAW_NAME",
            reason_code=SYNC_REASON_SUPERSEDE,
        )
    if len(exact_usable) == 1:
        beneficiary = exact_usable[0]
        identity_compatible, method = _identity_compatible_with_catalog(
            name_original=name_original,
            birth_date_iso=birth_date_iso,
            beneficiary=beneficiary,
        )
        if identity_compatible and _operational_canonical(beneficiary) and method is not None:
            return PersonSyncAction(
                person_id=person_id,
                action="KEEP",
                employee=employee,
                account=account,
                name_original=name_original,
                rfc=rfc,
                birth_date_iso=birth_date_iso,
                beneficiary_id=int(beneficiary["id"]),
                match_method=method,
                reason_code=SYNC_REASON_KEEP,
            )
        legacy_ids = _conflicting_legacy_ids(
            employee=employee,
            account=account,
            name_original=name_original,
            birth_date_iso=birth_date_iso,
            actives=actives,
            keep_id=None,
        )
        if int(beneficiary["id"]) not in legacy_ids:
            legacy_ids.append(int(beneficiary["id"]))
            legacy_ids.sort()
        return PersonSyncAction(
            person_id=person_id,
            action="SUPERSEDE",
            employee=employee,
            account=account,
            name_original=name_original,
            rfc=rfc,
            birth_date_iso=birth_date_iso,
            supersede_ids=legacy_ids,
            match_method=_resolve_match_method(name_original, name_original),
            reason_code=SYNC_REASON_SUPERSEDE,
        )

    legacy_ids = _conflicting_legacy_ids(
        employee=employee,
        account=account,
        name_original=name_original,
        birth_date_iso=birth_date_iso,
        actives=actives,
        keep_id=None,
    )
    if legacy_ids:
        return PersonSyncAction(
            person_id=person_id,
            action="SUPERSEDE",
            employee=employee,
            account=account,
            name_original=name_original,
            rfc=rfc,
            birth_date_iso=birth_date_iso,
            supersede_ids=legacy_ids,
            match_method=_resolve_match_method(name_original, name_original),
            reason_code=SYNC_REASON_SUPERSEDE,
        )
    return PersonSyncAction(
        person_id=person_id,
        action="CREATE",
        employee=employee,
        account=account,
        name_original=name_original,
        rfc=rfc,
        birth_date_iso=birth_date_iso,
        match_method="EXACT_EMPLOYEE_ACCOUNT_RAW_NAME",
        reason_code=SYNC_REASON_CREATE,
    )


def _compute_extras(
    actives: list[dict[str, Any]],
    actions: list[PersonSyncAction],
) -> list[int]:
    keep_ids = {int(a.beneficiary_id) for a in actions if a.action == "KEEP" and a.beneficiary_id}
    touched = keep_ids | {bid for a in actions for bid in a.supersede_ids}
    return sorted(
        int(b["id"])
        for b in actives
        if _usable(b) and int(b["id"]) not in touched
    )


def _open_legacy_draft_ids(conn: sqlite3.Connection) -> list[int]:
    active = conn.execute(
        "SELECT id FROM nomina_banorte_catalog_versions WHERE status='ACTIVE'"
    ).fetchone()
    if active is not None:
        return []
    rows = conn.execute(
        """
        SELECT id FROM nomina_banorte_export_drafts
        WHERE status='OPEN' AND catalog_mode='LEGACY'
        ORDER BY id
        """
    ).fetchall()
    return [int(row["id"]) for row in rows]


def build_catalog_legacy_sync_plan(conn: sqlite3.Connection, version_id: int) -> CatalogLegacySyncPlan:
    persons = _load_desired_persons(conn, int(version_id))
    actives = _active_beneficiaries(conn)
    actions = [_classify_person(person, actives) for person in persons]
    extras = _compute_extras(actives, actions)
    block_drafts = _open_legacy_draft_ids(conn)
    aggregates = Counter(
        action.action for action in actions
    )
    aggregates["INACTIVATE_EXTRA"] = len(extras)
    aggregates["BLOCK_DRAFT"] = len(block_drafts)
    plan = CatalogLegacySyncPlan(
        version_id=int(version_id),
        actions=actions,
        inactivate_extra_ids=extras,
        block_draft_ids=block_drafts,
        aggregates=dict(sorted(aggregates.items())),
        valid=True,
        errors=[],
    )
    errors = validate_catalog_legacy_sync_plan(conn, plan)
    plan.valid = not errors
    plan.errors = errors
    return plan


def validate_catalog_legacy_sync_plan(
    conn: sqlite3.Connection,
    plan: CatalogLegacySyncPlan,
) -> list[str]:
    errors: list[str] = []
    employees: dict[str, int] = {}
    accounts: dict[str, int] = {}
    for action in plan.actions:
        if action.employee in employees and employees[action.employee] != action.person_id:
            errors.append("unique_employee_collision")
        else:
            employees[action.employee] = action.person_id
        if action.account in accounts and accounts[action.account] != action.person_id:
            errors.append("unique_account_collision")
        else:
            accounts[action.account] = action.person_id
        if not is_valid_employee_number(action.employee):
            errors.append("invalid_employee")
        if not is_valid_account_number(action.account):
            errors.append("invalid_account")
        if action.action == "KEEP" and action.beneficiary_id is None:
            errors.append("keep_missing_beneficiary")
        if action.action == "KEEP" and action.beneficiary_id is not None:
            successor = conn.execute(
                """
                SELECT id FROM nomina_banorte_beneficiaries
                WHERE replaces_id=? AND record_status='ACTIVO'
                """,
                (int(action.beneficiary_id),),
            ).fetchone()
            if successor is not None:
                errors.append("supersede_successor_conflict")
        if action.action == "SUPERSEDE" and not action.supersede_ids:
            errors.append("supersede_missing_legacy")
    for legacy_id in plan.inactivate_extra_ids:
        row = conn.execute(
            "SELECT record_status,replaces_id FROM nomina_banorte_beneficiaries WHERE id=?",
            (int(legacy_id),),
        ).fetchone()
        if row is None:
            errors.append("extra_not_found")
            continue
        if row["record_status"] != "ACTIVO":
            errors.append("extra_not_active")
        successor = conn.execute(
            """
            SELECT id FROM nomina_banorte_beneficiaries
            WHERE replaces_id=? AND record_status='ACTIVO'
            """,
            (int(legacy_id),),
        ).fetchone()
        if successor is not None:
            errors.append("extra_has_active_successor")
    for action in plan.actions:
        for legacy_id in action.supersede_ids:
            row = conn.execute(
                "SELECT record_status FROM nomina_banorte_beneficiaries WHERE id=?",
                (int(legacy_id),),
            ).fetchone()
            if row is None:
                errors.append("supersede_target_not_found")
            elif row["record_status"] != "ACTIVO":
                errors.append("supersede_target_not_active")
            successor = conn.execute(
                """
                SELECT id FROM nomina_banorte_beneficiaries
                WHERE replaces_id=? AND record_status='ACTIVO'
                """,
                (int(legacy_id),),
            ).fetchone()
            if successor is not None and (
                action.action != "KEEP"
                or int(successor["id"]) != int(action.beneficiary_id or 0)
            ):
                errors.append("supersede_successor_conflict")
    return sorted(set(errors))


def _insert_beneficiary_event(
    conn: sqlite3.Connection,
    *,
    beneficiary_id: int,
    action: str,
    reason: str,
    user: str,
    previous_validation_status: str | None,
    new_validation_status: str | None,
    previous_record_status: str | None,
    new_record_status: str | None,
    replacement_beneficiary_id: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiary_events (
            beneficiary_id, action, reason,
            previous_validation_status, new_validation_status,
            previous_record_status, new_record_status,
            created_by, created_at, replacement_beneficiary_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(beneficiary_id),
            action,
            reason.strip(),
            previous_validation_status,
            new_validation_status,
            previous_record_status,
            new_record_status,
            user,
            _now(),
            replacement_beneficiary_id,
        ),
    )


def _mark_replaced(
    conn: sqlite3.Connection,
    *,
    beneficiary_id: int,
    actor: str,
    reason: str,
) -> None:
    row = conn.execute(
        "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
        (int(beneficiary_id),),
    ).fetchone()
    if row is None or row["record_status"] != "ACTIVO":
        return
    now = _now()
    conn.execute(
        """
        UPDATE nomina_banorte_beneficiaries
        SET record_status='INACTIVO_REEMPLAZADO', replace_reason=?, replaced_by=?,
            replaced_at=COALESCE(replaced_at, ?), updated_at=?
        WHERE id=? AND record_status='ACTIVO'
        """,
        (reason.strip(), actor, now, now, int(beneficiary_id)),
    )
    _insert_beneficiary_event(
        conn,
        beneficiary_id=int(beneficiary_id),
        action="replace",
        reason=reason,
        user=actor,
        previous_validation_status=row["validation_status"],
        new_validation_status=row["validation_status"],
        previous_record_status=row["record_status"],
        new_record_status="INACTIVO_REEMPLAZADO",
    )


def _mark_manual_inactive(
    conn: sqlite3.Connection,
    *,
    beneficiary_id: int,
    actor: str,
    reason: str,
) -> None:
    row = conn.execute(
        "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
        (int(beneficiary_id),),
    ).fetchone()
    if row is None or row["record_status"] != "ACTIVO":
        return
    now = _now()
    conn.execute(
        """
        UPDATE nomina_banorte_beneficiaries
        SET record_status='INACTIVO_MANUAL', updated_at=?
        WHERE id=? AND record_status='ACTIVO'
        """,
        (now, int(beneficiary_id)),
    )
    _insert_beneficiary_event(
        conn,
        beneficiary_id=int(beneficiary_id),
        action="deactivate",
        reason=reason,
        user=actor,
        previous_validation_status=row["validation_status"],
        new_validation_status=row["validation_status"],
        previous_record_status=row["record_status"],
        new_record_status="INACTIVO_MANUAL",
    )


def _create_catalog_mirror(
    conn: sqlite3.Connection,
    *,
    action: PersonSyncAction,
    actor: str,
    replaces_id: int | None,
    use_catalog_rfc_as_curp: bool = True,
) -> dict[str, Any]:
    if not is_valid_employee_number(action.employee):
        raise CatalogLegacySyncError("invalid_employee")
    if not is_valid_account_number(action.account):
        raise CatalogLegacySyncError("invalid_account")
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, curp,
            employee_number_requested, employee_number_effective, account_number,
            source_kind, validation_status, record_status,
            banorte_employee_substituted, manual_effective_from_account,
            imported_at, imported_by, replaces_id, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?, 'ACTIVO',0,0,?,?,?,?,?)
        """,
        (
            action.name_original,
            normalize_name(action.name_original),
            action.rfc if use_catalog_rfc_as_curp else None,
            action.employee,
            action.employee,
            action.account,
            SOURCE_KIND_CATALOG,
            "IMPORTADO_EXITOSO",
            now,
            actor,
            replaces_id,
            now,
            now,
        ),
    )
    new_id = int(cur.lastrowid)
    _insert_beneficiary_event(
        conn,
        beneficiary_id=new_id,
        action="replace" if replaces_id else "mark_usable_manual",
        reason=action.reason_code,
        user=actor,
        previous_validation_status=None,
        new_validation_status="IMPORTADO_EXITOSO",
        previous_record_status=None,
        new_record_status="ACTIVO",
        replacement_beneficiary_id=None,
    )
    if replaces_id is not None:
        _insert_beneficiary_event(
            conn,
            beneficiary_id=int(replaces_id),
            action="replace",
            reason=action.reason_code,
            user=actor,
            previous_validation_status=None,
            new_validation_status=None,
            previous_record_status="INACTIVO_REEMPLAZADO",
            new_record_status="INACTIVO_REEMPLAZADO",
            replacement_beneficiary_id=new_id,
        )
    row = conn.execute(
        "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
        (new_id,),
    ).fetchone()
    return dict(row)


def _reconcile_person(
    conn: sqlite3.Connection,
    *,
    version_id: int,
    action: PersonSyncAction,
    beneficiary: dict[str, Any],
    actor: str,
) -> None:
    supersedes_id = _supersede_current(
        conn,
        version_id=int(version_id),
        person_id=int(action.person_id),
        actor=actor,
    )
    _insert_reconciliation(
        conn,
        version_id=int(version_id),
        person_id=int(action.person_id),
        beneficiary=beneficiary,
        status="AUTO_MATCHED",
        method=action.match_method,
        candidate_count=1,
        reason_code=action.reason_code,
        actor=actor,
        supersedes_id=supersedes_id,
    )


def apply_catalog_legacy_sync(
    conn: sqlite3.Connection,
    version_id: int,
    *,
    actor: str,
) -> dict[str, Any]:
    plan = build_catalog_legacy_sync_plan(conn, int(version_id))
    if not plan.valid:
        raise CatalogLegacySyncError(plan.errors[0] if plan.errors else "sync_plan_invalid")

    supersede_ids = sorted(
        {legacy_id for action in plan.actions for legacy_id in action.supersede_ids},
        reverse=True,
    )
    for legacy_id in supersede_ids:
        _mark_replaced(
            conn,
            beneficiary_id=int(legacy_id),
            actor=actor,
            reason=SYNC_REASON_SUPERSEDE,
        )

    final_beneficiary_ids: set[int] = set()
    for action in plan.actions:
        if action.action == "KEEP":
            beneficiary = conn.execute(
                "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
                (int(action.beneficiary_id),),
            ).fetchone()
            if beneficiary is None or beneficiary["record_status"] != "ACTIVO":
                raise CatalogLegacySyncError("keep_target_inactive")
            beneficiary_dict = dict(beneficiary)
            final_beneficiary_ids.add(int(beneficiary_dict["id"]))
            _reconcile_person(
                conn,
                version_id=int(version_id),
                action=action,
                beneficiary=beneficiary_dict,
                actor=actor,
            )
            continue
        replaces_id = action.supersede_ids[0] if action.supersede_ids else None
        beneficiary_dict = _create_catalog_mirror(
            conn,
            action=action,
            actor=actor,
            replaces_id=replaces_id,
        )
        action.match_method = _resolve_match_method(
            action.name_original, str(beneficiary_dict["nombre_original"])
        )
        final_beneficiary_ids.add(int(beneficiary_dict["id"]))
        _reconcile_person(
            conn,
            version_id=int(version_id),
            action=action,
            beneficiary=beneficiary_dict,
            actor=actor,
        )

    for extra_id in plan.inactivate_extra_ids:
        if int(extra_id) in final_beneficiary_ids:
            continue
        _mark_manual_inactive(
            conn,
            beneficiary_id=int(extra_id),
            actor=actor,
            reason=SYNC_REASON_EXTRA,
        )

    now = _now()
    for draft_id in plan.block_draft_ids:
        conn.execute(
            """
            UPDATE nomina_banorte_export_drafts
            SET status='BLOCKED_DRIFT', updated_by=?, updated_at=?
            WHERE id=? AND status='OPEN' AND catalog_mode='LEGACY'
            """,
            (actor, now, int(draft_id)),
        )

    drift_errors = validate_post_sync_mirror(conn, int(version_id), final_beneficiary_ids)
    if drift_errors:
        raise CatalogLegacySyncError("LEGACY_MIRROR_DRIFT")

    return {
        "version_id": int(version_id),
        "aggregates": plan.aggregates,
        "blocked_draft_ids": plan.block_draft_ids,
        "final_beneficiary_count": len(final_beneficiary_ids),
    }


def validate_post_sync_mirror(
    conn: sqlite3.Connection,
    version_id: int,
    final_beneficiary_ids: set[int],
) -> list[str]:
    errors: list[str] = []
    persons = _load_desired_persons(conn, int(version_id))
    for person in persons:
        row = conn.execute(
            """
            SELECT r.beneficiary_id, r.reconciliation_status, b.record_status
            FROM nomina_banorte_catalog_reconciliations r
            LEFT JOIN nomina_banorte_beneficiaries b ON b.id=r.beneficiary_id
            WHERE r.person_id=? AND r.is_current=1
            """,
            (int(person["person_id"]),),
        ).fetchone()
        if row is None or row["reconciliation_status"] != "AUTO_MATCHED":
            errors.append("person_not_auto_matched")
            continue
        if row["beneficiary_id"] is None or row["record_status"] != "ACTIVO":
            errors.append("mirror_not_active")
    active_extras = conn.execute(
        """
        SELECT id FROM nomina_banorte_beneficiaries
        WHERE record_status='ACTIVO' AND id NOT IN (
            SELECT beneficiary_id FROM nomina_banorte_catalog_reconciliations
            WHERE version_id=? AND is_current=1 AND beneficiary_id IS NOT NULL
        )
        """,
        (int(version_id),),
    ).fetchall()
    if active_extras:
        errors.append("active_extras_remain")
    if final_beneficiary_ids:
        for bid in final_beneficiary_ids:
            row = conn.execute(
                "SELECT record_status FROM nomina_banorte_beneficiaries WHERE id=?",
                (int(bid),),
            ).fetchone()
            if row is None or row["record_status"] != "ACTIVO":
                errors.append("final_mirror_inactive")
    return sorted(set(errors))
