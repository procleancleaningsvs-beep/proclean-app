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
    page_size: int = 15,
    q_name: str = "",
    q_emp: str = "",
    validation_status: str = "",
    record_status: str = "",
) -> dict[str, Any]:
    page = max(1, int(page))
    page_size = 15  # Fase 2.2B contract: exactly 15 per page
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
                   banorte_employee_substituted, replaces_id, updated_at, banorte_comment
            FROM nomina_banorte_beneficiaries
            WHERE {where}
            ORDER BY id DESC
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
            out_rows.append(item)
        return {
            "page": page,
            "page_size": page_size,
            "total": total,
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
        prev_rec = row["record_status"]

        if action == "mark_usable_manual":
            if prev_rec != "ACTIVO":
                raise BeneficiaryError("not_active")
            conn.execute(
                """
                UPDATE nomina_banorte_beneficiaries
                SET validation_status='MANUAL_PENDIENTE_VALIDACION',
                    manual_effective_from_account=1,
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
                "manual_effective_from_account": 1,
            }

        if action == "keep_pending":
            if prev_rec != "ACTIVO":
                raise BeneficiaryError("not_active")
            conn.execute(
                """
                UPDATE nomina_banorte_beneficiaries
                SET validation_status='MANUAL_PENDIENTE_VALIDACION', updated_at=?
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
            }

        if action == "deactivate":
            if prev_rec != "ACTIVO":
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
            return {"id": int(beneficiary_id), "record_status": "INACTIVO_MANUAL"}

        if action == "replace":
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
