from __future__ import annotations

import json
import sqlite3
from typing import Any

from modules.nomina.banorte.beneficiary_material import beneficiary_material_fingerprint
from modules.nomina.banorte.catalog_lifecycle import (
    active_catalog_version_id,
    has_ever_had_catalog_activation,
    legacy_authority_allowed,
)
from modules.nomina.banorte.repository import connect


def _manual_effective_confirmed(row: dict[str, Any]) -> bool:
    ud = row.get("user_decision") or {}
    return bool(ud.get("confirm_manual_effective_from_account"))


def evaluate_payment_authority(
    *,
    conn: sqlite3.Connection | None = None,
    db_path: str | None = None,
    draft: dict[str, Any] | None = None,
    row: dict[str, Any] | None = None,
    person: dict[str, Any] | None = None,
    reconciliation: dict[str, Any] | None = None,
    beneficiary: dict[str, Any] | None = None,
    active_version_id: int | None = None,
) -> dict[str, Any]:
    """Single runtime gate for PAYMENT_ENABLED and export authority."""
    owns_conn = conn is None
    if conn is None:
        if db_path is None:
            raise ValueError("conn_or_db_path_required")
        conn = connect(db_path)
    try:
        if active_version_id is None:
            active_version_id = active_catalog_version_id(conn)
        reason_codes: list[str] = []
        if draft is not None:
            draft_mode = str(draft.get("catalog_mode") or "LEGACY")
            draft_version = draft.get("catalog_version_id")
            if draft_mode == "CATALOG":
                if active_version_id is None:
                    reason_codes.append("CATALOG_ACTIVE_REQUIRED")
                elif draft_version is None or int(draft_version) != int(active_version_id):
                    reason_codes.append("CATALOG_VERSION_CHANGED")
            elif not legacy_authority_allowed(conn):
                reason_codes.append("CATALOG_ACTIVE_REQUIRED")
        if row is not None:
            if str(draft.get("catalog_mode") if draft else "") == "CATALOG" or (
                active_version_id is not None and not legacy_authority_allowed(conn)
            ):
                if row.get("catalog_person_id") is None:
                    reason_codes.append("CATALOG_PROVENANCE_MISSING")
                if row.get("catalog_reconciliation_id") is None:
                    reason_codes.append("RECONCILIATION_MISSING")
                fp_version = row.get("beneficiary_material_fingerprint_version")
                fp_seen = row.get("beneficiary_material_fingerprint_seen")
                if beneficiary is not None and fp_seen:
                    live = beneficiary_material_fingerprint(beneficiary)
                    if fp_version and str(fp_version) != live.version:
                        reason_codes.append("FINGERPRINT_VERSION_MISMATCH")
                    if str(fp_seen) != live.sha256:
                        reason_codes.append("RECONCILIATION_STALE")
        if person is not None:
            if active_version_id is None:
                reason_codes.append("CATALOG_ACTIVE_REQUIRED")
            elif int(person.get("version_id") or 0) != int(active_version_id):
                reason_codes.append("CATALOG_VERSION_CHANGED")
            person_status = str(person.get("person_status") or "")
            if person_status != "CATALOG_READY":
                reason_codes.append("CATALOG_PERSON_NOT_READY")
            if str(person.get("eligibility") or "") not in {"", "ELIGIBLE"}:
                reason_codes.append("NO_ELIGIBLE_ROW")
        if reconciliation is None and (person is not None or (row and row.get("catalog_person_id"))):
            reason_codes.append("RECONCILIATION_MISSING")
        elif reconciliation is not None:
            recon_status = str(reconciliation.get("reconciliation_status") or "")
            if recon_status not in {"AUTO_MATCHED", "MANUAL_MATCHED"}:
                reason_codes.append(recon_status or "RECONCILIATION_MISSING")
            elif int(reconciliation.get("is_current") or 0) != 1:
                reason_codes.append("RECONCILIATION_STALE")
            elif beneficiary is not None:
                live_fp = beneficiary_material_fingerprint(beneficiary)
                if (
                    str(reconciliation.get("beneficiary_material_fingerprint") or "")
                    != live_fp.sha256
                ):
                    reason_codes.append("RECONCILIATION_STALE")
                elif (
                    str(reconciliation.get("beneficiary_material_fingerprint_version") or "")
                    != live_fp.version
                ):
                    reason_codes.append("FINGERPRINT_VERSION_MISMATCH")
        if beneficiary is None and (
            person is not None or (row and row.get("catalog_person_id"))
        ):
            reason_codes.append("LEGACY_NOT_USABLE")
        elif beneficiary is not None:
            if str(beneficiary.get("record_status") or "") != "ACTIVO":
                reason_codes.append("LEGACY_NOT_USABLE")
            employee = str(
                (person or {}).get("employee_number_normalized")
                or (row or {}).get("employee_number_snapshot")
                or ""
            )
            account = str(
                (person or {}).get("account_number_normalized")
                or (row or {}).get("account_number_snapshot")
                or ""
            )
            if employee and str(beneficiary.get("employee_number_effective") or "") != employee:
                reason_codes.append("EMPLOYEE_MISMATCH")
            if account and str(beneficiary.get("account_number") or "") != account:
                reason_codes.append("ACCOUNT_MISMATCH")
            if int(beneficiary.get("manual_effective_from_account") or 0) == 1:
                check_row = row or {"user_decision": {}}
                if not _manual_effective_confirmed(check_row):
                    reason_codes.append("MANUAL_PENDIENTE_VALIDACION")
        enabled = not reason_codes
        return {
            "payment_enabled": enabled,
            "reason_codes": reason_codes,
            "fail_closed": has_ever_had_catalog_activation(conn)
            and active_version_id is None,
        }
    finally:
        if owns_conn:
            conn.close()


def load_catalog_authority_bundle(
    conn: sqlite3.Connection,
    *,
    catalog_person_id: int,
    active_version_id: int,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    row = conn.execute(
        """
        SELECT p.id, p.version_id, p.person_status,
               r.employee_number_normalized, r.account_number_normalized,
               r.name_original, r.name_normalized, r.eligibility,
               rec.id AS reconciliation_id, rec.reconciliation_status,
               rec.match_method, rec.is_current, rec.beneficiary_id,
               rec.beneficiary_material_fingerprint,
               rec.beneficiary_material_fingerprint_version,
               b.id AS beneficiary_id_live,
               b.employee_number_effective, b.account_number,
               b.record_status, b.manual_effective_from_account,
               b.nombre_original AS beneficiary_name_original,
               b.nombre_normalizado, b.curp,
               b.employee_number_requested, b.source_kind,
               b.validation_status, b.banorte_employee_substituted,
               b.replaces_id, b.updated_at
        FROM nomina_banorte_catalog_persons p
        JOIN nomina_banorte_catalog_rows r ON r.id=p.current_row_id
        LEFT JOIN nomina_banorte_catalog_reconciliations rec
          ON rec.person_id=p.id AND rec.is_current=1
        LEFT JOIN nomina_banorte_beneficiaries b ON b.id=rec.beneficiary_id
        WHERE p.id=? AND p.version_id=?
        """,
        (int(catalog_person_id), int(active_version_id)),
    ).fetchone()
    if row is None:
        raise ValueError("catalog_person_not_found")
    raw = dict(row)
    person = {
        "id": raw["id"],
        "version_id": raw["version_id"],
        "person_status": raw["person_status"],
        "employee_number_normalized": raw["employee_number_normalized"],
        "account_number_normalized": raw["account_number_normalized"],
        "name_original": raw["name_original"],
        "name_normalized": raw["name_normalized"],
        "eligibility": raw["eligibility"],
    }
    reconciliation = None
    if raw.get("reconciliation_id") is not None:
        reconciliation = {
            "id": raw["reconciliation_id"],
            "reconciliation_status": raw["reconciliation_status"],
            "match_method": raw["match_method"],
            "is_current": raw["is_current"],
            "beneficiary_id": raw["beneficiary_id"],
            "beneficiary_material_fingerprint": raw["beneficiary_material_fingerprint"],
            "beneficiary_material_fingerprint_version": raw[
                "beneficiary_material_fingerprint_version"
            ],
        }
    beneficiary = None
    if raw.get("beneficiary_id_live") is not None:
        beneficiary = {
            "id": raw["beneficiary_id_live"],
            "employee_number_effective": raw["employee_number_effective"],
            "account_number": raw["account_number"],
            "record_status": raw["record_status"],
            "manual_effective_from_account": raw["manual_effective_from_account"],
            "nombre_original": raw["beneficiary_name_original"],
            "nombre_normalizado": raw.get("nombre_normalizado"),
            "curp": raw.get("curp"),
            "employee_number_requested": raw.get("employee_number_requested"),
            "source_kind": raw.get("source_kind"),
            "validation_status": raw.get("validation_status"),
            "banorte_employee_substituted": raw.get("banorte_employee_substituted"),
            "replaces_id": raw.get("replaces_id"),
            "updated_at": raw.get("updated_at"),
        }
    return person, reconciliation, beneficiary


def resolve_catalog_person_id_for_beneficiary(
    conn: sqlite3.Connection,
    active_version_id: int,
    beneficiary_id: int,
) -> int | None:
    row = conn.execute(
        """
        SELECT r.person_id
        FROM nomina_banorte_catalog_reconciliations r
        JOIN nomina_banorte_catalog_persons p ON p.id=r.person_id
        WHERE r.version_id=? AND r.beneficiary_id=? AND r.is_current=1
          AND r.reconciliation_status IN ('AUTO_MATCHED','MANUAL_MATCHED')
          AND p.version_id=?
        LIMIT 1
        """,
        (int(active_version_id), int(beneficiary_id), int(active_version_id)),
    ).fetchone()
    return int(row["person_id"]) if row is not None else None


def _attach_catalog_provenance(
    out: dict[str, Any],
    *,
    person: dict[str, Any] | None,
    reconciliation: dict[str, Any] | None,
    beneficiary: dict[str, Any] | None,
    authority: dict[str, Any],
) -> None:
    if person is not None:
        out["catalog_person_id"] = int(person["id"])
    if reconciliation is not None:
        out["catalog_reconciliation_id"] = int(reconciliation["id"])
        out["catalog_match_method"] = str(reconciliation.get("match_method") or "")
    if beneficiary is not None:
        fp = beneficiary_material_fingerprint(beneficiary)
        out["beneficiary_material_fingerprint_version"] = fp.version
        out["beneficiary_material_fingerprint_seen"] = fp.sha256
    codes = list(authority.get("reason_codes") or [])
    out["catalog_observation_codes_json"] = json.dumps(codes, ensure_ascii=False)


def apply_authority_to_mutable_row(
    conn: sqlite3.Connection,
    *,
    draft: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, Any]:
    """Revalidate a mutable draft row against catalog authority when required."""
    if legacy_authority_allowed(conn):
        return dict(row)
    hydrated = rehydrate_row_authority(conn, draft=draft, row=row)
    authority = hydrated.pop("catalog_authority", {})
    out = hydrated
    codes = list(authority.get("reason_codes") or [])
    out["catalog_observation_codes_json"] = json.dumps(codes, ensure_ascii=False)
    cents = int(out.get("amount_final_cents") or 0)
    excluded = out.get("excluded_at") not in (None, "")
    wants_included = cents > 0 and not excluded
    if not authority.get("payment_enabled"):
        out["included"] = 0
        out["row_state"] = "NEEDS_REVIEW"
    elif wants_included:
        out["included"] = 1
        out["row_state"] = "OK"
    return out


def enforce_prepared_rows_catalog_authority(
    db_path: str | Path,
    draft: dict[str, Any],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    conn = connect(db_path)
    try:
        if legacy_authority_allowed(conn):
            return rows
        return [apply_authority_to_mutable_row(conn, draft=draft, row=dict(row)) for row in rows]
    finally:
        conn.close()


def rehydrate_row_authority(
    conn: sqlite3.Connection,
    *,
    draft: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, Any]:
    """Reload authoritative account/employee from catalog + beneficiary."""
    out = dict(row)
    active_id = active_catalog_version_id(conn)
    bid = row.get("beneficiary_id")
    beneficiary = None
    if bid is not None:
        ben_row = conn.execute(
            "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
            (int(bid),),
        ).fetchone()
        if ben_row is not None:
            beneficiary = dict(ben_row)
            out["account_number_snapshot"] = str(beneficiary["account_number"])
            out["employee_number_snapshot"] = str(beneficiary["employee_number_effective"])
    person = reconciliation = None
    cpid = row.get("catalog_person_id")
    if cpid is None and active_id is not None and bid is not None:
        resolved = resolve_catalog_person_id_for_beneficiary(conn, int(active_id), int(bid))
        if resolved is not None:
            cpid = resolved
            out["catalog_person_id"] = resolved
    if cpid is not None and active_id is not None:
        person, reconciliation, cat_beneficiary = load_catalog_authority_bundle(
            conn, catalog_person_id=int(cpid), active_version_id=int(active_id)
        )
        if cat_beneficiary is not None:
            beneficiary = cat_beneficiary
            out["beneficiary_id"] = int(cat_beneficiary["id"])
            out["account_number_snapshot"] = str(cat_beneficiary["account_number"])
            out["employee_number_snapshot"] = str(cat_beneficiary["employee_number_effective"])
        if person is not None:
            out["nombre_recibido"] = str(person.get("name_original") or out.get("nombre_recibido") or "")
        if person is not None or reconciliation is not None or beneficiary is not None:
            _attach_catalog_provenance(
                out,
                person=person,
                reconciliation=reconciliation,
                beneficiary=beneficiary,
                authority={"reason_codes": []},
            )
    authority = evaluate_payment_authority(
        conn=conn,
        draft=draft,
        row=out,
        person=person,
        reconciliation=reconciliation,
        beneficiary=beneficiary,
        active_version_id=active_id,
    )
    _attach_catalog_provenance(
        out,
        person=person if cpid is not None and active_id is not None else None,
        reconciliation=reconciliation if cpid is not None and active_id is not None else None,
        beneficiary=beneficiary,
        authority=authority,
    )
    out["catalog_authority"] = authority
    return out


def evaluate_catalog_export_blockers(
    conn: sqlite3.Connection,
    draft: dict[str, Any],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    blocked: list[dict[str, Any]] = []
    active_id = active_catalog_version_id(conn)
    for row in rows:
        if int(row.get("included") or 0) != 1:
            continue
        if str(row.get("row_state") or "") != "OK":
            continue
        hydrated = rehydrate_row_authority(conn, draft=draft, row=row)
        authority = hydrated.get("catalog_authority") or {}
        for code in authority.get("reason_codes") or []:
            blocked.append({"position": row.get("position"), "reason": code})
    if str(draft.get("catalog_mode") or "") == "CATALOG" and active_id is None:
        blocked.append({"position": None, "reason": "CATALOG_ACTIVE_REQUIRED"})
    if has_ever_had_catalog_activation(conn) and active_id is None:
        blocked.append({"position": None, "reason": "CATALOG_ACTIVE_REQUIRED"})
    return blocked
