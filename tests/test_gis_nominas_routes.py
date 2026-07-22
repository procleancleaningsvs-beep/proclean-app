"""GIS Nóminas — rutas del workspace."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash


def _full_app(tmp_path, monkeypatch, role: str = "admin"):
    from modules.nomina.db import ensure_nomina_tables

    db = str(tmp_path / f"gis_nom_{role}.db")
    monkeypatch.setattr("app.DB_PATH", Path(db))
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    ensure_nomina_tables(conn)
    conn.execute(
        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
        ("gisuser", generate_password_hash("secret"), role, "2026-01-01 00:00:00"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("PERF_LOG_ENABLED", "0")
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["DATABASE"] = db
    return app


def _login(client):
    return client.post("/login", data={"username": "gisuser", "password": "secret"}, follow_redirects=True)


def test_nominas_workspace_loads(tmp_path, monkeypatch):
    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    res = client.get("/gestion-idse-sua/nominas")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Nóminas y análisis" in html
    assert "Importar Excel" in html
    assert "Comparativo legado" in html


def test_nominas_not_redirect_to_legacy(tmp_path, monkeypatch):
    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    res = client.get("/gestion-idse-sua/nominas", follow_redirects=False)
    assert res.status_code == 200
    assert "/comparativo" not in (res.headers.get("Location") or "")


def test_hub_points_to_new_nominas(tmp_path, monkeypatch):
    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    html = client.get("/gestion-idse-sua/").get_data(as_text=True)
    assert 'href="/gestion-idse-sua/nominas"' in html
