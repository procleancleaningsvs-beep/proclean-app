"""Banorte Fase 2.2B — INACTIVO_MANUAL, events, actions, occupied employee numbers."""

from __future__ import annotations

import sqlite3

import pytest

from modules.nomina.banorte.beneficiary_service import (
    BeneficiaryError,
    apply_beneficiary_action,
    create_manual_beneficiary,
    list_beneficiaries,
)
from modules.nomina.banorte.employee_number_service import list_available_employee_numbers
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.db import ensure_nomina_tables


def _db(tmp_path, name="b.db"):
    path = str(tmp_path / name)
    conn = connect(path)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    conn.commit()
    conn.close()
    return path


def test_record_status_allows_inactivo_manual(tmp_path):
    db = _db(tmp_path)
    conn = connect(db)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='nomina_banorte_beneficiaries'"
    ).fetchone()[0]
    assert "INACTIVO_MANUAL" in sql
    conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, employee_number_effective, account_number,
            source_kind, validation_status, record_status,
            imported_at, imported_by, created_at, updated_at
        ) VALUES ('X','X','9','999','ALTA_MANUAL','MANUAL_PENDIENTE_VALIDACION','INACTIVO_MANUAL',
                  't','u','t','t')
        """
    )
    conn.commit()
    conn.close()


def test_events_check_rejects_empty_reason_and_bad_action(tmp_path):
    db = _db(tmp_path)
    conn = connect(db)
    ensure_banorte_tables(conn)
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, employee_number_effective, account_number,
            source_kind, validation_status, record_status,
            imported_at, imported_by, created_at, updated_at
        ) VALUES ('A','A','11','1111','ALTA_MANUAL','MANUAL_PENDIENTE_VALIDACION','ACTIVO','t','u','t','t')
        """
    )
    bid = int(cur.lastrowid)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO nomina_banorte_beneficiary_events (
                beneficiary_id, action, reason, created_by, created_at
            ) VALUES (?, 'deactivate', '   ', 'u', 't')
            """,
            (bid,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO nomina_banorte_beneficiary_events (
                beneficiary_id, action, reason, created_by, created_at
            ) VALUES (?, 'hack', 'motivo', 'u', 't')
            """,
            (bid,),
        )
    conn.rollback()
    conn.close()


def test_deactivate_creates_inactivo_manual(tmp_path):
    db = _db(tmp_path)
    created = create_manual_beneficiary(
        db, "u", nombre="Ana", account="1234567890", confirm_effective_from_account=True
    )
    out = apply_beneficiary_action(
        db, "u", int(created["id"]), action="deactivate", reason="baja operativa"
    )
    assert out["record_status"] == "INACTIVO_MANUAL"
    conn = connect(db)
    ev = conn.execute(
        "SELECT action, reason, new_record_status FROM nomina_banorte_beneficiary_events WHERE beneficiary_id=?",
        (int(created["id"]),),
    ).fetchone()
    conn.close()
    assert ev["action"] == "deactivate"
    assert ev["new_record_status"] == "INACTIVO_MANUAL"
    assert ev["reason"] == "baja operativa"


def test_replace_action_marks_inactivo_reemplazado(tmp_path):
    db = _db(tmp_path)
    created = create_manual_beneficiary(
        db, "u", nombre="Ana", account="1234567890", confirm_effective_from_account=True
    )
    out = apply_beneficiary_action(
        db,
        "u",
        int(created["id"]),
        action="replace",
        reason="cambio de cuenta",
        nombre="Ana Nueva",
        account="1234567891",
        employee_number_effective="1234567891",
    )
    assert out["previous_record_status"] == "INACTIVO_REEMPLAZADO"
    assert out["record_status"] == "ACTIVO"
    assert out["replaces_id"] == int(created["id"])


def test_resolve_duplicate_discard_vs_link(tmp_path):
    db = _db(tmp_path)
    a = create_manual_beneficiary(
        db, "u", nombre="A", account="1000000001", confirm_effective_from_account=True
    )
    b = create_manual_beneficiary(
        db, "u", nombre="B", account="1000000002", confirm_effective_from_account=True
    )
    discard = apply_beneficiary_action(
        db,
        "u",
        int(a["id"]),
        action="resolve_duplicate",
        reason="duplicado descartado",
        winner_id=int(b["id"]),
        loser_mode="discard",
    )
    assert discard["record_status"] == "INACTIVO_MANUAL"
    c = create_manual_beneficiary(
        db, "u", nombre="C", account="1000000003", confirm_effective_from_account=True
    )
    linked = apply_beneficiary_action(
        db,
        "u",
        int(c["id"]),
        action="resolve_duplicate",
        reason="duplicado vinculado",
        winner_id=int(b["id"]),
        loser_mode="link_winner",
    )
    assert linked["record_status"] == "INACTIVO_REEMPLAZADO"


def test_mark_usable_never_importado_exitoso(tmp_path):
    db = _db(tmp_path)
    created = create_manual_beneficiary(
        db, "u", nombre="Ana", account="1234567890", confirm_effective_from_account=True
    )
    out = apply_beneficiary_action(
        db, "u", int(created["id"]), action="mark_usable_manual", reason="revisado en oficina"
    )
    assert out["validation_status"] != "IMPORTADO_EXITOSO"
    assert out["record_status"] == "ACTIVO"
    assert out["manual_effective_from_account"] == 1


def test_list_page_size_is_15(tmp_path):
    db = _db(tmp_path)
    for i in range(20):
        create_manual_beneficiary(
            db,
            "u",
            nombre=f"P{i}",
            account=str(2000000000 + i),
            confirm_effective_from_account=True,
        )
    listing = list_beneficiaries(db, page=1, page_size=50)
    assert listing["page_size"] == 15
    assert len(listing["rows"]) == 15


def test_available_numbers_skip_requested_effective_and_export_snapshot(tmp_path):
    db = _db(tmp_path)
    conn = connect(db)
    ensure_banorte_tables(conn)
    # requested 1, effective 2 (substituted)
    conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, employee_number_requested, employee_number_effective,
            account_number, source_kind, validation_status, record_status,
            imported_at, imported_by, created_at, updated_at
        ) VALUES ('R','R','0000000001','0000000002','9991','ALTA_MANUAL','IMPORTADO_EXITOSO','ACTIVO','t','u','t','t')
        """
    )
    # replaced still occupies effective 3
    conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, employee_number_effective, account_number,
            source_kind, validation_status, record_status,
            imported_at, imported_by, created_at, updated_at
        ) VALUES ('Old','OLD','0000000003','9992','ALTA_MANUAL','IMPORTADO_EXITOSO','INACTIVO_REEMPLAZADO','t','u','t','t')
        """
    )
    # historical export snapshot only: 4
    conn.execute(
        """
        INSERT INTO nomina_banorte_exports (
            created_by, created_at, timezone, layout_date, layout_date_auto,
            consecutive, filename, payment_count, total_cents, capture_origin,
            incidents_json, manual_row_count, aliases_used_json, recommendations_accepted_json,
            warnings_ignored_json, file_sha256, file_size, file_blob, status
        ) VALUES (
            'u','t','America/Monterrey','20260101','20260101','01','x.pag',1,100,'MANUAL_CAPTURE',
            '[]',0,'[]','[]','[]','abc',1,X'00','GENERATED'
        )
        """
    )
    eid = int(conn.execute("SELECT id FROM nomina_banorte_exports").fetchone()[0])
    conn.execute(
        """
        INSERT INTO nomina_banorte_export_items (
            export_id, position, nombre_recibido, beneficiary_id, employee_number_effective,
            account_number, amount_cents, match_kind, validation_status, record_status,
            is_manual_beneficiary, warnings_json, user_decision_json
        ) VALUES (?,1,'H',NULL,'0000000004','9993',100,'MANUAL_SELECT','IMPORTADO_EXITOSO','ACTIVO',0,'[]','{}')
        """,
        (eid,),
    )
    conn.commit()
    conn.close()
    avail = list_available_employee_numbers(db, limit=5)
    nums = set(avail["numbers"])
    assert "0000000001" not in nums
    assert "0000000002" not in nums
    assert "0000000003" not in nums
    assert "0000000004" not in nums
    assert "0000000005" in nums
