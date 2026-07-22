"""Phase 1: Gestión IDSE / SUA hub shell and unified navigation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

from modules.roles_access import (
    can_access_gestion_idse_sua,
    nav_show_comparativo_export_links,
    nav_show_gestion_idse_sua,
    nav_show_imss_exportacion_link,
)


@pytest.mark.parametrize(
    "role",
    ["admin", "nomina", "coordinador", "usuario", "cobranza"],
)
def test_all_authenticated_roles_can_access_hub_flag(role):
    assert can_access_gestion_idse_sua(role) is True
    assert nav_show_gestion_idse_sua(role) is True


def test_anonymous_role_cannot_access_hub_flag():
    assert can_access_gestion_idse_sua("") is False
    assert nav_show_gestion_idse_sua("") is False


def test_legacy_export_nav_flags_hidden():
    for role in ("admin", "usuario", "coordinador", "cobranza", "nomina"):
        assert nav_show_imss_exportacion_link(role) is False
        assert nav_show_comparativo_export_links(role) is False


def _full_app(tmp_path, monkeypatch, role: str = "admin"):
    from modules.nomina.db import ensure_nomina_tables

    db = str(tmp_path / f"gis_{role}.db")
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


def test_hub_anonymous_blocked(tmp_path, monkeypatch):
    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    res = client.get("/gestion-idse-sua/")
    assert res.status_code in {302, 401, 403}
    if res.status_code == 302:
        assert "/login" in (res.headers.get("Location") or "")


@pytest.mark.parametrize("role", ["admin", "nomina", "coordinador", "usuario", "cobranza"])
def test_hub_loads_for_all_roles(tmp_path, monkeypatch, role):
    app = _full_app(tmp_path, monkeypatch, role=role)
    client = app.test_client()
    _login(client)
    res = client.get("/gestion-idse-sua/")
    assert res.status_code == 200, res.get_data(as_text=True)[:500]
    html = res.get_data(as_text=True)
    assert "Gestión IDSE / SUA" in html
    assert "Nóminas y análisis" in html
    assert "Movimientos afiliatorios" in html
    assert "Reportes mensuales" in html
    assert "Sin pendientes por ahora" in html or "No hay pendientes" in html
    assert "filtro-cliente" not in html
    assert "data-gis-filter" not in html


def test_sidebar_unified_and_legacy_links_hidden(tmp_path, monkeypatch):
    import re

    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    html = client.get("/gestion-idse-sua/").get_data(as_text=True)
    assert "/gestion-idse-sua/" in html
    assert "Gestión IDSE / SUA" in html

    nav_hrefs: list[str] = []
    for m in re.finditer(r"<a\s([^>]+)>", html, flags=re.I):
        attrs = m.group(1)
        if "nav-link" not in attrs:
            continue
        hm = re.search(r'href="([^"]+)"', attrs)
        if hm:
            nav_hrefs.append(hm.group(1))

    assert any("/gestion-idse-sua/" in h for h in nav_hrefs)
    assert not any(h.rstrip("/").endswith("/exportacion-imss") or "/exportacion-imss/" in h for h in nav_hrefs)
    assert not any(h.rstrip("/") == "/comparativo" or h.endswith("/comparativo/") for h in nav_hrefs)
    assert not any("reporte-mensual" in h for h in nav_hrefs)
    assert "Comparativo semanal</span>" not in html
    assert ">Reporte mensual</span>" not in html


def test_legacy_routes_still_reachable(tmp_path, monkeypatch):
    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    assert client.get("/exportacion-imss/").status_code == 200
    assert client.get("/comparativo/").status_code == 200
    assert client.get("/comparativo/reporte-mensual").status_code == 200


def test_hub_cards_point_to_new_nominas_workspace(tmp_path, monkeypatch):
    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    html = client.get("/gestion-idse-sua/").get_data(as_text=True)
    assert 'data-gis-area="nominas"' in html
    assert 'data-gis-area="movimientos"' in html
    assert 'data-gis-area="reportes"' in html
    assert "Importar nómina" in html
    assert "Nuevo movimiento" in html
    assert "Crear reporte mensual" in html
    assert "Abrir área completa" in html
    assert 'href="/gestion-idse-sua/nominas"' in html
    assert 'href="/exportacion-imss/"' in html
    assert "/comparativo/reporte-mensual" in html
    assert "gis-mod--locked" not in html


def test_hub_locked_cards_for_nomina_without_comparativo(tmp_path, monkeypatch):
    app = _full_app(tmp_path, monkeypatch, role="nomina")
    client = app.test_client()
    _login(client)
    html = client.get("/gestion-idse-sua/").get_data(as_text=True)
    assert "Nóminas y análisis" in html
    assert 'data-gis-area="nominas"' in html
    assert "gis-mod--locked" in html
    assert "sin acceso" in html.lower() or "no tiene acceso" in html.lower()
    assert 'data-gis-area="movimientos"' in html
    assert 'href="/exportacion-imss/"' in html
    assert "Importar nómina" not in html
    assert "Crear reporte mensual" not in html


def test_area_full_routes(tmp_path, monkeypatch):
    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    r1 = client.get("/gestion-idse-sua/nominas", follow_redirects=False)
    assert r1.status_code == 200
    assert "Nóminas y análisis" in r1.get_data(as_text=True)
    r2 = client.get("/gestion-idse-sua/movimientos", follow_redirects=False)
    assert r2.status_code in {302, 301}
    assert "/exportacion-imss" in (r2.headers.get("Location") or "")
    r3 = client.get("/gestion-idse-sua/reportes", follow_redirects=False)
    assert r3.status_code == 200
    assert "Reportes mensuales" in r3.get_data(as_text=True)


def test_area_nominas_forbidden_for_nomina(tmp_path, monkeypatch):
    app = _full_app(tmp_path, monkeypatch, role="nomina")
    client = app.test_client()
    _login(client)
    assert client.get("/gestion-idse-sua/nominas").status_code == 403


def test_constancias_movimientos_imss_nav_preserved(tmp_path, monkeypatch):
    """Do not remove Nuevo movimiento / Historial under Movimientos IMSS."""
    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    html = client.get("/gestion-idse-sua/").get_data(as_text=True)
    assert "Nuevo movimiento" in html
    assert "Historial de movimientos" in html
