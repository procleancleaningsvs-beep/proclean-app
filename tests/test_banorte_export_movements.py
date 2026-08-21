from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from flask import Flask, g

from modules.nomina.banorte.export_service import DraftPaymentRow, generate_export
from modules.nomina.banorte.repository import connect
from modules.nomina.blueprint import register_nomina
from modules.nomina.db import ensure_nomina_tables


def _make_app(tmp_path: Path, role: str = "admin") -> tuple[Flask, str]:
    repo = Path(__file__).resolve().parents[1]
    app = Flask(
        __name__,
        template_folder=str(repo / "templates"),
        static_folder=str(repo / "static"),
    )
    db_path = str(tmp_path / "proclean.db")
    app.config.update(TESTING=True, SECRET_KEY="movements-test", DATABASE=db_path)
    conn = sqlite3.connect(db_path)
    try:
        ensure_nomina_tables(conn)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users "
            "(id INTEGER PRIMARY KEY, username TEXT, role TEXT, password_hash TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO users (id, username, role, password_hash, created_at) "
            "VALUES (1,?,?,?,?)",
            ("tester", role, "x", "t"),
        )
        conn.commit()
    finally:
        conn.close()

    @app.route("/login")
    def login():
        return "login"

    register_nomina(app)

    @app.before_request
    def _auth():
        g.user = {"id": 1, "username": "tester", "role": role}

    return app, db_path


def _beneficiary(
    conn: sqlite3.Connection,
    *,
    name: str,
    employee_number: str,
    account_number: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original,nombre_normalizado,employee_number_effective,account_number,
            source_kind,validation_status,record_status,imported_at,imported_by,
            created_at,updated_at
        ) VALUES (?,?,?,?, 'ALTA_MANUAL','IMPORTADO_EXITOSO','ACTIVO','t','u','t','t')
        """,
        (name, name.upper(), employee_number, account_number),
    )
    return int(cur.lastrowid)


def _seed_export(db_path: str):
    conn = connect(db_path)
    first_id = _beneficiary(
        conn,
        name="Nombre histórico uno",
        employee_number="0000000011",
        account_number="1321431243",
    )
    second_id = _beneficiary(
        conn,
        name="Nombre histórico dos",
        employee_number="0000000022",
        account_number="1321431244",
    )
    conn.commit()
    conn.close()
    exported = generate_export(
        db_path,
        "tester",
        [
            DraftPaymentRow(
                1,
                "Nombre recibido uno",
                first_id,
                "2700.00",
                "EXACT",
                client_account_number="1321431243",
                client_employee_number="0000000011",
            ),
            DraftPaymentRow(
                4,
                "Nombre recibido dos",
                second_id,
                "19.25",
                "EXACT",
                client_account_number="1321431244",
                client_employee_number="0000000022",
            ),
        ],
        consecutive="07",
        layout_date="20260115",
        confirm_date_override=True,
    )
    return exported, (first_id, second_id)


def _movements_url(export_id: int) -> str:
    return f"/nomina/exportaciones/banorte/historial/{export_id}/movimientos"


@pytest.mark.parametrize("role", ["admin", "nomina"])
def test_allowed_roles_receive_ordered_historical_snapshots(tmp_path, role):
    app, db_path = _make_app(tmp_path, role=role)
    exported, _beneficiary_ids = _seed_export(db_path)

    response = app.test_client().get(_movements_url(exported.export_id))

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    body = response.get_json()
    conn = connect(db_path)
    persisted_layout_date = str(
        conn.execute(
            "SELECT layout_date FROM nomina_banorte_exports WHERE id=?",
            (exported.export_id,),
        ).fetchone()["layout_date"]
    )
    conn.close()
    assert body["ok"] is True
    assert body["export"] == {
        "export_id": exported.export_id,
        "filename": exported.filename,
        "layout_date": persisted_layout_date,
        "payment_count": 2,
        "total_cents": 271925,
    }
    assert body["items"] == [
        {
            "position": 1,
            "historical_name": "Nombre recibido uno",
            "employee_number": "0000000011",
            "account_number": "1321431243",
            "amount_cents": 270000,
        },
        {
            "position": 4,
            "historical_name": "Nombre recibido dos",
            "employee_number": "0000000022",
            "account_number": "1321431244",
            "amount_cents": 1925,
        },
    ]
    assert "beneficiary_id" not in response.get_data(as_text=True)


@pytest.mark.parametrize("role", ["coordinador", "usuario", "cobranza"])
def test_disallowed_roles_receive_403(tmp_path, role):
    app, db_path = _make_app(tmp_path, role=role)
    exported, _beneficiary_ids = _seed_export(db_path)
    assert app.test_client().get(_movements_url(exported.export_id)).status_code == 403


def test_missing_export_is_no_store_404(tmp_path):
    app, _db_path = _make_app(tmp_path)
    response = app.test_client().get(_movements_url(999999))
    assert response.status_code == 404
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.get_json() == {"ok": False, "code": "export_not_found"}


def test_later_beneficiary_changes_do_not_change_historical_response(tmp_path):
    app, db_path = _make_app(tmp_path)
    exported, beneficiary_ids = _seed_export(db_path)
    client = app.test_client()
    before = client.get(_movements_url(exported.export_id)).get_json()

    conn = connect(db_path)
    for offset, beneficiary_id in enumerate(beneficiary_ids, start=1):
        conn.execute(
            """
            UPDATE nomina_banorte_beneficiaries
            SET nombre_original=?, employee_number_effective=?, account_number=?
            WHERE id=?
            """,
            (
                f"Nombre actual distinto {offset}",
                f"{900 + offset:010d}",
                f"{1321439900 + offset:010d}",
                beneficiary_id,
            ),
        )
    conn.commit()
    conn.close()

    after = client.get(_movements_url(exported.export_id)).get_json()
    assert after == before


def test_large_export_returns_every_item_without_truncation(tmp_path):
    app, db_path = _make_app(tmp_path)
    exported, _beneficiary_ids = _seed_export(db_path)
    conn = connect(db_path)
    conn.execute("DELETE FROM nomina_banorte_export_items WHERE export_id=?", (exported.export_id,))
    rows = []
    for position in range(1, 151):
        rows.append(
            (
                exported.export_id,
                position,
                f"Snapshot {position}",
                f"{position:010d}",
                f"{1300000000 + position:010d}",
                100 + position,
                "EXACT",
                "IMPORTADO_EXITOSO",
                "ACTIVO",
                "[]",
                "{}",
            )
        )
    conn.executemany(
        """
        INSERT INTO nomina_banorte_export_items (
            export_id, position, nombre_recibido, beneficiary_id,
            employee_number_effective, account_number, curp, amount_cents,
            match_kind, alias_id, validation_status, record_status,
            is_manual_beneficiary, warnings_json, user_decision_json, calculo_row_id
        ) VALUES (?,?,?,NULL,?,?,NULL,?, ?,NULL,?,?,0,?,?,NULL)
        """,
        rows,
    )
    total_cents = sum(100 + position for position in range(1, 151))
    conn.execute(
        "UPDATE nomina_banorte_exports SET payment_count=150,total_cents=? WHERE id=?",
        (total_cents, exported.export_id),
    )
    conn.commit()
    conn.close()

    body = app.test_client().get(_movements_url(exported.export_id)).get_json()
    assert len(body["items"]) == 150
    assert [item["position"] for item in body["items"]] == list(range(1, 151))


def test_empty_snapshot_returns_empty_items_for_ui_state(tmp_path):
    app, db_path = _make_app(tmp_path)
    exported, _beneficiary_ids = _seed_export(db_path)
    conn = connect(db_path)
    conn.execute("DELETE FROM nomina_banorte_export_items WHERE export_id=?", (exported.export_id,))
    conn.execute(
        "UPDATE nomina_banorte_exports SET payment_count=0,total_cents=0 WHERE id=?",
        (exported.export_id,),
    )
    conn.commit()
    conn.close()

    response = app.test_client().get(_movements_url(exported.export_id))
    assert response.status_code == 200
    assert response.get_json()["items"] == []
