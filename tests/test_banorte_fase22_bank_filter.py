"""Banorte Fase 2.2A — bank filter before draft + amount policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.nomina.banorte.calculo_adapter import build_draft_rows_from_calculo
from modules.nomina.banorte.prepare_service import prepare_draft_rows
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.banorte.validators import is_valid_account_number, is_valid_employee_number
from modules.nomina.db import ensure_nomina_tables
from tests.test_banorte_calculo_list import seed_calculo


def _ben(conn, *, emp: str, account: str, nombre: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, employee_number_effective, account_number,
            source_kind, validation_status, record_status,
            imported_at, imported_by, created_at, updated_at
        ) VALUES (?,?,?,?,'ALTA_MANUAL','IMPORTADO_EXITOSO','ACTIVO','t','u','t','t')
        """,
        (nombre, nombre.upper(), emp, account),
    )
    return int(cur.lastrowid)


def test_validators_canonical_lengths():
    assert is_valid_employee_number("1") is True
    assert is_valid_employee_number("1234567890") is True
    assert is_valid_employee_number("12345678901") is False
    assert is_valid_account_number("123456789012345678") is True
    assert is_valid_account_number("1234567890123456789") is False
    assert is_valid_account_number("") is False


def test_adapter_omits_non_banorte_banks(tmp_path):
    db = tmp_path / "a.db"
    cid = seed_calculo(
        db,
        netos=[100.0, 200.0, 50.0],
        bancos=["BANORTE", "BBVA", "Banorte2"],
    )
    result = build_draft_rows_from_calculo(str(db), cid)
    assert len(result.rows) == 1
    assert result.rows[0].banco_snapshot
    assert str(result.rows[0].banco_snapshot).strip().casefold() == "banorte"
    assert any(o.get("causa") == "banco_no_banorte" for o in result.omitted)


def test_adapter_omits_empty_bank(tmp_path):
    db = tmp_path / "b.db"
    cid = seed_calculo(db, netos=[100.0], bancos=[""])
    # seed first asistencia row is BANORTE by default — override via bancos[0]
    # seed_calculo uses bancos[i] for i>=1; first row from _seed_asistencia is BANORTE
    # Use only one row with empty bank by updating after seed
    conn = connect(db)
    conn.execute("UPDATE nomina_asistencia_rows SET banco=''")
    conn.execute("UPDATE nomina_calculo_rows SET banco=''")
    conn.commit()
    conn.close()
    result = build_draft_rows_from_calculo(str(db), cid)
    assert result.rows == []
    assert any(o.get("causa") == "banco_vacio" or o.get("causa") == "banco_no_banorte" for o in result.omitted)


def test_adapter_banorte_zero_creates_excluded(tmp_path):
    db = tmp_path / "z.db"
    cid = seed_calculo(db, netos=[0.0], bancos=["BANORTE"])
    result = build_draft_rows_from_calculo(str(db), cid)
    assert len(result.rows) == 1
    assert result.rows[0].included == 0
    assert result.rows[0].row_state == "EXCLUDED"
    assert "amount_zero" in result.rows[0].warnings


def test_other_bank_match_does_not_rescue(tmp_path):
    db = str(tmp_path / "r.db")
    conn = connect(db)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    _ben(conn, emp="42", account="123456789012", nombre="ANA DEMO")
    conn.commit()
    conn.close()
    rows = prepare_draft_rows(
        db,
        [
            {
                "nombre_recibido": "ANA DEMO",
                "banco_snapshot": "BBVA",
                "account_number_snapshot": "123456789012",
                "amount_original_cents": 10000,
                "amount_final_cents": 10000,
                "warnings": [],
                "user_decision": {},
            }
        ],
        origin_kind="CALCULO_RUN",
    )
    assert rows[0]["included"] == 0
    assert "otro_banco_con_match_banorte" not in (rows[0].get("warnings") or [])


def test_adapter_negative_and_invalid_are_amount_errors_not_rows(tmp_path):
    db = tmp_path / "n.db"
    cid = seed_calculo(db, netos=[100.0, -25.0], bancos=["BANORTE", "BANORTE"])
    conn = connect(db)
    # Force second row neto to invalid text via calculo update if seed stored decimal
    rows = conn.execute("SELECT id FROM nomina_calculo_rows ORDER BY id").fetchall()
    assert len(rows) >= 2
    conn.execute(
        "UPDATE nomina_calculo_rows SET neto_a_pagar_final=? WHERE id=?",
        ("-25.00", int(rows[1]["id"])),
    )
    conn.commit()
    conn.close()
    result = build_draft_rows_from_calculo(str(db), cid)
    assert len(result.rows) == 1
    assert result.rows[0].amount_final_cents == 10000
    assert any(e.get("causa") == "amount_negative" for e in result.amount_errors)