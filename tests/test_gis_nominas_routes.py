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


def test_nominas_import_post_with_session_user_row(tmp_path, monkeypatch):
    from io import BytesIO
    from pathlib import Path

    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    fixture = Path("tests/fixtures/nomina_carrier_anon.xlsx").read_bytes()
    res = client.post(
        "/gestion-idse-sua/nominas/import",
        data={"file": (BytesIO(fixture), "Carrier 10 al 16 jul.xlsx")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert res.status_code == 302
    location = res.headers.get("Location") or ""
    assert "/gestion-idse-sua/nominas/import/" in location
    assert "/login" not in location


def test_period_review_previews_clients_before_confirmation(tmp_path, monkeypatch):
    from io import BytesIO

    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    fixture = Path("tests/fixtures/nomina_carrier_anon.xlsx").read_bytes()
    response = client.post(
        "/gestion-idse-sua/nominas/import",
        data={"file": (BytesIO(fixture), "Carrier 10 al 16 jul.xlsx")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    import_id = int((response.headers["Location"].rstrip("/").split("/")[-1]))

    connection = sqlite3.connect(app.config["DATABASE"])
    sheet_id = connection.execute(
        "SELECT id FROM gis_nomina_sheets WHERE import_id = ? ORDER BY sheet_index LIMIT 1",
        (import_id,),
    ).fetchone()[0]
    connection.close()
    client.post(
        f"/gestion-idse-sua/nominas/import/{import_id}/classify",
        data={f"sheet_{sheet_id}": "nomina"},
    )
    monkeypatch.setattr(
        "modules.gestion_idse_sua.routes_nominas.obtener_activos",
        lambda *args, **kwargs: [{"cliente": "CARRIER", "nombre_completo": "PERSONA HC"}],
    )

    html = client.get(f"/gestion-idse-sua/nominas/import/{import_id}/period").get_data(as_text=True)
    assert "Clientes detectados" in html
    assert 'name="clientes" value="CARRIER"' in html
    assert "Confianza" in html
    assert "Periodo inicio" in html

    confirmed = client.post(
        f"/gestion-idse-sua/nominas/sheet/{sheet_id}/period",
        data={
            "fecha_inicio": "10/07/2025",
            "fecha_fin": "16/07/2025",
            "clientes": ["CARRIER"],
        },
        follow_redirects=False,
    )
    assert confirmed.status_code == 302
    assert "/workspace/" in confirmed.headers["Location"]
    connection = sqlite3.connect(app.config["DATABASE"])
    assignments = connection.execute(
        "SELECT DISTINCT cliente_confirmado FROM gis_nomina_workers"
    ).fetchall()
    connection.close()
    assert assignments == [("CARRIER",)]
