"""Banorte Fase 2.1A — hub tabs replace drawers."""

from __future__ import annotations

from pathlib import Path

from flask import Flask, g

from modules.nomina.blueprint import register_nomina
from modules.nomina.banorte.repository import connect
from modules.nomina.db import ensure_nomina_tables


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


def test_hub_uses_tabs_not_drawers(tmp_path):
    app = _app(tmp_path)
    client = app.test_client()
    res = client.get("/nomina/exportaciones/banorte")
    assert res.status_code == 200
    assert res.headers.get("Cache-Control") == "private, no-store"
    html = res.data.decode("utf-8")
    assert "data-banorte-tab" in html
    assert "banorte-drawer" not in html
    assert "data-banorte-drawer" not in html
    assert 'data-banorte-panel="hub"' in html or 'id="banorte-tab-hub"' in html
    assert 'data-banorte-panel="cargar-pagos"' in html


def test_secondary_tabs_hidden_on_load(tmp_path):
    app = _app(tmp_path)
    client = app.test_client()
    html = client.get("/nomina/exportaciones/banorte").data.decode("utf-8")
    for panel in ("import-base", "agregar-benef", "cargar-pagos", "historial"):
        assert f'data-banorte-panel="{panel}" hidden' in html or f'id="banorte-tab-{panel}" hidden' in html


def test_no_separate_beneficiarios_nav_button(tmp_path):
    app = _app(tmp_path)
    html = app.test_client().get("/nomina/exportaciones/banorte").data.decode("utf-8")
    assert "banorte_beneficiarios_page" not in html
    assert "Beneficiarios Banorte" in html or "data-banorte-tab" in html
