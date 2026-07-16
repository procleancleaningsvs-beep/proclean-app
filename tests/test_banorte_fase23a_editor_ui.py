"""Fase 2.3A — editor UI contracts (queue, undo, filters, no Colapsar)."""

from __future__ import annotations

from pathlib import Path

from flask import Flask, g

from modules.nomina.banorte.repository import connect
from modules.nomina.blueprint import register_nomina
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


def test_editor_has_sort_filters_and_no_colapsar(tmp_path):
    html = _app(tmp_path).test_client().get("/nomina/exportaciones/banorte").data.decode("utf-8")
    assert "banorte-view-sort" in html
    assert "Nombre A–Z" in html
    assert "Monto mayor–menor" in html
    assert "Colapsar origen" not in html
    assert "banorte-toggle-origin" not in html
    assert "Deshacer último cambio" in html


def test_js_queue_covers_terminal_actions():
    js = (
        Path(__file__).resolve().parents[1] / "static/nomina/exportaciones_banorte_editor.js"
    ).read_text(encoding="utf-8")
    assert "enqueueTerminal" in js
    assert 'type: "abandon"' in js
    assert 'type: "generate"' in js
    assert 'type: "exclude"' in js
    assert "/undo" in js
    assert "clearEditorAfterAbandon" in js
    assert "STALE_MSG" in js
    assert "empSortKey" in js
    assert "banorte-toggle-origin" not in js
