"""Persistent Banorte beneficiary staging batches (MANUAL / REPORTE_DETALLADO)."""

from __future__ import annotations

import hashlib
import io
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

from modules.nomina.banorte.employee_number_service import collect_occupied_employee_numbers
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.banorte.validators import (
    digits_only,
    extract_identifier_cell,
    normalize_header,
    normalize_name,
    safe_upload_filename,
)

TZ = ZoneInfo("America/Monterrey")


class BatchStaleError(Exception):
    def __init__(self, batch_id: int, current_revision: int):
        super().__init__("batch_stale")
        self.batch_id = batch_id
        self.current_revision = current_revision
        self.code = "batch_stale"


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _digits(value: Any) -> str:
    return digits_only(value)


def get_batch(db_path: str, batch_id: int) -> dict[str, Any] | None:
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        b = conn.execute(
            "SELECT * FROM nomina_banorte_beneficiary_batches WHERE id=?",
            (int(batch_id),),
        ).fetchone()
        if b is None:
            return None
        rows = conn.execute(
            """
            SELECT * FROM nomina_banorte_beneficiary_batch_rows
            WHERE batch_id=? ORDER BY position ASC
            """,
            (int(batch_id),),
        ).fetchall()
        payload = dict(b)
        payload["rows"] = [dict(r) for r in rows]
        return payload
    finally:
        conn.close()


def create_batch(
    db_path: str,
    user: str,
    *,
    origin_kind: str,
    source_filename: str | None = None,
    source_sha256: str | None = None,
    prior_batch_id: int | None = None,
) -> dict[str, Any]:
    if origin_kind not in {"MANUAL", "REPORTE_DETALLADO"}:
        raise ValueError("invalid_origin_kind")
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        existing = conn.execute(
            """
            SELECT id FROM nomina_banorte_beneficiary_batches
            WHERE created_by=? AND origin_kind=? AND status='OPEN'
            """,
            (user, origin_kind),
        ).fetchone()
        if existing is not None:
            return get_batch(db_path, int(existing["id"]))  # type: ignore[return-value]
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO nomina_banorte_beneficiary_batches (
                origin_kind, status, revision, source_filename, source_sha256,
                prior_batch_id, created_by, created_at, updated_at
            ) VALUES (?, 'OPEN', 1, ?, ?, ?, ?, ?, ?)
            """,
            (origin_kind, source_filename, source_sha256, prior_batch_id, user, now, now),
        )
        conn.commit()
        return get_batch(db_path, int(cur.lastrowid))  # type: ignore[return-value]
    finally:
        conn.close()


def _bump_batch(conn, batch_id: int, expected_revision: int, user: str) -> None:
    now = _now()
    cur = conn.execute(
        """
        UPDATE nomina_banorte_beneficiary_batches
        SET revision = revision + 1, updated_at=?, created_by=created_by
        WHERE id=? AND revision=? AND status='OPEN'
        """,
        (now, int(batch_id), int(expected_revision)),
    )
    # touch updated_by-less: keep created_by; only revision/updated_at
    if cur.rowcount != 1:
        row = conn.execute(
            "SELECT revision FROM nomina_banorte_beneficiary_batches WHERE id=?",
            (int(batch_id),),
        ).fetchone()
        raise BatchStaleError(int(batch_id), int(row["revision"]) if row else expected_revision)
    _ = user  # reserved for future audit column


def add_batch_row(
    db_path: str,
    batch_id: int,
    user: str,
    expected_revision: int,
    *,
    nombre: str,
    cuenta: str,
    employee_number: str | None = None,
    use_account_as_employee_number: bool = False,
    comment: str | None = None,
    source_row: int | None = None,
) -> dict[str, Any]:
    name = (nombre or "").strip()
    acct = _digits(cuenta)
    use_acct = bool(use_account_as_employee_number)
    if use_acct:
        if len(acct) != 10:
            raise ValueError("account_must_be_exactly_10_digits")
        emp = acct
    else:
        emp = _digits(employee_number or "")
        if emp and (len(emp) != 10 or emp == "0000000000"):
            raise ValueError("employee_number_must_be_exactly_10_digits")
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        conn.execute("BEGIN IMMEDIATE")
        _bump_batch(conn, batch_id, expected_revision, user)
        pos = int(
            conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS p FROM nomina_banorte_beneficiary_batch_rows WHERE batch_id=?",
                (int(batch_id),),
            ).fetchone()["p"]
        )
        now = _now()
        state = "OK" if name and acct and emp else "DRAFT"
        conn.execute(
            """
            INSERT INTO nomina_banorte_beneficiary_batch_rows (
                batch_id, position, nombre, cuenta, employee_number,
                use_account_as_employee_number, comment, row_state,
                source_row, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(batch_id),
                pos,
                name or None,
                acct or None,
                emp or None,
                1 if use_acct else 0,
                comment,
                state,
                source_row,
                now,
                now,
            ),
        )
        conn.commit()
    except BatchStaleError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_batch(db_path, batch_id)  # type: ignore[return-value]


def delete_batch_row(
    db_path: str,
    batch_id: int,
    row_id: int,
    user: str,
    expected_revision: int,
) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        conn.execute("BEGIN IMMEDIATE")
        _bump_batch(conn, batch_id, expected_revision, user)
        cur = conn.execute(
            "DELETE FROM nomina_banorte_beneficiary_batch_rows WHERE id=? AND batch_id=?",
            (int(row_id), int(batch_id)),
        )
        if cur.rowcount != 1:
            raise ValueError("batch_row_not_found")
        conn.commit()
    except BatchStaleError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_batch(db_path, batch_id)  # type: ignore[return-value]


def abandon_batch(
    db_path: str,
    batch_id: int,
    user: str,
    expected_revision: int,
) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        conn.execute("BEGIN IMMEDIATE")
        now = _now()
        cur = conn.execute(
            """
            UPDATE nomina_banorte_beneficiary_batches
            SET status='ABANDONED', revision = revision + 1, updated_at=?
            WHERE id=? AND revision=? AND status='OPEN'
            """,
            (now, int(batch_id), int(expected_revision)),
        )
        if cur.rowcount != 1:
            row = conn.execute(
                "SELECT revision FROM nomina_banorte_beneficiary_batches WHERE id=?",
                (int(batch_id),),
            ).fetchone()
            raise BatchStaleError(int(batch_id), int(row["revision"]) if row else expected_revision)
        conn.commit()
        _ = user
    except BatchStaleError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_batch(db_path, batch_id)  # type: ignore[return-value]


def confirm_batch(
    db_path: str,
    batch_id: int,
    user: str,
    expected_revision: int,
) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        conn.execute("BEGIN IMMEDIATE")
        batch = conn.execute(
            "SELECT * FROM nomina_banorte_beneficiary_batches WHERE id=?",
            (int(batch_id),),
        ).fetchone()
        if batch is None:
            raise ValueError("batch_not_found")
        if batch["status"] != "OPEN":
            raise ValueError("batch_not_open")
        if int(batch["revision"]) != int(expected_revision):
            raise BatchStaleError(int(batch_id), int(batch["revision"]))
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM nomina_banorte_beneficiary_batch_rows WHERE batch_id=? ORDER BY position",
                (int(batch_id),),
            )
        ]
        if not rows:
            raise ValueError("batch_empty")
        occupied = collect_occupied_employee_numbers(conn)
        seen_emp: set[str] = set()
        seen_acct: set[str] = set()
        errors: list[dict[str, Any]] = []
        for r in rows:
            rid = int(r["id"])
            name = (r.get("nombre") or "").strip()
            acct = _digits(r.get("cuenta"))
            emp = _digits(r.get("employee_number"))
            use_acct = int(r.get("use_account_as_employee_number") or 0) == 1
            code = None
            msg = None
            if not name:
                code, msg = "nombre_required", "Nombre obligatorio."
            elif not acct:
                code, msg = "account_required", "Cuenta obligatoria."
            elif use_acct and len(acct) != 10:
                code, msg = "account_must_be_exactly_10_digits", "La cuenta debe tener exactamente 10 dígitos."
            elif len(emp) != 10 or emp == "0000000000":
                code, msg = "employee_number_must_be_exactly_10_digits", "El número debe tener exactamente 10 dígitos."
            elif use_acct and emp != acct:
                code, msg = "employee_account_mismatch", "El número debe coincidir con la cuenta."
            elif emp in seen_emp or emp in occupied:
                code, msg = "duplicate_employee_number", "Número de empleado no disponible."
            elif acct in seen_acct:
                code, msg = "duplicate_account_in_batch", "Cuenta duplicada en el lote."
            else:
                dup = conn.execute(
                    "SELECT id FROM nomina_banorte_beneficiaries WHERE account_number=? AND record_status='ACTIVO'",
                    (acct,),
                ).fetchone()
                if dup:
                    code, msg = "duplicate_active_account", "Ya existe una cuenta activa igual."
            if code:
                errors.append({"row_id": rid, "error_code": code, "error_message": msg})
            else:
                seen_emp.add(emp)
                seen_acct.add(acct)
                occupied.add(emp.zfill(10) if len(emp) <= 10 else emp)
        if errors:
            for e in errors:
                conn.execute(
                    """
                    UPDATE nomina_banorte_beneficiary_batch_rows
                    SET row_state='ERROR', error_code=?, error_message=?, updated_at=?
                    WHERE id=?
                    """,
                    (e["error_code"], e["error_message"], _now(), e["row_id"]),
                )
            conn.commit()
            raise ValueError("batch_row_errors:" + ",".join(e["error_code"] for e in errors))

        # Insert outside nested transactions via create_manual on same db — use direct insert
        now = _now()
        for r in rows:
            name = str(r["nombre"]).strip()
            acct = _digits(r["cuenta"])
            emp = _digits(r["employee_number"])
            manual_eff = 1 if int(r.get("use_account_as_employee_number") or 0) == 1 else 0
            conn.execute(
                """
                INSERT INTO nomina_banorte_beneficiaries (
                    nombre_original, nombre_normalizado, curp,
                    employee_number_requested, employee_number_effective, account_number,
                    source_kind, validation_status, record_status,
                    banorte_employee_substituted, manual_effective_from_account,
                    banorte_comment, imported_at, imported_by, created_at, updated_at
                ) VALUES (?,?,NULL,?,?,?,?, 'MANUAL_PENDIENTE_VALIDACION','ACTIVO',0,?,?,?,?,?,?)
                """,
                (
                    name,
                    normalize_name(name),
                    emp,
                    emp,
                    acct,
                    "ALTA_MANUAL" if batch["origin_kind"] == "MANUAL" else "REPORTE_DETALLADO",
                    manual_eff,
                    f"batch:{batch_id}",
                    now,
                    user,
                    now,
                    now,
                ),
            )
        cur = conn.execute(
            """
            UPDATE nomina_banorte_beneficiary_batches
            SET status='CONFIRMED', revision = revision + 1, updated_at=?, confirmed_at=?
            WHERE id=? AND revision=? AND status='OPEN'
            """,
            (now, now, int(batch_id), int(expected_revision)),
        )
        if cur.rowcount != 1:
            raise BatchStaleError(int(batch_id), int(expected_revision))
        conn.commit()
    except BatchStaleError:
        conn.rollback()
        raise
    except ValueError:
        # row errors already committed with ERROR markers for UI
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_batch(db_path, batch_id)  # type: ignore[return-value]


def prepare_reporte_batch(
    db_path: str,
    user: str,
    file_bytes: bytes,
    filename: str,
    *,
    confirm_reimport: bool = False,
) -> dict[str, Any]:
    """Parse Reporte Detallado into a staging batch — does not insert beneficiaries."""
    from modules.nomina.banorte.import_service import _cell, _existing_sha_batch, _find_header_map

    safe_name = safe_upload_filename(filename)
    if not safe_name.lower().endswith(".xlsx"):
        raise ValueError("invalid_extension")
    sha = hashlib.sha256(file_bytes).hexdigest()
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        prior_id = _existing_sha_batch(conn, sha)
        prior_at = None
        if prior_id is not None:
            prow = conn.execute(
                "SELECT created_at FROM nomina_banorte_import_batches WHERE id=?",
                (int(prior_id),),
            ).fetchone()
            prior_at = prow["created_at"] if prow else None
        if prior_id is not None and not confirm_reimport:
            return {
                "ok": False,
                "code": "duplicate_file_confirmation_required",
                "message": (
                    "Este reporte ya fue procesado anteriormente. "
                    "¿Deseas preparar un nuevo lote con el mismo archivo?"
                ),
                "prior": {"imported_at": prior_at, "batch_ref": f"IMP-{prior_id}"},
            }
    finally:
        conn.close()

    wb = load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb[wb.sheetnames[0]]
    header_row, colmap = _find_header_map(
        ws, {"NUMERO DE EMPLEADO", "NOMBRE DEL EMPLEADO", "NUMERO DE CUENTA"}
    )

    if confirm_reimport:
        existing = create_batch(db_path, user, origin_kind="REPORTE_DETALLADO")
        if existing and existing.get("status") == "OPEN":
            abandon_batch(db_path, int(existing["id"]), user, int(existing["revision"]))

    batch = create_batch(
        db_path,
        user,
        origin_kind="REPORTE_DETALLADO",
        source_filename=safe_name,
        source_sha256=sha,
        prior_batch_id=prior_id,
    )
    if (
        not confirm_reimport
        and batch.get("source_sha256") == sha
        and batch.get("rows")
    ):
        return {"ok": True, "batch": batch, "idempotent": True}

    # If reusing empty OPEN batch, stamp source metadata
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        conn.execute(
            """
            UPDATE nomina_banorte_beneficiary_batches
            SET source_filename=?, source_sha256=?, prior_batch_id=?, updated_at=?
            WHERE id=? AND status='OPEN'
            """,
            (safe_name, sha, prior_id, _now(), int(batch["id"])),
        )
        conn.commit()
    finally:
        conn.close()
    batch = get_batch(db_path, int(batch["id"]))  # type: ignore[assignment]
    assert batch is not None

    rev = int(batch["revision"])
    bid = int(batch["id"])
    for r in range(header_row + 1, (ws.max_row or header_row) + 1):
        if not any(ws.cell(r, c).value for c in range(1, (ws.max_column or 1) + 1)):
            continue
        estatus = _cell(ws, r, colmap.get("ESTATUS"))
        if normalize_header(estatus) != "EXITOSO":
            continue
        nombre = _cell(ws, r, colmap.get("NOMBRE DEL EMPLEADO"))
        emp_cell = ws.cell(r, colmap["NUMERO DE EMPLEADO"])
        acct_cell = ws.cell(r, colmap["NUMERO DE CUENTA"])
        emp, _emp_err = extract_identifier_cell(emp_cell.value, number_format=emp_cell.number_format)
        acct, _acct_err = extract_identifier_cell(acct_cell.value, number_format=acct_cell.number_format)
        if not nombre or not emp or not acct:
            continue
        batch = add_batch_row(
            db_path,
            bid,
            user,
            rev,
            nombre=str(nombre),
            cuenta=str(acct),
            employee_number=str(emp),
            use_account_as_employee_number=False,
            source_row=r,
        )
        rev = int(batch["revision"])
    return {"ok": True, "batch": batch, "idempotent": False}
