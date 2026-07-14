"""Beneficiary admin: alta manual + safe versioning (replaces_id)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.banorte.validators import normalize_name

TZ = ZoneInfo("America/Monterrey")


class BeneficiaryError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _digits(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def list_beneficiaries(
    db_path: str,
    *,
    page: int = 1,
    page_size: int = 50,
    q_name: str = "",
    q_emp: str = "",
    validation_status: str = "",
    record_status: str = "",
) -> dict[str, Any]:
    page = max(1, int(page))
    page_size = min(50, max(1, int(page_size)))
    offset = (page - 1) * page_size
    clauses = ["1=1"]
    params: list[Any] = []
    if q_name.strip():
        clauses.append("nombre_normalizado LIKE ?")
        params.append(f"%{normalize_name(q_name)}%")
    if q_emp.strip():
        clauses.append("employee_number_effective LIKE ?")
        params.append(f"%{q_emp.strip()}%")
    if validation_status:
        clauses.append("validation_status=?")
        params.append(validation_status)
    if record_status:
        clauses.append("record_status=?")
        params.append(record_status)
    where = " AND ".join(clauses)
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        total = int(
            conn.execute(
                f"SELECT COUNT(*) AS c FROM nomina_banorte_beneficiaries WHERE {where}",
                params,
            ).fetchone()["c"]
        )
        rows = conn.execute(
            f"""
            SELECT id, nombre_original, employee_number_effective, account_number,
                   validation_status, record_status, manual_effective_from_account,
                   banorte_employee_substituted, replaces_id, updated_at
            FROM nomina_banorte_beneficiaries
            WHERE {where}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        ).fetchall()
        return {
            "page": page,
            "page_size": page_size,
            "total": total,
            "rows": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def search_by_account(db_path: str, account_query: str) -> list[dict[str, Any]]:
    """POST-only search; returns full account for authorized roles."""
    digits = _digits(account_query)
    if len(digits) < 4:
        raise BeneficiaryError("account_query_too_short")
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        rows = conn.execute(
            """
            SELECT id, nombre_original, employee_number_effective, account_number,
                   validation_status, record_status
            FROM nomina_banorte_beneficiaries
            WHERE account_number LIKE ?
            ORDER BY id DESC LIMIT 50
            """,
            (f"%{digits}%",),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def search_by_name(db_path: str, name_query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """POST-only name search for autocomplete; never log full query."""
    q = normalize_name(name_query)
    if len(q) < 3:
        raise BeneficiaryError("name_query_too_short")
    lim = min(50, max(1, int(limit)))
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        rows = conn.execute(
            """
            SELECT id, nombre_original, employee_number_effective, account_number,
                   validation_status, record_status
            FROM nomina_banorte_beneficiaries
            WHERE record_status='ACTIVO' AND nombre_normalizado LIKE ?
            ORDER BY id DESC LIMIT ?
            """,
            (f"%{q}%", lim),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_manual_beneficiary(
    db_path: str,
    user: str,
    *,
    nombre: str,
    account: str,
    confirm_effective_from_account: bool = False,
) -> dict[str, Any]:
    name = (nombre or "").strip()
    if not name:
        raise BeneficiaryError("nombre_required")
    acct = _digits(account)
    if not acct:
        raise BeneficiaryError("account_required")
    if len(acct) > 18:
        raise BeneficiaryError("account_too_long")

    now = _now()
    usable_as_emp = 1 <= len(acct) <= 10
    if usable_as_emp and not confirm_effective_from_account:
        raise BeneficiaryError("confirm_effective_from_account_required")

    if usable_as_emp:
        emp = acct
        validation = "MANUAL_PENDIENTE_VALIDACION"
        manual_eff = 1
        record = "ACTIVO"
    else:
        # cannot form valid employee_number_effective from account — pending blocked
        raise BeneficiaryError("account_cannot_serve_as_employee_number")

    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        dup_a = conn.execute(
            "SELECT id FROM nomina_banorte_beneficiaries WHERE account_number=? AND record_status='ACTIVO'",
            (acct,),
        ).fetchone()
        if dup_a:
            raise BeneficiaryError("duplicate_active_account")
        dup_e = conn.execute(
            "SELECT id FROM nomina_banorte_beneficiaries WHERE employee_number_effective=? AND record_status='ACTIVO'",
            (emp,),
        ).fetchone()
        if dup_e:
            raise BeneficiaryError("duplicate_active_employee")
        cur = conn.execute(
            """
            INSERT INTO nomina_banorte_beneficiaries (
                nombre_original, nombre_normalizado, curp,
                employee_number_requested, employee_number_effective, account_number,
                source_kind, validation_status, record_status,
                banorte_employee_substituted, manual_effective_from_account,
                banorte_comment, imported_at, imported_by, created_at, updated_at
            ) VALUES (?,?,NULL,?,?,?,'ALTA_MANUAL',?,?,0,?,?,?,?,?,?)
            """,
            (
                name,
                normalize_name(name),
                emp,
                emp,
                acct,
                validation,
                record,
                manual_eff,
                f"manual_alta_by:{user}",
                now,
                user,
                now,
                now,
            ),
        )
        conn.commit()
        return {"id": int(cur.lastrowid), "validation_status": validation, "manual_effective_from_account": manual_eff}
    finally:
        conn.close()


def replace_beneficiary(
    db_path: str,
    user: str,
    beneficiary_id: int,
    *,
    nombre: str | None = None,
    account: str | None = None,
    employee_number_effective: str | None = None,
    reason: str,
) -> dict[str, Any]:
    if not (reason or "").strip():
        raise BeneficiaryError("reason_required")
    now = _now()
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        old = conn.execute(
            "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
            (int(beneficiary_id),),
        ).fetchone()
        if old is None:
            raise BeneficiaryError("not_found")
        if old["record_status"] != "ACTIVO":
            raise BeneficiaryError("not_active")

        new_name = (nombre or old["nombre_original"]).strip()
        new_acct = _digits(account) if account is not None else str(old["account_number"])
        new_emp = (
            _digits(employee_number_effective)
            if employee_number_effective is not None
            else str(old["employee_number_effective"])
        )
        if not new_acct or len(new_acct) > 18:
            raise BeneficiaryError("account_invalid")
        if not new_emp or len(new_emp) > 10:
            raise BeneficiaryError("employee_invalid")

        # inactivate old
        conn.execute(
            """
            UPDATE nomina_banorte_beneficiaries
            SET record_status='INACTIVO_REEMPLAZADO', replace_reason=?, replaced_by=?, replaced_at=?, updated_at=?
            WHERE id=?
            """,
            (reason.strip(), user, now, now, int(beneficiary_id)),
        )
        cur = conn.execute(
            """
            INSERT INTO nomina_banorte_beneficiaries (
                nombre_original, nombre_normalizado, curp,
                employee_number_requested, employee_number_effective, account_number,
                source_kind, validation_status, record_status,
                banorte_employee_substituted, manual_effective_from_account,
                imported_at, imported_by, replaces_id, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                new_name,
                normalize_name(new_name),
                old["curp"],
                old["employee_number_requested"],
                new_emp,
                new_acct,
                "ALTA_MANUAL",
                old["validation_status"],
                "ACTIVO",
                0,
                0,
                now,
                user,
                int(beneficiary_id),
                now,
                now,
            ),
        )
        conn.commit()
        return {"id": int(cur.lastrowid), "replaces_id": int(beneficiary_id)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def replacement_history(db_path: str, beneficiary_id: int) -> list[dict[str, Any]]:
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        chain: list[dict[str, Any]] = []
        current = conn.execute(
            "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
            (int(beneficiary_id),),
        ).fetchone()
        if current is None:
            return []
        # walk backward via replaces_id
        node = dict(current)
        seen: set[int] = set()
        while node and int(node["id"]) not in seen:
            seen.add(int(node["id"]))
            chain.append(
                {
                    "id": int(node["id"]),
                    "nombre_original": node["nombre_original"],
                    "account_number": node["account_number"],
                    "employee_number_effective": node["employee_number_effective"],
                    "record_status": node["record_status"],
                    "replace_reason": node.get("replace_reason"),
                    "replaced_by": node.get("replaced_by"),
                    "replaced_at": node.get("replaced_at"),
                    "replaces_id": node.get("replaces_id"),
                }
            )
            rid = node.get("replaces_id")
            if rid is None:
                break
            row = conn.execute(
                "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
                (int(rid),),
            ).fetchone()
            node = dict(row) if row else None
        return chain
    finally:
        conn.close()
