from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask, g

from modules.nomina.banorte.catalog_parser import CATALOG_HEADER_V1
from modules.nomina.banorte.catalog_reconciliation import pre_reconcile_catalog_version
from modules.nomina.banorte.catalog_search_cursor import (
    CatalogSearchCursorError,
    issue_catalog_search_cursor,
    parse_catalog_search_cursor,
)
from modules.nomina.banorte.catalog_search_service import search_catalog_sidebar
from modules.nomina.banorte.catalog_service import analyze_catalog_version, stage_catalog_version
from modules.nomina.banorte.schema import ensure_banorte_tables
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
        SECRET_KEY="sidebar-test-secret",
        DATABASE=str(tmp_path / f"{role}.db"),
    )
    import sqlite3

    conn = sqlite3.connect(app.config["DATABASE"])
    ensure_nomina_tables(conn)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, role TEXT, password_hash TEXT, created_at TEXT)"
    )
    conn.execute(
        "INSERT INTO users (id,username,role,password_hash,created_at) VALUES (1,'tester',?,'x','t')",
        (role,),
    )
    conn.commit()
    conn.close()
    register_nomina(app)

    @app.before_request
    def _auth():
        g.user = {"id": 1, "username": "tester", "role": role}

    return app


def _row(employee: str = "0000000001", name: str = "PERSONA SIDEBAR") -> list[str]:
    return [
        employee,
        name,
        "01/01/2026",
        "20/08/2026",
        "ADMIN",
        "01/01/1990",
        "SID900101AA1",
        "1000",
        "900",
        "NUEVO LEON",
        "01/01/2020",
        "SEMANAL",
        "NUEVO LEON",
        "CUENTA BANORTE",
        "1111111111",
        "0",
        "ALTA",
        "INDIVIDUAL",
        "APLICADO",
        "REGISTRO ACEPTADO",
        "ADMIN",
        "",
        "",
        "",
    ]


def _payload(name: str = "PERSONA SIDEBAR") -> bytes:
    return "\n".join(
        [
            "FECHA: 20/ago./2026",
            "EMISORA: 67059 EMPRESA SINTETICA",
            "",
            "|".join(CATALOG_HEADER_V1) + "|",
            "|".join(_row(name=name)) + "|",
        ]
    ).encode("utf-8")


def _seed_active_catalog(db_path: str) -> int:
    import sqlite3

    conn = sqlite3.connect(db_path)
    ensure_banorte_tables(conn)
    conn.commit()
    conn.close()
    staged = stage_catalog_version(db_path, raw=_payload(), filename="sidebar.txt", actor="admin")
    analyze_catalog_version(db_path, staged["id"], actor="admin")
    pre_reconcile_catalog_version(db_path, staged["id"], actor="admin")
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE nomina_banorte_catalog_versions SET status='ACTIVE' WHERE id=?",
        (staged["id"],),
    )
    conn.commit()
    conn.close()
    return int(staged["id"])


def test_cursor_roundtrip_and_tamper():
    secret = "sidebar-test-secret"
    token = issue_catalog_search_cursor(
        secret_key=secret, version_id=3, offset=10, sort="employee_asc", limit=25
    )
    parsed = parse_catalog_search_cursor(secret_key=secret, cursor=token)
    assert parsed["version_id"] == 3
    assert parsed["offset"] == 10
    with pytest.raises(CatalogSearchCursorError):
        parse_catalog_search_cursor(secret_key=secret, cursor=token + "x")


def test_sidebar_without_active_catalog(tmp_path):
    app = _make_app(tmp_path, "nomina")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["banorte_csrf_token"] = "x" * 32
    resp = client.post(
        "/nomina/exportaciones/banorte/catalogo/sidebar/search",
        json={"csrf_token": "x" * 32, "q": "persona"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["catalog_active"] is False
    assert "no activo" in data["message"].lower()
    assert data["items"] == []


def test_sidebar_requires_csrf(tmp_path):
    app = _make_app(tmp_path, "nomina")
    client = app.test_client()
    resp = client.post(
        "/nomina/exportaciones/banorte/catalogo/sidebar/search",
        json={"q": "persona"},
    )
    assert resp.status_code == 403


@pytest.mark.parametrize("role", ["usuario", "coordinador"])
def test_sidebar_forbidden_for_non_nomina_roles(tmp_path, role):
    app = _make_app(tmp_path, role)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["banorte_csrf_token"] = "x" * 32
    resp = client.post(
        "/nomina/exportaciones/banorte/catalogo/sidebar/search",
        json={"csrf_token": "x" * 32},
    )
    assert resp.status_code == 403


def test_sidebar_search_active_catalog(tmp_path):
    app = _make_app(tmp_path, "nomina")
    _seed_active_catalog(app.config["DATABASE"])
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["banorte_csrf_token"] = "x" * 32
    resp = client.post(
        "/nomina/exportaciones/banorte/catalogo/sidebar/search",
        json={"csrf_token": "x" * 32, "q": "SIDEBAR"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["catalog_active"] is True
    assert data["items"]
    assert "Cache-Control" in resp.headers
    assert "no-store" in resp.headers["Cache-Control"]


def test_sidebar_service_search_by_employee(tmp_path):
    db = str(tmp_path / "svc.db")
    _seed_active_catalog(db)
    out = search_catalog_sidebar(
        db,
        secret_key="sidebar-test-secret",
        q="0000000001",
        role="nomina",
    )
    assert out["catalog_active"] is True
    assert out["items"]
    assert out["items"][0]["employee_number"] == "0000000001"
