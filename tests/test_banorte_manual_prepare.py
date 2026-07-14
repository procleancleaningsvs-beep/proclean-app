"""Banorte Fase 2.1A — MANUAL_CAPTURE skips bank gate."""

from __future__ import annotations

from modules.nomina.banorte.prepare_service import prepare_draft_rows
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.db import ensure_nomina_tables


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


def test_manual_capture_no_banco_vacio_includes_match(tmp_path):
    db = tmp_path / "manual.db"
    conn = connect(db)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    _ben(conn, emp="42", account="1234567890", nombre="JUAN PEREZ LOPEZ")
    conn.commit()
    conn.close()
    rows = prepare_draft_rows(
        str(db),
        [
            {
                "nombre_recibido": "JUAN PEREZ LOPEZ",
                "banco_snapshot": "",
                "account_number_snapshot": "",
                "employee_number_snapshot": "",
                "amount_original_cents": 50000,
                "amount_final_cents": 50000,
                "included": 1,
                "warnings": [],
                "user_decision": {},
            }
        ],
        origin_kind="MANUAL_CAPTURE",
    )
    assert rows[0]["included"] == 1
    assert rows[0]["row_state"] == "OK"
    assert "banco_vacio" not in (rows[0].get("warnings") or [])


def test_manual_capture_ambiguous_blocked(tmp_path):
    db = tmp_path / "ambig.db"
    conn = connect(db)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    _ben(conn, emp="1", account="1111111111", nombre="MARIA GARCIA")
    _ben(conn, emp="2", account="2222222222", nombre="MARIA GARCIA")
    conn.commit()
    conn.close()
    rows = prepare_draft_rows(
        str(db),
        [
            {
                "nombre_recibido": "MARIA GARCIA",
                "amount_original_cents": 10000,
                "amount_final_cents": 10000,
                "warnings": [],
                "user_decision": {},
            }
        ],
        origin_kind="MANUAL_CAPTURE",
    )
    assert rows[0]["included"] == 0
    assert rows[0]["row_state"] in {"BLOCKED", "NEEDS_REVIEW"}


def test_calculo_run_still_requires_banorte_bank(tmp_path):
    db = tmp_path / "calc.db"
    conn = connect(db)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    _ben(conn, emp="9", account="9999999999", nombre="PEDRO LOPEZ")
    conn.commit()
    conn.close()
    rows = prepare_draft_rows(
        str(db),
        [
            {
                "nombre_recibido": "PEDRO LOPEZ",
                "banco_snapshot": "",
                "amount_original_cents": 10000,
                "amount_final_cents": 10000,
                "warnings": [],
                "user_decision": {},
            }
        ],
        origin_kind="CALCULO_RUN",
    )
    assert rows[0]["included"] == 0
    assert "banco_vacio" in (rows[0].get("warnings") or [])
