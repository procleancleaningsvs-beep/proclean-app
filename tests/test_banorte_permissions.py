from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from flask import Flask, g

from modules.nomina.blueprint import register_nomina
from modules.nomina.db import ensure_nomina_tables


def _make_app(tmp_path: Path, role: str) -> Flask:
    repo = Path(__file__).resolve().parents[1]
    app = Flask(
        __name__,
        template_folder=str(repo / "templates"),
        static_folder=str(repo / "static"),
    )
    app.config.update(
        TESTING=True,
        SECRET_KEY="banorte-test-secret",
        DATABASE=str(tmp_path / "proclean.db"),
    )
    conn = sqlite3.connect(app.config["DATABASE"])
    try:
        ensure_nomina_tables(conn)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, role TEXT, password_hash TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO users (id, username, role, password_hash, created_at) VALUES (1,?,?,?,?)",
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

    return app


@pytest.mark.parametrize("role", ["admin", "nomina"])
def test_banorte_allowed_roles(tmp_path, role):
    app = _make_app(tmp_path, role)
    client = app.test_client()
    res = client.get("/nomina/exportaciones/banorte")
    assert res.status_code == 200
    assert res.headers.get("Cache-Control") == "private, no-store"


@pytest.mark.parametrize("role", ["coordinador", "usuario", "cobranza"])
def test_banorte_blocked_roles(tmp_path, role):
    app = _make_app(tmp_path, role)
    client = app.test_client()
    res = client.get("/nomina/exportaciones/banorte")
    assert res.status_code == 403


def test_csrf_required_on_post(tmp_path):
    app = _make_app(tmp_path, "admin")
    client = app.test_client()
    # Establish session + token via GET
    get = client.get("/nomina/exportaciones/banorte")
    assert get.status_code == 200
    # POST without token
    bad = client.post("/nomina/exportaciones/banorte/paste", json={"names": "A", "amounts": "1"})
    assert bad.status_code == 403
    # Extract token from HTML data-csrf
    html = get.data.decode("utf-8")
    marker = 'data-csrf="'
    start = html.index(marker) + len(marker)
    end = html.index('"', start)
    token = html[start:end]
    ok = client.post(
        "/nomina/exportaciones/banorte/paste",
        json={"csrf_token": token, "names": "Ana\n", "amounts": "10\n"},
        headers={"X-CSRF-Token": token},
    )
    assert ok.status_code == 200
    body = ok.get_json()
    assert body["ok"] is True


def test_get_cannot_generate_export(tmp_path):
    app = _make_app(tmp_path, "admin")
    client = app.test_client()
    res = client.get("/nomina/exportaciones/banorte/export/generate")
    assert res.status_code in {405, 404}
