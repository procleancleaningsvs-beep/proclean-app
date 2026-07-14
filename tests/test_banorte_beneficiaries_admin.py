from __future__ import annotations

from modules.nomina.banorte.beneficiary_service import (
    BeneficiaryError,
    create_manual_beneficiary,
    list_beneficiaries,
    replace_beneficiary,
)
from modules.nomina.banorte.repository import connect
from modules.nomina.db import ensure_nomina_tables
from modules.nomina.blueprint import register_nomina
from flask import Flask, g
from pathlib import Path
import pytest


def test_list_page_size_max_50(tmp_path):
    db = str(tmp_path / "b.db")
    conn = connect(db)
    ensure_nomina_tables(conn)
    for i in range(55):
        conn.execute(
            """
            INSERT INTO nomina_banorte_beneficiaries (
                nombre_original, nombre_normalizado, employee_number_effective, account_number,
                source_kind, validation_status, record_status,
                imported_at, imported_by, created_at, updated_at
            ) VALUES (?,?,?,?, 'ALTA_MANUAL','IMPORTADO_EXITOSO','ACTIVO','t','u','t','t')
            """,
            (f"N{i}", f"N{i}", str(1000 + i), str(2000000000 + i)),
        )
    conn.commit()
    conn.close()
    page1 = list_beneficiaries(db, page=1, page_size=50)
    assert page1["page_size"] == 50
    assert len(page1["rows"]) == 50
    assert page1["total"] == 55


def test_alta_account_as_emp_requires_confirm(tmp_path):
    db = str(tmp_path / "c.db")
    conn = connect(db)
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()
    with pytest.raises(BeneficiaryError):
        create_manual_beneficiary(db, "u", nombre="ANA DEMO", account="1234567890", confirm_effective_from_account=False)
    out = create_manual_beneficiary(
        db, "u", nombre="ANA DEMO", account="1234567890", confirm_effective_from_account=True
    )
    assert out["manual_effective_from_account"] == 1


def test_alta_account_too_long_blocked(tmp_path):
    db = str(tmp_path / "d.db")
    conn = connect(db)
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()
    with pytest.raises(BeneficiaryError) as ei:
        create_manual_beneficiary(
            db, "u", nombre="ANA", account="123456789012345", confirm_effective_from_account=True
        )
    assert ei.value.code == "account_cannot_serve_as_employee_number"


def test_replace_versions(tmp_path):
    db = str(tmp_path / "e.db")
    conn = connect(db)
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()
    created = create_manual_beneficiary(
        db, "u", nombre="ANA DEMO", account="5555555555", confirm_effective_from_account=True
    )
    nxt = replace_beneficiary(
        db, "u", created["id"], account="5555555556", employee_number_effective="5555555556", reason="cambio cuenta"
    )
    assert nxt["replaces_id"] == created["id"]


def _app(tmp_path, role="admin"):
    repo = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(repo / "templates"), static_folder=str(repo / "static"))
    app.config.update(TESTING=True, SECRET_KEY="x", DATABASE=str(tmp_path / "app.db"))
    conn = connect(app.config["DATABASE"])
    ensure_nomina_tables(conn)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, role TEXT, password_hash TEXT, created_at TEXT)"
    )
    conn.execute(
        "INSERT INTO users (id, username, role, password_hash, created_at) VALUES (1,?,?,?,?)",
        ("tester", role, "x", "t"),
    )
    conn.commit()
    conn.close()

    @app.route("/login")
    def login():
        return "login"

    register_nomina(app)

    @app.before_request
    def _auth():
        g.user = {"id": 1, "username": "tester", "role": role}

    return app


def test_index_extra_head_and_no_store(tmp_path):
    app = _app(tmp_path)
    client = app.test_client()
    res = client.get("/nomina/exportaciones/banorte")
    assert res.status_code == 200
    assert res.headers.get("Cache-Control") == "private, no-store"
    html = res.data.decode("utf-8")
    assert "exportaciones_banorte.css" in html
    assert "Nóminas listas para exportar a Banorte" in html
    # file inputs only inside drawers
    assert 'id="drawer-import-altas"' in html
    assert "data-banorte-drawer" in html


def test_historial_no_store(tmp_path):
    app = _app(tmp_path)
    client = app.test_client()
    res = client.get("/nomina/exportaciones/banorte/historial")
    assert res.status_code == 200
    assert res.headers.get("Cache-Control") == "private, no-store"
    assert "Origen" in res.data.decode("utf-8")
