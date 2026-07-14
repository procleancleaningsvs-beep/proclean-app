"""Banorte Fase 2.1B — POST search-name (no PII in URL)."""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask, g

from modules.nomina.banorte.beneficiary_service import BeneficiaryError, search_by_name
from modules.nomina.banorte.repository import connect
from modules.nomina.blueprint import register_nomina
from modules.nomina.db import ensure_nomina_tables


def _app(tmp_path):
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
        ("tester", "admin", "x", "t"),
    )
    conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, employee_number_effective, account_number,
            source_kind, validation_status, record_status,
            imported_at, imported_by, created_at, updated_at
        ) VALUES ('ANA GARCIA LOPEZ','ANA GARCIA LOPEZ','1','1111111111','ALTA_MANUAL','IMPORTADO_EXITOSO','ACTIVO','t','u','t','t')
        """
    )
    conn.commit()
    conn.close()

    @app.route("/login")
    def login():
        return "login"

    register_nomina(app)

    @app.before_request
    def _auth():
        g.user = {"id": 1, "username": "tester", "role": "admin"}

    return app


def test_search_by_name_service(tmp_path):
    db = str(tmp_path / "n.db")
    conn = connect(db)
    ensure_nomina_tables(conn)
    conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, employee_number_effective, account_number,
            source_kind, validation_status, record_status,
            imported_at, imported_by, created_at, updated_at
        ) VALUES ('PEDRO SANCHEZ','PEDRO SANCHEZ','2','2222222222','ALTA_MANUAL','IMPORTADO_EXITOSO','ACTIVO','t','u','t','t')
        """
    )
    conn.commit()
    conn.close()
    rows = search_by_name(db, "PEDRO SAN", limit=10)
    assert len(rows) == 1
    assert rows[0]["nombre_original"] == "PEDRO SANCHEZ"


def test_search_name_post_route_no_query_string(tmp_path):
    app = _app(tmp_path)
    client = app.test_client()
    get = client.get("/nomina/exportaciones/banorte")
    html = get.data.decode("utf-8")
    marker = 'data-csrf="'
    start = html.index(marker) + len(marker)
    token = html[start:html.index('"', start)]
    res = client.post(
        "/nomina/exportaciones/banorte/beneficiarios/search-name",
        json={"q": "ANA GAR", "limit": 5, "csrf_token": token},
        headers={"X-CSRF-Token": token},
    )
    assert res.status_code == 200
    assert res.headers.get("Cache-Control") == "private, no-store"
    data = res.get_json()
    assert data["ok"] is True
    assert len(data["rows"]) >= 1


def test_search_name_too_short(tmp_path):
    db = str(tmp_path / "s.db")
    conn = connect(db)
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()
    with pytest.raises(BeneficiaryError) as ei:
        search_by_name(db, "AB")
    assert ei.value.code == "name_query_too_short"
