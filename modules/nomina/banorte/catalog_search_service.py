from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from modules.nomina.banorte.catalog_search_cursor import (
    CatalogSearchCursorError,
    issue_catalog_search_cursor,
    parse_catalog_search_cursor,
)
from modules.nomina.banorte.payment_authority import evaluate_payment_authority
from modules.nomina.banorte.post_catalog_authority import (
    AUTHORITY_KIND_POST_CATALOG,
    search_post_catalog_additions,
)
from modules.nomina.banorte.repository import connect

_MAX_Q = 100
_MAX_LIMIT = 50
_DEFAULT_LIMIT = 25
_SORT_ALLOWLIST = frozenset({"employee_asc", "employee_desc", "name_asc"})

_PERSON_BLOCK_LABELS: dict[str, str] = {
    "NO_ELIGIBLE_ROW": "Sin fila elegible en catálogo.",
    "IDENTITY_CONFLICT": "Conflicto de identidad en catálogo.",
    "AMBIGUOUS_CURRENT_ACCOUNT": "Cuenta actual ambigua.",
    "INVALID_CURRENT_ROW": "Fila actual inválida.",
    "CATALOG_READY": "",
}

_RECON_BLOCK_LABELS: dict[str, str] = {
    "UNMATCHED": "Sin reconciliación con beneficiario legacy.",
    "MULTIPLE_CANDIDATES": "Múltiples candidatos legacy.",
    "IDENTITY_CONFLICT": "Conflicto de identidad en reconciliación.",
    "ACCOUNT_MISMATCH": "Cuenta no coincide.",
    "EMPLOYEE_MISMATCH": "Número de empleado no coincide.",
    "LEGACY_NOT_USABLE": "Beneficiario legacy no utilizable.",
    "STALE_RECONCILIATION": "Reconciliación desactualizada.",
}

_BENEFICIARY_BLOCK_LABELS: dict[str, str] = {
    "beneficiary_missing": "Beneficiario legacy no encontrado.",
    "beneficiary_not_active": "Beneficiario legacy inactivo.",
    "employee_mismatch": "Número de empleado no coincide.",
    "account_mismatch": "Cuenta no coincide.",
    "fingerprint_mismatch": "Huella material desactualizada.",
    "manual_effective_confirmation_required": "Requiere confirmación manual.",
    "PRE_CATALOG_LEGACY_EXCLUDED": "Beneficiario anterior al catálogo activo.",
    "POST_CATALOG_SOURCE_NOT_AUTHORIZED": "Alta no autorizada post-catálogo.",
    "CATALOG_EMPLOYEE_COLLISION": "Número de empleado ocupado por catálogo.",
    "CATALOG_ACCOUNT_COLLISION": "Cuenta ocupada por catálogo.",
}


def _normalize_q(raw: str) -> str:
    q = str(raw or "").strip()
    if len(q) > _MAX_Q:
        q = q[:_MAX_Q]
    return q


def _digits_only(value: str) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _active_version_id(conn) -> int | None:
    row = conn.execute(
        "SELECT id FROM nomina_banorte_catalog_versions WHERE status='ACTIVE'"
    ).fetchone()
    return int(row["id"]) if row is not None else None


def _order_clause(sort: str) -> str:
    if sort == "employee_desc":
        return "r.employee_number_normalized DESC, p.name_normalized ASC, p.id ASC"
    if sort == "name_asc":
        return "p.name_normalized ASC, r.employee_number_normalized ASC, p.id ASC"
    return "r.employee_number_normalized ASC, p.name_normalized ASC, p.id ASC"


def _evaluate_sidebar_person(
    *,
    conn,
    person: dict[str, Any],
    reconciliation: dict[str, Any] | None,
    beneficiary: dict[str, Any] | None,
    active_version_id: int,
) -> dict[str, Any]:
    person_status = str(person.get("person_status") or "")
    authority = evaluate_payment_authority(
        conn=conn,
        person=person,
        reconciliation=reconciliation,
        beneficiary=beneficiary,
        active_version_id=active_version_id,
    )
    reason_codes = list(authority.get("reason_codes") or [])
    payment_enabled = bool(authority.get("payment_enabled"))
    block_reason = _human_block_reason(reason_codes, person_status, reconciliation, beneficiary)
    return {
        "catalog_person_id": int(person["id"]),
        "authority_kind": "CATALOG",
        "beneficiary_id": int(beneficiary["id"]) if beneficiary is not None else None,
        "employee_number": str(person.get("employee_number_normalized") or ""),
        "display_name": str(person.get("name_original") or person.get("name_normalized") or ""),
        "account_masked": _mask_account(str(person.get("account_number_normalized") or "")),
        "payment_enabled": payment_enabled,
        "reason_codes": reason_codes,
        "block_reason": block_reason,
    }


def _human_block_reason(
    reason_codes: list[str],
    person_status: str,
    reconciliation: dict[str, Any] | None,
    beneficiary: dict[str, Any] | None,
) -> str | None:
    if not reason_codes:
        return None
    for code in reason_codes:
        if code in _PERSON_BLOCK_LABELS and _PERSON_BLOCK_LABELS[code]:
            return _PERSON_BLOCK_LABELS[code]
    if reconciliation is not None:
        recon_status = str(reconciliation.get("reconciliation_status") or "")
        if recon_status in _RECON_BLOCK_LABELS:
            return _RECON_BLOCK_LABELS[recon_status]
    for code in reason_codes:
        if code in _RECON_BLOCK_LABELS:
            return _RECON_BLOCK_LABELS[code]
        if code in _BENEFICIARY_BLOCK_LABELS:
            return _BENEFICIARY_BLOCK_LABELS[code]
        if code == "MANUAL_PENDIENTE_VALIDACION":
            return "Pendiente de validación operacional."
        if code == "STALE_RECONCILIATION":
            return "Reconciliación desactualizada."
        if code == "FINGERPRINT_VERSION_MISMATCH":
            return "Versión de huella desactualizada."
    if person_status != "CATALOG_READY":
        return "Persona no lista para pago."
    return "No habilitado para pago."


def _mask_account(account: str) -> str:
    digits = _digits_only(account)
    if len(digits) <= 4:
        return digits
    return f"{'*' * (len(digits) - 4)}{digits[-4:]}"


def _search_where(q: str) -> tuple[str, list[Any]]:
    if not q:
        return "", []
    digits = _digits_only(q)
    clauses: list[str] = []
    params: list[Any] = []
    normalized = q.upper()
    clauses.append(
        "(p.name_normalized LIKE ? OR r.name_normalized LIKE ? OR r.name_original LIKE ?)"
    )
    like = f"%{normalized}%"
    params.extend([like, like, like])
    if digits:
        clauses.append("(r.employee_number_normalized LIKE ? OR r.account_number_normalized LIKE ?)")
        digit_like = f"%{digits}%"
        params.extend([digit_like, digit_like])
    return f"({' OR '.join(clauses)})", params


def search_catalog_sidebar(
    db_path: str | Path,
    *,
    secret_key: str,
    q: str = "",
    sort: str = "employee_asc",
    cursor: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    role: str = "",
) -> dict[str, Any]:
    q_norm = _normalize_q(q)
    limit = max(1, min(int(limit or _DEFAULT_LIMIT), _MAX_LIMIT))
    if sort not in _SORT_ALLOWLIST:
        sort = "employee_asc"
    offset = 0
    version_id: int | None = None
    if cursor:
        parsed = parse_catalog_search_cursor(secret_key=secret_key, cursor=cursor)
        version_id = int(parsed["version_id"])
        offset = int(parsed["offset"])
        sort = str(parsed["sort"])
        limit = int(parsed["limit"])
    conn = connect(db_path)
    try:
        active_id = _active_version_id(conn)
        if active_id is None:
            return {
                "catalog_active": False,
                "active_version_id": None,
                "message": "Catálogo oficial todavía no activo",
                "items": [],
                "next_cursor": None,
                "total_estimate": 0,
            }
        if version_id is not None and int(version_id) != int(active_id):
            raise CatalogSearchCursorError("cursor_version_stale")
        version_id = int(active_id)
        where_sql, where_params = _search_where(q_norm)
        base_from = """
            FROM nomina_banorte_catalog_persons p
            JOIN nomina_banorte_catalog_rows r ON r.id=p.current_row_id
            LEFT JOIN nomina_banorte_catalog_reconciliations rec
              ON rec.person_id=p.id AND rec.is_current=1
            LEFT JOIN nomina_banorte_beneficiaries b ON b.id=rec.beneficiary_id
            WHERE p.version_id=?
        """
        params: list[Any] = [version_id]
        if where_sql:
            base_from += f" AND {where_sql}"
            params.extend(where_params)
        count_sql = f"SELECT COUNT(*) {base_from}"
        catalog_total = int(conn.execute(count_sql, params).fetchone()[0])
        post_items, post_total = search_post_catalog_additions(
            conn, q=q_norm, limit=5000, offset=0
        )
        total = catalog_total + post_total
        query_sql = f"""
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
            {base_from}
            ORDER BY {_order_clause(sort)}
        """
        rows = conn.execute(query_sql, params).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
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
            evaluated = _evaluate_sidebar_person(
                conn=conn,
                person=person,
                reconciliation=reconciliation,
                beneficiary=beneficiary,
                active_version_id=version_id,
            )
            if role != "admin":
                evaluated.pop("reason_codes", None)
            items.append(evaluated)
        for post_item in post_items:
            block_reason = _human_block_reason(
                list(post_item.get("reason_codes") or []),
                "CATALOG_READY",
                None,
                None,
            )
            post_item["block_reason"] = block_reason
            if role != "admin":
                post_item.pop("reason_codes", None)
            items.append(post_item)
        items.sort(
            key=lambda it: (
                str(it.get("employee_number") or ""),
                str(it.get("display_name") or ""),
                int(it.get("catalog_person_id") or it.get("beneficiary_id") or 0),
            )
        )
        page_items = items[offset : offset + limit]
        has_more = len(items) > offset + limit
        next_cursor = None
        if has_more:
            next_cursor = issue_catalog_search_cursor(
                secret_key=secret_key,
                version_id=version_id,
                offset=offset + limit,
                sort=sort,
                limit=limit,
            )
        return {
            "catalog_active": True,
            "active_version_id": version_id,
            "message": None,
            "items": page_items,
            "next_cursor": next_cursor,
            "total_estimate": total,
        }
    finally:
        conn.close()
