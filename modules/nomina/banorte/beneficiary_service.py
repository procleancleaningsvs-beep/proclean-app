"""Beneficiary admin: alta manual + safe versioning (replaces_id)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.banorte.validators import (
    is_valid_account_number,
    is_valid_employee_number,
    normalize_name,
)

TZ = ZoneInfo("America/Monterrey")

BENEFICIARY_ACTION_MESSAGES = {
    "mark_usable_manual": "El beneficiario quedó marcado como utilizable.",
    "keep_pending": "El beneficiario quedó pendiente de validación.",
    "deactivate": "El beneficiario quedó desactivado.",
    "replace": "Se creó una nueva versión activa del beneficiario.",
    "new_version": "Se creó una nueva versión activa del beneficiario.",
    "reason_required": "Indique un motivo.",
    "not_found": "No se encontró el beneficiario.",
    "account_invalid": "La cuenta no cumple el formato permitido.",
    "account_required": "La cuenta no cumple el formato permitido.",
    "employee_invalid": "El número de empleado no cumple el formato permitido.",
    "duplicate_active_account": "Ya existe una cuenta activa igual.",
    "duplicate_active_employee": "El número de empleado ya está ocupado.",
    "already_has_active_successor": (
        "Este registro ya tiene una versión activa. Trabaje sobre esa versión."
    ),
    "already_replaced": "El registro ya fue reemplazado y no se puede desactivar de nuevo.",
    "invalid_action": "La acción no es válida.",
    "not_active": "El registro no admite esta operación en su estado actual.",
    "stale": "El registro fue actualizado por otra operación. Recarga e inténtalo nuevamente.",
}


class BeneficiaryError(Exception):
    def __init__(self, code: str, message: str | None = None):
        super().__init__(code)
        self.code = code
        self.message = message or BENEFICIARY_ACTION_MESSAGES.get(
            code, "No se pudo completar la operación."
        )


def beneficiary_action_message(code: str) -> str:
    return BENEFICIARY_ACTION_MESSAGES.get(code, "No se pudo completar la operación.")


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _digits(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


_SORT_ALLOWLIST: dict[str, str] = {
    "id_desc": "id DESC",
    "name_asc": "nombre_normalizado ASC, id ASC",
    "name_desc": "nombre_normalizado DESC, id DESC",
    "emp_asc": (
        "CASE WHEN employee_number_effective IS NULL "
        "OR trim(employee_number_effective) = '' "
        "OR employee_number_effective GLOB '*[^0-9]*' THEN 1 ELSE 0 END ASC, "
        "CAST(employee_number_effective AS INTEGER) ASC, id ASC"
    ),
    "emp_desc": (
        "CASE WHEN employee_number_effective IS NULL "
        "OR trim(employee_number_effective) = '' "
        "OR employee_number_effective GLOB '*[^0-9]*' THEN 1 ELSE 0 END ASC, "
        "CAST(employee_number_effective AS INTEGER) DESC, id DESC"
    ),
}


def _status_explanation(item: dict[str, Any]) -> str:
    if item.get("last_event_reason"):
        return str(item["last_event_reason"])
    if item.get("replaces_id"):
        return "Sucesor de un registro reemplazado."
    if item.get("record_status") == "INACTIVO_REEMPLAZADO":
        return "Reemplazado por otro registro."
    if item.get("record_status") == "INACTIVO_MANUAL":
        return "Desactivado manualmente."
    if item.get("record_status") == "CONFLICTO_CRITICO":
        return "Conflicto crítico pendiente."
    if item.get("banorte_comment"):
        return str(item["banorte_comment"])
    if item.get("validation_status") == "IMPORTADO_EXITOSO" and item.get("record_status") == "ACTIVO":
        return "Importado y validado por Banorte."
    if item.get("manual_effective_from_account") and item.get("record_status") == "ACTIVO":
        return "Alta manual utilizable, pendiente de validación Banorte."
    return "Pendiente de validación."


def list_beneficiaries(
    db_path: str,
    *,
    page: int = 1,
    page_size: int = 15,
    q_name: str = "",
    q_emp: str = "",
    validation_status: str = "",
    record_status: str = "",
    sort: str = "id_desc",
) -> dict[str, Any]:
    page = max(1, int(page))
    page_size = 15  # contract: exactly 15 per page
    offset = (page - 1) * page_size
    sort_key = str(sort or "id_desc").strip()
    if sort_key not in _SORT_ALLOWLIST:
        raise ValueError("invalid_sort")
    order_sql = _SORT_ALLOWLIST[sort_key]
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
        total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
        if total == 0:
            page = 1
            total_pages = 1
        elif page > total_pages:
            page = total_pages
            offset = (page - 1) * page_size
        rows = conn.execute(
            f"""
            SELECT id, nombre_original, employee_number_effective, account_number,
                   employee_number_requested, validation_status, record_status,
                   manual_effective_from_account, banorte_employee_substituted,
                   replaces_id, updated_at, banorte_comment
            FROM nomina_banorte_beneficiaries
            WHERE {where}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        ).fetchall()
        out_rows = []
        for r in rows:
            item = dict(r)
            last = conn.execute(
                """
                SELECT reason, action, created_at FROM nomina_banorte_beneficiary_events
                WHERE beneficiary_id=? ORDER BY id DESC LIMIT 1
                """,
                (int(item["id"]),),
            ).fetchone()
            item["last_event_reason"] = last["reason"] if last else None
            item["last_event_action"] = last["action"] if last else None
            item["status_explanation"] = _status_explanation(item)
            out_rows.append(item)
        start_index = 0 if total == 0 else offset + 1
        end_index = 0 if total == 0 else offset + len(out_rows)
        return {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "has_previous": page > 1 and total > 0,
            "has_next": page < total_pages and total > 0,
            "start_index": start_index,
            "end_index": end_index,
            "sort": sort_key,
            "rows": out_rows,
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
    employee_number: str | None = None,
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
    if confirm_effective_from_account:
        if len(acct) != 10:
            raise BeneficiaryError("account_must_be_exactly_10_digits")
        emp = acct
        manual_eff = 1
    else:
        emp = _digits(employee_number or "")
        if len(emp) != 10 or emp == "0000000000":
            raise BeneficiaryError("employee_number_must_be_exactly_10_digits")
        manual_eff = 0
    validation = "MANUAL_PENDIENTE_VALIDACION"
    record = "ACTIVO"

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


def list_beneficiary_events(db_path: str, beneficiary_id: int) -> list[dict[str, Any]]:
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        rows = conn.execute(
            """
            SELECT id, beneficiary_id, action, reason,
                   previous_validation_status, new_validation_status,
                   previous_record_status, new_record_status,
                   created_by, created_at, replacement_beneficiary_id
            FROM nomina_banorte_beneficiary_events
            WHERE beneficiary_id=?
            ORDER BY id DESC
            LIMIT 50
            """,
            (int(beneficiary_id),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _insert_event(
    conn,
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


def _active_successor_id(conn, beneficiary_id: int) -> int | None:
    row = conn.execute(
        """
        SELECT id FROM nomina_banorte_beneficiaries
        WHERE replaces_id=? AND record_status='ACTIVO'
        ORDER BY id DESC LIMIT 1
        """,
        (int(beneficiary_id),),
    ).fetchone()
    return int(row["id"]) if row else None


def _validate_account_emp(acct: str, emp: str) -> None:
    if not acct or not is_valid_account_number(acct):
        raise BeneficiaryError("account_invalid")
    if not emp or not is_valid_employee_number(emp) or emp == "0000000000":
        raise BeneficiaryError("employee_invalid")


def _assert_no_active_dupes(
    conn, *, acct: str, emp: str, exclude_id: int | None = None
) -> None:
    q_acct = "SELECT id FROM nomina_banorte_beneficiaries WHERE account_number=? AND record_status='ACTIVO'"
    q_emp = (
        "SELECT id FROM nomina_banorte_beneficiaries "
        "WHERE employee_number_effective=? AND record_status='ACTIVO'"
    )
    params_a: list[Any] = [acct]
    params_e: list[Any] = [emp]
    if exclude_id is not None:
        q_acct += " AND id<>?"
        q_emp += " AND id<>?"
        params_a.append(int(exclude_id))
        params_e.append(int(exclude_id))
    if conn.execute(q_acct, params_a).fetchone():
        raise BeneficiaryError("duplicate_active_account")
    if conn.execute(q_emp, params_e).fetchone():
        raise BeneficiaryError("duplicate_active_employee")


def _insert_successor_version(
    conn,
    old: Any,
    *,
    user: str,
    reason: str,
    nombre: str,
    acct: str,
    emp: str,
    validation_status: str,
    manual_effective_from_account: int,
    action: str,
) -> dict[str, Any]:
    now = _now()
    old_d = dict(old)
    old_id = int(old_d["id"])
    if _active_successor_id(conn, old_id) is not None:
        raise BeneficiaryError("already_has_active_successor")
    if int(old_d.get("replaces_id") or 0) == old_id:
        raise BeneficiaryError("already_has_active_successor")
    _validate_account_emp(acct, emp)
    _assert_no_active_dupes(conn, acct=acct, emp=emp, exclude_id=None)
    # Keep prior as replaced (idempotent if already)
    conn.execute(
        """
        UPDATE nomina_banorte_beneficiaries
        SET record_status='INACTIVO_REEMPLAZADO', replace_reason=?, replaced_by=?,
            replaced_at=COALESCE(replaced_at, ?), updated_at=?
        WHERE id=?
        """,
        (reason.strip(), user, now, now, old_id),
    )
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, curp,
            employee_number_requested, employee_number_effective, account_number,
            source_kind, validation_status, record_status,
            banorte_employee_substituted, manual_effective_from_account,
            imported_at, imported_by, replaces_id, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?, 'ACTIVO',0,?,?,?,?,?,?)
        """,
        (
            nombre,
            normalize_name(nombre),
            old_d["curp"],
            old_d["employee_number_requested"] or emp,
            emp,
            acct,
            "ALTA_MANUAL",
            validation_status,
            int(manual_effective_from_account),
            now,
            user,
            old_id,
            now,
            now,
        ),
    )
    new_id = int(cur.lastrowid)
    _insert_event(
        conn,
        beneficiary_id=old_id,
        action="replace" if action == "replace" else action,
        reason=reason,
        user=user,
        previous_validation_status=old_d["validation_status"],
        new_validation_status=old_d["validation_status"],
        previous_record_status=old_d["record_status"],
        new_record_status="INACTIVO_REEMPLAZADO",
        replacement_beneficiary_id=new_id,
    )
    _insert_event(
        conn,
        beneficiary_id=new_id,
        action=action,
        reason=reason,
        user=user,
        previous_validation_status=None,
        new_validation_status=validation_status,
        previous_record_status=None,
        new_record_status="ACTIVO",
        replacement_beneficiary_id=None,
    )
    return {
        "id": new_id,
        "replaces_id": old_id,
        "record_status": "ACTIVO",
        "validation_status": validation_status,
        "manual_effective_from_account": int(manual_effective_from_account),
        "message": beneficiary_action_message("new_version"),
    }


def apply_beneficiary_action(
    db_path: str,
    user: str,
    beneficiary_id: int,
    *,
    action: str,
    reason: str,
    nombre: str | None = None,
    account: str | None = None,
    employee_number_effective: str | None = None,
    winner_id: int | None = None,
    loser_mode: str | None = None,
) -> dict[str, Any]:
    if not (reason or "").strip():
        raise BeneficiaryError("reason_required")
    action = str(action or "").strip()
    allowed = {
        "mark_usable_manual",
        "keep_pending",
        "deactivate",
        "replace",
        "resolve_duplicate",
    }
    if action not in allowed:
        raise BeneficiaryError("invalid_action")

    now = _now()
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
            (int(beneficiary_id),),
        ).fetchone()
        if row is None:
            raise BeneficiaryError("not_found")
        prev_val = row["validation_status"]
        prev_rec = str(row["record_status"])
        name = (nombre if nombre is not None else row["nombre_original"] or "").strip()
        acct = _digits(account) if account is not None else _digits(row["account_number"])
        emp = (
            _digits(employee_number_effective)
            if employee_number_effective is not None
            else _digits(row["employee_number_effective"])
        )

        if action == "deactivate":
            if prev_rec == "INACTIVO_REEMPLAZADO":
                raise BeneficiaryError("already_replaced")
            if prev_rec not in {"ACTIVO", "CONFLICTO_CRITICO"}:
                raise BeneficiaryError("not_active")
            conn.execute(
                """
                UPDATE nomina_banorte_beneficiaries
                SET record_status='INACTIVO_MANUAL', updated_at=?
                WHERE id=?
                """,
                (now, int(beneficiary_id)),
            )
            _insert_event(
                conn,
                beneficiary_id=int(beneficiary_id),
                action=action,
                reason=reason,
                user=user,
                previous_validation_status=prev_val,
                new_validation_status=prev_val,
                previous_record_status=prev_rec,
                new_record_status="INACTIVO_MANUAL",
            )
            conn.commit()
            return {
                "id": int(beneficiary_id),
                "record_status": "INACTIVO_MANUAL",
                "message": beneficiary_action_message("deactivate"),
            }

        if action in {"mark_usable_manual", "keep_pending", "replace"} and prev_rec == "INACTIVO_REEMPLAZADO":
            validation = "MANUAL_PENDIENTE_VALIDACION"
            manual_eff = 1 if action == "mark_usable_manual" else 0
            if action == "replace":
                manual_eff = 1 if int(row["manual_effective_from_account"] or 0) else 0
                validation = "MANUAL_PENDIENTE_VALIDACION"
            out = _insert_successor_version(
                conn,
                row,
                user=user,
                reason=reason,
                nombre=name or str(row["nombre_original"]),
                acct=acct,
                emp=emp,
                validation_status=validation,
                manual_effective_from_account=manual_eff,
                action=action,
            )
            conn.commit()
            return out

        if action == "mark_usable_manual":
            if prev_rec not in {"ACTIVO", "CONFLICTO_CRITICO"}:
                raise BeneficiaryError("not_active")
            _validate_account_emp(acct, emp)
            _assert_no_active_dupes(conn, acct=acct, emp=emp, exclude_id=int(beneficiary_id))
            conn.execute(
                """
                UPDATE nomina_banorte_beneficiaries
                SET validation_status='MANUAL_PENDIENTE_VALIDACION',
                    record_status='ACTIVO',
                    manual_effective_from_account=1,
                    account_number=?,
                    employee_number_effective=?,
                    nombre_original=?,
                    nombre_normalizado=?,
                    updated_at=?
                WHERE id=?
                """,
                (acct, emp, name, normalize_name(name), now, int(beneficiary_id)),
            )
            _insert_event(
                conn,
                beneficiary_id=int(beneficiary_id),
                action=action,
                reason=reason,
                user=user,
                previous_validation_status=prev_val,
                new_validation_status="MANUAL_PENDIENTE_VALIDACION",
                previous_record_status=prev_rec,
                new_record_status="ACTIVO",
            )
            conn.commit()
            return {
                "id": int(beneficiary_id),
                "validation_status": "MANUAL_PENDIENTE_VALIDACION",
                "record_status": "ACTIVO",
                "manual_effective_from_account": 1,
                "message": beneficiary_action_message("mark_usable_manual"),
            }

        if action == "keep_pending":
            if prev_rec not in {"ACTIVO", "CONFLICTO_CRITICO"}:
                raise BeneficiaryError("not_active")
            conn.execute(
                """
                UPDATE nomina_banorte_beneficiaries
                SET validation_status='MANUAL_PENDIENTE_VALIDACION',
                    record_status='ACTIVO',
                    updated_at=?
                WHERE id=?
                """,
                (now, int(beneficiary_id)),
            )
            _insert_event(
                conn,
                beneficiary_id=int(beneficiary_id),
                action=action,
                reason=reason,
                user=user,
                previous_validation_status=prev_val,
                new_validation_status="MANUAL_PENDIENTE_VALIDACION",
                previous_record_status=prev_rec,
                new_record_status="ACTIVO",
            )
            conn.commit()
            return {
                "id": int(beneficiary_id),
                "validation_status": "MANUAL_PENDIENTE_VALIDACION",
                "record_status": "ACTIVO",
                "message": beneficiary_action_message("keep_pending"),
            }

        if action == "replace":
            if prev_rec != "ACTIVO":
                raise BeneficiaryError("not_active")
            conn.rollback()
            conn.close()
            created = replace_beneficiary(
                db_path,
                user,
                int(beneficiary_id),
                nombre=nombre,
                account=account,
                employee_number_effective=employee_number_effective,
                reason=reason,
            )
            conn = connect(db_path)
            ensure_banorte_tables(conn)
            _insert_event(
                conn,
                beneficiary_id=int(beneficiary_id),
                action=action,
                reason=reason,
                user=user,
                previous_validation_status=prev_val,
                new_validation_status=prev_val,
                previous_record_status=prev_rec,
                new_record_status="INACTIVO_REEMPLAZADO",
                replacement_beneficiary_id=int(created["id"]),
            )
            conn.commit()
            return {
                "id": int(created["id"]),
                "replaces_id": int(beneficiary_id),
                "previous_record_status": "INACTIVO_REEMPLAZADO",
                "record_status": "ACTIVO",
                "message": beneficiary_action_message("replace"),
            }

        # resolve_duplicate
        if winner_id is None:
            raise BeneficiaryError("winner_id_required")
        mode = (loser_mode or "discard").strip()
        if mode not in {"discard", "link_winner"}:
            raise BeneficiaryError("invalid_loser_mode")
        if prev_rec != "ACTIVO":
            raise BeneficiaryError("not_active")
        winner = conn.execute(
            "SELECT id, record_status FROM nomina_banorte_beneficiaries WHERE id=?",
            (int(winner_id),),
        ).fetchone()
        if winner is None or winner["record_status"] != "ACTIVO":
            raise BeneficiaryError("winner_not_active")
        if int(winner_id) == int(beneficiary_id):
            raise BeneficiaryError("winner_same_as_loser")
        if mode == "discard":
            new_rec = "INACTIVO_MANUAL"
            conn.execute(
                """
                UPDATE nomina_banorte_beneficiaries
                SET record_status='INACTIVO_MANUAL', updated_at=?
                WHERE id=?
                """,
                (now, int(beneficiary_id)),
            )
            repl_id = None
        else:
            new_rec = "INACTIVO_REEMPLAZADO"
            conn.execute(
                """
                UPDATE nomina_banorte_beneficiaries
                SET record_status='INACTIVO_REEMPLAZADO', replace_reason=?, replaced_by=?,
                    replaced_at=?, updated_at=?
                WHERE id=?
                """,
                (reason.strip(), user, now, now, int(beneficiary_id)),
            )
            repl_id = int(winner_id)
        _insert_event(
            conn,
            beneficiary_id=int(beneficiary_id),
            action=action,
            reason=reason,
            user=user,
            previous_validation_status=prev_val,
            new_validation_status=prev_val,
            previous_record_status=prev_rec,
            new_record_status=new_rec,
            replacement_beneficiary_id=repl_id,
        )
        conn.commit()
        return {"id": int(beneficiary_id), "record_status": new_rec, "winner_id": int(winner_id)}
    except BeneficiaryError:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass
