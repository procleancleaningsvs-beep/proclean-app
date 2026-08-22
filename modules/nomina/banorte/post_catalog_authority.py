"""Post-catalog payment authority: additions after ACTIVE Empleados.txt snapshot."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _resolve_catalog_person_id_for_beneficiary(
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

AUTHORITY_KIND_CATALOG = "CATALOG"
AUTHORITY_KIND_POST_CATALOG = "POST_CATALOG_ADDITION"
MATCH_METHOD_POST_CATALOG = "POST_CATALOG_ADDITION"

_AUTHORIZED_SOURCES = frozenset({"REPORTE_DETALLADO", "ALTA_MANUAL"})


class ActiveCatalogContext:
    __slots__ = ("version_id", "activated_at")

    def __init__(self, version_id: int, activated_at: str) -> None:
        self.version_id = int(version_id)
        self.activated_at = str(activated_at)


def parse_utc_timestamp(value: str | None) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_active_catalog_context(conn: sqlite3.Connection) -> ActiveCatalogContext | None:
    row = conn.execute(
        """
        SELECT id, activated_at
        FROM nomina_banorte_catalog_versions
        WHERE status='ACTIVE'
        LIMIT 1
        """
    ).fetchone()
    if row is None or not row["activated_at"]:
        return None
    return ActiveCatalogContext(int(row["id"]), str(row["activated_at"]))


def beneficiary_created_after_activation(
    beneficiary: dict[str, Any],
    *,
    activated_at: str,
) -> bool:
    created = parse_utc_timestamp(str(beneficiary.get("created_at") or ""))
    activated = parse_utc_timestamp(activated_at)
    if created is None or activated is None:
        return False
    return created >= activated


def is_pre_catalog_legacy_orphan(
    conn: sqlite3.Connection,
    beneficiary: dict[str, Any],
    *,
    ctx: ActiveCatalogContext,
) -> bool:
    bid = int(beneficiary["id"])
    if _resolve_catalog_person_id_for_beneficiary(conn, ctx.version_id, bid) is not None:
        return False
    if beneficiary_created_after_activation(beneficiary, activated_at=ctx.activated_at):
        return False
    return True


def _digits(value: str) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _catalog_occupies_employee(
    conn: sqlite3.Connection, version_id: int, employee: str
) -> bool:
    emp = _digits(employee)
    if not emp:
        return False
    row = conn.execute(
        """
        SELECT 1
        FROM nomina_banorte_catalog_persons p
        JOIN nomina_banorte_catalog_rows r ON r.id=p.current_row_id
        WHERE p.version_id=? AND p.person_status='CATALOG_READY'
          AND r.employee_number_normalized=?
        LIMIT 1
        """,
        (int(version_id), emp),
    ).fetchone()
    return row is not None


def _catalog_occupies_account(conn: sqlite3.Connection, version_id: int, account: str) -> bool:
    acct = _digits(account)
    if not acct:
        return False
    row = conn.execute(
        """
        SELECT 1
        FROM nomina_banorte_catalog_persons p
        JOIN nomina_banorte_catalog_rows r ON r.id=p.current_row_id
        WHERE p.version_id=? AND p.person_status='CATALOG_READY'
          AND r.account_number_normalized=?
        LIMIT 1
        """,
        (int(version_id), acct),
    ).fetchone()
    return row is not None


def _other_active_occupies(
    conn: sqlite3.Connection,
    *,
    employee: str | None = None,
    account: str | None = None,
    exclude_id: int,
) -> bool:
    emp = _digits(employee or "")
    acct = _digits(account or "")
    if emp:
        row = conn.execute(
            """
            SELECT 1 FROM nomina_banorte_beneficiaries
            WHERE record_status='ACTIVO' AND employee_number_effective=? AND id<>?
            LIMIT 1
            """,
            (emp, int(exclude_id)),
        ).fetchone()
        if row is not None:
            return True
    if acct:
        row = conn.execute(
            """
            SELECT 1 FROM nomina_banorte_beneficiaries
            WHERE record_status='ACTIVO' AND account_number=? AND id<>?
            LIMIT 1
            """,
            (acct, int(exclude_id)),
        ).fetchone()
        if row is not None:
            return True
    return False


def post_catalog_source_operational(beneficiary: dict[str, Any]) -> tuple[bool, list[str]]:
    source = str(beneficiary.get("source_kind") or "")
    validation = str(beneficiary.get("validation_status") or "")
    if source not in _AUTHORIZED_SOURCES:
        return False, ["POST_CATALOG_SOURCE_NOT_AUTHORIZED"]
    if validation == "MANUAL_PENDIENTE_VALIDACION":
        return False, ["MANUAL_PENDIENTE_VALIDACION"]
    if validation != "IMPORTADO_EXITOSO":
        return False, ["POST_CATALOG_VALIDATION_NOT_AUTHORIZED"]
    return True, []


def evaluate_post_catalog_addition(
    conn: sqlite3.Connection,
    beneficiary: dict[str, Any],
    *,
    ctx: ActiveCatalogContext | None = None,
) -> dict[str, Any]:
    """Return payment_enabled + reason_codes for POST-CATALOG ADDITION branch."""
    if ctx is None:
        ctx = load_active_catalog_context(conn)
    reason_codes: list[str] = []
    if ctx is None:
        return {
            "authority_kind": AUTHORITY_KIND_POST_CATALOG,
            "payment_enabled": False,
            "reason_codes": ["CATALOG_ACTIVE_REQUIRED"],
        }
    bid = int(beneficiary["id"])
    if _resolve_catalog_person_id_for_beneficiary(conn, ctx.version_id, bid) is not None:
        return {
            "authority_kind": AUTHORITY_KIND_CATALOG,
            "payment_enabled": False,
            "reason_codes": ["USE_CATALOG_AUTHORITY"],
        }
    if is_pre_catalog_legacy_orphan(conn, beneficiary, ctx=ctx):
        reason_codes.append("PRE_CATALOG_LEGACY_EXCLUDED")
    if str(beneficiary.get("record_status") or "") != "ACTIVO":
        reason_codes.append("LEGACY_NOT_USABLE")
    ok_source, source_codes = post_catalog_source_operational(beneficiary)
    reason_codes.extend(source_codes)
    if not beneficiary_created_after_activation(beneficiary, activated_at=ctx.activated_at):
        if "PRE_CATALOG_LEGACY_EXCLUDED" not in reason_codes:
            reason_codes.append("PRE_CATALOG_LEGACY_EXCLUDED")
    emp = str(beneficiary.get("employee_number_effective") or "")
    acct = str(beneficiary.get("account_number") or "")
    if _catalog_occupies_employee(conn, ctx.version_id, emp):
        reason_codes.append("CATALOG_EMPLOYEE_COLLISION")
    if _catalog_occupies_account(conn, ctx.version_id, acct):
        reason_codes.append("CATALOG_ACCOUNT_COLLISION")
    if _other_active_occupies(conn, employee=emp, account=acct, exclude_id=bid):
        if "CATALOG_EMPLOYEE_COLLISION" not in reason_codes and emp:
            reason_codes.append("POST_CATALOG_EMPLOYEE_COLLISION")
        if "CATALOG_ACCOUNT_COLLISION" not in reason_codes and acct:
            reason_codes.append("POST_CATALOG_ACCOUNT_COLLISION")
    if int(beneficiary.get("manual_effective_from_account") or 0) == 1:
        reason_codes.append("MANUAL_PENDIENTE_VALIDACION")
    enabled = ok_source and not reason_codes
    return {
        "authority_kind": AUTHORITY_KIND_POST_CATALOG,
        "payment_enabled": enabled,
        "reason_codes": reason_codes,
    }


def load_beneficiary_row(conn: sqlite3.Connection, beneficiary_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
        (int(beneficiary_id),),
    ).fetchone()
    return dict(row) if row is not None else None


def resolve_beneficiary_payment_authority(
    conn: sqlite3.Connection,
    beneficiary_id: int,
    *,
    ctx: ActiveCatalogContext | None = None,
    row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Single resolver: catalog member or authorized post-catalog addition."""
    if ctx is None:
        ctx = load_active_catalog_context(conn)
    beneficiary = load_beneficiary_row(conn, beneficiary_id)
    if beneficiary is None:
        return {
            "authority_kind": None,
            "payment_enabled": False,
            "reason_codes": ["beneficiary_missing"],
            "person": None,
            "reconciliation": None,
            "beneficiary": None,
            "catalog_person_id": None,
        }
    if ctx is not None:
        cpid = _resolve_catalog_person_id_for_beneficiary(conn, ctx.version_id, int(beneficiary_id))
        if cpid is not None:
            from modules.nomina.banorte.payment_authority import (
                evaluate_payment_authority,
                load_catalog_authority_bundle,
            )

            person, reconciliation, cat_beneficiary = load_catalog_authority_bundle(
                conn, catalog_person_id=int(cpid), active_version_id=ctx.version_id
            )
            authority = evaluate_payment_authority(
                conn=conn,
                row=row,
                person=person,
                reconciliation=reconciliation,
                beneficiary=cat_beneficiary or beneficiary,
                active_version_id=ctx.version_id,
            )
            return {
                "authority_kind": AUTHORITY_KIND_CATALOG,
                "payment_enabled": bool(authority.get("payment_enabled")),
                "reason_codes": list(authority.get("reason_codes") or []),
                "person": person,
                "reconciliation": reconciliation,
                "beneficiary": cat_beneficiary or beneficiary,
                "catalog_person_id": int(cpid),
            }
    post = evaluate_post_catalog_addition(conn, beneficiary, ctx=ctx)
    return {
        "authority_kind": post.get("authority_kind"),
        "payment_enabled": bool(post.get("payment_enabled")),
        "reason_codes": list(post.get("reason_codes") or []),
        "person": None,
        "reconciliation": None,
        "beneficiary": beneficiary,
        "catalog_person_id": None,
    }


def _post_catalog_search_where(q: str) -> tuple[str, list[Any]]:
    if not q:
        return "", []
    normalized = q.upper()
    digits = _digits(q)
    clauses = ["(b.nombre_normalizado LIKE ? OR b.nombre_original LIKE ?)"]
    params: list[Any] = [f"%{normalized}%", f"%{normalized}%"]
    if digits:
        clauses.append("(b.employee_number_effective LIKE ? OR b.account_number LIKE ?)")
        digit_like = f"%{digits}%"
        params.extend([digit_like, digit_like])
    return f"({' OR '.join(clauses)})", params


def search_post_catalog_additions(
    conn: sqlite3.Connection,
    *,
    q: str = "",
    limit: int = 25,
    offset: int = 0,
    ctx: ActiveCatalogContext | None = None,
) -> tuple[list[dict[str, Any]], int]:
    ctx = ctx or load_active_catalog_context(conn)
    if ctx is None:
        return [], 0
    where_sql, where_params = _post_catalog_search_where(q.strip())
    base_from = """
        FROM nomina_banorte_beneficiaries b
        WHERE b.record_status='ACTIVO'
          AND b.created_at >= ?
          AND b.source_kind IN ('REPORTE_DETALLADO','ALTA_MANUAL')
          AND b.validation_status='IMPORTADO_EXITOSO'
          AND NOT EXISTS (
            SELECT 1 FROM nomina_banorte_catalog_reconciliations rec
            JOIN nomina_banorte_catalog_persons p ON p.id=rec.person_id
            WHERE rec.version_id=? AND rec.beneficiary_id=b.id AND rec.is_current=1
              AND rec.reconciliation_status IN ('AUTO_MATCHED','MANUAL_MATCHED')
              AND p.version_id=?
          )
    """
    params: list[Any] = [ctx.activated_at, ctx.version_id, ctx.version_id]
    if where_sql:
        base_from += f" AND {where_sql}"
        params.extend(where_params)
    total = int(conn.execute(f"SELECT COUNT(*) {base_from}", params).fetchone()[0])
    rows = conn.execute(
        f"""
        SELECT b.*
        {base_from}
        ORDER BY b.employee_number_effective ASC, b.nombre_normalizado ASC, b.id ASC
        LIMIT ? OFFSET ?
        """,
        params + [int(limit), int(offset)],
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        beneficiary = dict(row)
        authority = evaluate_post_catalog_addition(conn, beneficiary, ctx=ctx)
        emp = str(beneficiary.get("employee_number_effective") or "")
        acct = str(beneficiary.get("account_number") or "")
        items.append(
            {
                "authority_kind": AUTHORITY_KIND_POST_CATALOG,
                "catalog_person_id": None,
                "beneficiary_id": int(beneficiary["id"]),
                "employee_number": emp,
                "display_name": str(beneficiary.get("nombre_original") or ""),
                "account_masked": _mask_account(acct),
                "payment_enabled": bool(authority.get("payment_enabled")),
                "reason_codes": list(authority.get("reason_codes") or []),
            }
        )
    return items, total


def _mask_account(account: str) -> str:
    digits = _digits(account)
    if len(digits) <= 4:
        return digits
    return f"{'*' * (len(digits) - 4)}{digits[-4:]}"


def apply_post_catalog_provenance_to_row(
    row: dict[str, Any],
    *,
    beneficiary: dict[str, Any],
    authority: dict[str, Any],
) -> dict[str, Any]:
    from modules.nomina.banorte.beneficiary_material import beneficiary_material_fingerprint

    out = dict(row)
    out["beneficiary_id"] = int(beneficiary["id"])
    out["nombre_recibido"] = str(
        beneficiary.get("nombre_original") or out.get("nombre_recibido") or ""
    )
    out["employee_number_snapshot"] = str(beneficiary.get("employee_number_effective") or "")
    out["account_number_snapshot"] = str(beneficiary.get("account_number") or "")
    out["banco_snapshot"] = "Banorte"
    out["catalog_person_id"] = None
    out["catalog_reconciliation_id"] = None
    out["catalog_match_method"] = MATCH_METHOD_POST_CATALOG
    fp = beneficiary_material_fingerprint(beneficiary)
    out["beneficiary_material_fingerprint_version"] = fp.version
    out["beneficiary_material_fingerprint_seen"] = fp.sha256
    codes = list(authority.get("reason_codes") or [])
    out["catalog_observation_codes_json"] = __import__("json").dumps(codes, ensure_ascii=False)
    if authority.get("payment_enabled"):
        out["match_kind"] = MATCH_METHOD_POST_CATALOG
        if int(out.get("amount_final_cents") or 0) > 0:
            out["included"] = 1
            out["row_state"] = "OK"
    else:
        out["included"] = 0
        out["row_state"] = "NEEDS_REVIEW"
    return out
