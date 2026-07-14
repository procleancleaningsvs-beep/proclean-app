from __future__ import annotations

from modules.nomina.banorte.prepare_service import (
    EMPLOYEE_NUMBER_SEMANTICS,
    prepare_draft_rows,
    resolve_row_match,
)
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


def test_employee_semantics_documented_as_not_banorte():
    assert EMPLOYEE_NUMBER_SEMANTICS == "PROCLEAN_PARAMETROS_NOT_BANORTE"


def test_account_match_auto_selects(tmp_path):
    db = tmp_path / "m.db"
    conn = connect(db)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    bid = _ben(conn, emp="999", account="5555555555", nombre="ANA DEMO")
    conn.commit()
    conn.close()
    m = resolve_row_match(
        str(db),
        {
            "nombre_recibido": "OTRO NOMBRE",
            "account_number_snapshot": "5555555555",
            "employee_number_snapshot": "111",
        },
    )
    assert m.auto_selected is True
    assert m.selected_id == bid


def test_employee_collision_does_not_auto_assign(tmp_path):
    """Same digits in ProClean emp and Banorte emp for different people → no auto."""
    db = tmp_path / "n.db"
    conn = connect(db)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    _ben(conn, emp="777", account="1111111111", nombre="PERSONA BANORTE")
    conn.commit()
    conn.close()
    m = resolve_row_match(
        str(db),
        {
            "nombre_recibido": "PERSONA PROCLEAN DISTINTA",
            "account_number_snapshot": "9999999999",
            "employee_number_snapshot": "777",
        },
    )
    assert m.auto_selected is False
    assert m.selected_id is None
    assert m.kind in {"EMPLOYEE_SECONDARY", "NONE", "FUZZY_RECOMMENDATION", "AMBIGUOUS"}


def test_bank_rules_other_bank_without_match_excluded(tmp_path):
    db = tmp_path / "o.db"
    conn = connect(db)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    conn.commit()
    conn.close()
    rows = prepare_draft_rows(
        str(db),
        [
            {
                "nombre_recibido": "X",
                "banco_snapshot": "BBVA",
                "account_number_snapshot": "",
                "employee_number_snapshot": "",
                "amount_original_cents": 1000,
                "amount_final_cents": 1000,
                "included": 1,
                "warnings": [],
                "user_decision": {},
            }
        ],
    )
    assert rows[0]["included"] == 0
    assert rows[0]["row_state"] == "EXCLUDED"
