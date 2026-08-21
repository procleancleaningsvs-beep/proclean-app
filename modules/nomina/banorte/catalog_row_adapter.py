from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from modules.nomina.banorte.beneficiary_material import beneficiary_material_fingerprint
from modules.nomina.banorte.catalog_lifecycle import legacy_authority_allowed
from modules.nomina.banorte.catalog_search_service import _evaluate_sidebar_person
from modules.nomina.banorte.prepare_service import prepare_draft_rows
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.rows_capture import CaptureRow, capture_rows_to_prepare_inputs


def _load_catalog_person_bundle(
    conn, person_id: int, active_version_id: int
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
        (int(person_id), int(active_version_id)),
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


def apply_catalog_provenance_to_row(
    row: dict[str, Any],
    *,
    person: dict[str, Any],
    reconciliation: dict[str, Any] | None,
    beneficiary: dict[str, Any] | None,
    active_version_id: int,
    authority: dict[str, Any],
) -> dict[str, Any]:
    out = dict(row)
    out["catalog_person_id"] = int(person["id"])
    out["nombre_recibido"] = str(person.get("name_original") or out.get("nombre_recibido") or "")
    out["employee_number_snapshot"] = str(person.get("employee_number_normalized") or "")
    out["account_number_snapshot"] = str(person.get("account_number_normalized") or "")
    out["catalog_observation_codes"] = list(out.get("catalog_observation_codes") or [])
    if reconciliation is not None:
        out["catalog_reconciliation_id"] = int(reconciliation["id"])
        out["catalog_match_method"] = str(reconciliation.get("match_method") or "")
    if beneficiary is not None:
        out["beneficiary_id"] = int(beneficiary["id"])
        fp = beneficiary_material_fingerprint(beneficiary)
        out["beneficiary_material_fingerprint_version"] = fp.version
        out["beneficiary_material_fingerprint_seen"] = fp.sha256
        out["banco_snapshot"] = "Banorte"
    if authority.get("payment_enabled"):
        out["row_state"] = "OK" if int(out.get("included") or 0) == 1 else out.get("row_state")
        out["match_kind"] = str(reconciliation.get("match_method") or "CATALOG") if reconciliation else "CATALOG"
    else:
        out["included"] = 0
        out["row_state"] = "NEEDS_REVIEW"
        for code in authority.get("reason_codes") or []:
            if code not in out["catalog_observation_codes"]:
                out["catalog_observation_codes"].append(code)
    out["catalog_observation_codes_json"] = json.dumps(
        out.get("catalog_observation_codes") or [], ensure_ascii=False
    )
    return out


def prepare_capture_rows(
    db_path: str | Path,
    rows: list[CaptureRow],
    *,
    origin_kind: str,
) -> list[dict[str, Any]]:
    base_rows = capture_rows_to_prepare_inputs(rows)
    conn = connect(db_path)
    try:
        active_row = conn.execute(
            "SELECT id FROM nomina_banorte_catalog_versions WHERE status='ACTIVE'"
        ).fetchone()
        active_id = int(active_row["id"]) if active_row is not None else None
        use_legacy = legacy_authority_allowed(conn)
    finally:
        conn.close()
    if use_legacy or active_id is None:
        prepared: list[dict[str, Any]] = []
        for base in base_rows:
            if str(base.get("row_state") or "") == "NEEDS_REVIEW":
                out = dict(base)
                out.setdefault("match_kind", "NONE")
                out.setdefault("warnings", [])
                out.setdefault("user_decision", {})
                out["catalog_observation_codes_json"] = json.dumps(
                    base.get("catalog_observation_codes") or [], ensure_ascii=False
                )
                prepared.append(out)
            else:
                prepared.append(prepare_draft_rows(db_path, [base], origin_kind=origin_kind)[0])
        return prepared
    conn = connect(db_path)
    try:
        prepared: list[dict[str, Any]] = []
        for base in base_rows:
            person_id = base.get("catalog_person_id")
            if person_id is None:
                legacy_one = prepare_draft_rows(db_path, [base], origin_kind=origin_kind)[0]
                legacy_one["catalog_observation_codes_json"] = json.dumps(
                    base.get("catalog_observation_codes") or [], ensure_ascii=False
                )
                prepared.append(legacy_one)
                continue
            person, reconciliation, beneficiary = _load_catalog_person_bundle(
                conn, int(person_id), active_id
            )
            authority = _evaluate_sidebar_person(
                person=person,
                reconciliation=reconciliation,
                beneficiary=beneficiary,
                active_version_id=active_id,
            )
            merged = apply_catalog_provenance_to_row(
                base,
                person=person,
                reconciliation=reconciliation,
                beneficiary=beneficiary,
                active_version_id=active_id,
                authority=authority,
            )
            prepared.append(merged)
        return prepared
    finally:
        conn.close()
