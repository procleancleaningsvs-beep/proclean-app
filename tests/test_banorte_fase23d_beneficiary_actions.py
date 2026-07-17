"""Fase 2.3D — acciones sobre CONFLICTO_CRITICO e INACTIVO_REEMPLAZADO."""

from __future__ import annotations

import pytest

from modules.nomina.banorte.beneficiary_service import (
    BeneficiaryError,
    apply_beneficiary_action,
    create_manual_beneficiary,
    list_beneficiary_events,
    replace_beneficiary,
)
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.db import ensure_nomina_tables


def _db(tmp_path):
    path = str(tmp_path / "d1.db")
    conn = connect(path)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    conn.commit()
    conn.close()
    return path


def _insert(db, *, nombre, emp, acct, record, validation="MANUAL_PENDIENTE_VALIDACION"):
    conn = connect(db)
    ensure_banorte_tables(conn)
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, employee_number_effective, account_number,
            source_kind, validation_status, record_status,
            imported_at, imported_by, created_at, updated_at
        ) VALUES (?,?,?,?,'ALTA_MANUAL',?,?, 't','u','t','t')
        """,
        (nombre, nombre.upper(), emp, acct, validation, record),
    )
    conn.commit()
    bid = int(cur.lastrowid)
    conn.close()
    return bid


def test_conflicto_mark_usable_activates_without_importado_exitoso(tmp_path):
    db = _db(tmp_path)
    bid = _insert(
        db, nombre="CONFLICTO UNO", emp="1111111111", acct="1111111111", record="CONFLICTO_CRITICO"
    )
    out = apply_beneficiary_action(
        db, "u", bid, action="mark_usable_manual", reason="revisado en oficina"
    )
    assert out["record_status"] == "ACTIVO"
    assert out["validation_status"] == "MANUAL_PENDIENTE_VALIDACION"
    assert out["validation_status"] != "IMPORTADO_EXITOSO"
    assert out.get("message")
    assert "not_active" not in str(out.get("message")).lower()
    events = list_beneficiary_events(db, bid)
    assert events[0]["action"] == "mark_usable_manual"


def test_conflicto_keep_pending_and_deactivate(tmp_path):
    db = _db(tmp_path)
    bid = _insert(
        db, nombre="CONFLICTO DOS", emp="2222222222", acct="2222222222", record="CONFLICTO_CRITICO"
    )
    out = apply_beneficiary_action(db, "u", bid, action="keep_pending", reason="sigue en revisión")
    assert out["record_status"] == "ACTIVO"
    assert out["validation_status"] == "MANUAL_PENDIENTE_VALIDACION"
    out2 = apply_beneficiary_action(db, "u", bid, action="deactivate", reason="descartar conflicto")
    assert out2["record_status"] == "INACTIVO_MANUAL"


def test_conflicto_invalid_account_rejected(tmp_path):
    db = _db(tmp_path)
    bid = _insert(
        db, nombre="SIN CUENTA", emp="3333333333", acct="", record="CONFLICTO_CRITICO"
    )
    with pytest.raises(BeneficiaryError) as exc:
        apply_beneficiary_action(db, "u", bid, action="mark_usable_manual", reason="x")
    assert exc.value.code in {"account_invalid", "account_required"}


def test_conflicto_duplicate_emp_rejected(tmp_path):
    db = _db(tmp_path)
    create_manual_beneficiary(
        db, "u", nombre="ACTIVO", account="4444444444", confirm_effective_from_account=True
    )
    bid = _insert(
        db, nombre="DUP", emp="4444444444", acct="5555555555", record="CONFLICTO_CRITICO"
    )
    with pytest.raises(BeneficiaryError) as exc:
        apply_beneficiary_action(db, "u", bid, action="mark_usable_manual", reason="x")
    assert exc.value.code == "duplicate_active_employee"


def test_reemplazado_creates_new_version_usable(tmp_path):
    db = _db(tmp_path)
    created = create_manual_beneficiary(
        db, "u", nombre="OLD", account="6666666666", confirm_effective_from_account=True
    )
    replaced = replace_beneficiary(
        db, "u", int(created["id"]), nombre="MID", reason="primera corrección"
    )
    # Force original-style: act on an INACTIVO_REEMPLAZADO tip that has no further active successor
    # Act on MID's predecessor (original) after MID exists — should refuse double successor
    with pytest.raises(BeneficiaryError) as exc:
        apply_beneficiary_action(
            db, "u", int(created["id"]), action="mark_usable_manual", reason="intento doble"
        )
    assert exc.value.code == "already_has_active_successor"

    # Deactivate MID then act on MID as replaced... actually mark MID replaced by acting replace
    apply_beneficiary_action(db, "u", int(replaced["id"]), action="deactivate", reason="baja temporal")
    # Now MID is INACTIVO_MANUAL — use a true INACTIVO_REEMPLAZADO without active successor:
    # create fresh replaced chain tip without active successor by SQL
    old_id = _insert(
        db, nombre="REEMP", emp="7777777777", acct="7777777777", record="INACTIVO_REEMPLAZADO"
    )
    out = apply_beneficiary_action(
        db, "u", old_id, action="mark_usable_manual", reason="reactivar vía versión"
    )
    assert out["id"] != old_id
    assert out["record_status"] == "ACTIVO"
    assert out["validation_status"] != "IMPORTADO_EXITOSO"
    assert out.get("replaces_id") == old_id
    conn = connect(db)
    prev = conn.execute(
        "SELECT record_status FROM nomina_banorte_beneficiaries WHERE id=?", (old_id,)
    ).fetchone()
    conn.close()
    assert prev["record_status"] == "INACTIVO_REEMPLAZADO"


def test_reemplazado_keep_pending_new_version(tmp_path):
    db = _db(tmp_path)
    old_id = _insert(
        db, nombre="PEND", emp="8888888888", acct="8888888888", record="INACTIVO_REEMPLAZADO"
    )
    out = apply_beneficiary_action(db, "u", old_id, action="keep_pending", reason="nueva revisión")
    assert out["id"] != old_id
    assert out["record_status"] == "ACTIVO"
    assert out["validation_status"] == "MANUAL_PENDIENTE_VALIDACION"


def test_reemplazado_deactivate_rejected_business(tmp_path):
    db = _db(tmp_path)
    old_id = _insert(
        db, nombre="NO BAJA", emp="9999999999", acct="9999999999", record="INACTIVO_REEMPLAZADO"
    )
    with pytest.raises(BeneficiaryError) as exc:
        apply_beneficiary_action(db, "u", old_id, action="deactivate", reason="n/a")
    assert exc.value.code == "already_replaced"
    assert "not_active" not in exc.value.code


def test_reason_required_and_no_importado_exitoso(tmp_path):
    db = _db(tmp_path)
    bid = _insert(
        db, nombre="R", emp="1010101010", acct="1010101010", record="CONFLICTO_CRITICO"
    )
    with pytest.raises(BeneficiaryError) as exc:
        apply_beneficiary_action(db, "u", bid, action="keep_pending", reason=" ")
    assert exc.value.code == "reason_required"
