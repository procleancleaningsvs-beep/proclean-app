from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest
from flask import Flask, g

from modules.nomina.banorte.catalog_activation import catalog_activation_check
from modules.nomina.banorte.catalog_parser import CATALOG_HEADER_V1
from modules.nomina.banorte.catalog_service import (
    analyze_catalog_version,
    stage_catalog_version,
)
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
        SECRET_KEY="catalog-admin-test",
        DATABASE=str(tmp_path / f"{role}.db"),
    )
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

    @app.route("/login")
    def login():
        return "login"

    register_nomina(app)

    @app.before_request
    def _auth():
        g.user = {"id": 1, "username": "tester", "role": role}

    return app


def _token(html: bytes) -> str:
    text = html.decode("utf-8")
    for marker in ('data-csrf="', 'name="csrf_token" value="'):
        if marker in text:
            start = text.index(marker) + len(marker)
            return text[start : text.index('"', start)]
    raise AssertionError("csrf token not rendered")


def _row() -> list[str]:
    return [
        "0000000001",
        "PERSONA SINTETICA",
        "01/01/2026",
        "20/08/2026",
        "ADMIN",
        "01/01/1990",
        "SINT900101AA1",
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


def _payload() -> bytes:
    return "\n".join(
        [
            "FECHA: 20/ago./2026",
            "EMISORA: 67059 EMPRESA SINTETICA",
            "",
            "|".join(CATALOG_HEADER_V1) + "|",
            "|".join(_row()) + "|",
        ]
    ).encode("utf-8")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/nomina/exportaciones/banorte/import/altas"),
        ("post", "/nomina/exportaciones/banorte/import/reporte"),
        ("post", "/nomina/exportaciones/banorte/import/reporte/prepare-batch"),
        ("post", "/nomina/exportaciones/banorte/aliases"),
        ("post", "/nomina/exportaciones/banorte/beneficiarios/available-employee-numbers"),
        ("post", "/nomina/exportaciones/banorte/beneficiarios/create"),
        ("post", "/nomina/exportaciones/banorte/beneficiarios/1/actions"),
        ("post", "/nomina/exportaciones/banorte/beneficiarios/1/replace"),
        ("post", "/nomina/exportaciones/banorte/beneficiarios/batches"),
        ("get", "/nomina/exportaciones/banorte/beneficiarios/batches/1"),
        ("post", "/nomina/exportaciones/banorte/beneficiarios/batches/1/rows"),
        ("post", "/nomina/exportaciones/banorte/beneficiarios/batches/1/rows/1/delete"),
        ("post", "/nomina/exportaciones/banorte/beneficiarios/batches/1/confirm"),
        ("post", "/nomina/exportaciones/banorte/beneficiarios/batches/1/abandon"),
    ],
)
def test_nomina_gets_403_on_legacy_identity_admin_routes(tmp_path, method, path):
    client = _make_app(tmp_path, "nomina").test_client()
    response = getattr(client, method)(path)
    assert response.status_code == 403


def test_nomina_keeps_normal_banorte_operations_but_admin_controls_are_hidden(tmp_path):
    client = _make_app(tmp_path, "nomina").test_client()
    page = client.get("/nomina/exportaciones/banorte")
    assert page.status_code == 200
    html = page.data.decode("utf-8")
    assert "Cargar pagos" in html
    assert "Historial" in html
    assert "Generar .pag" in html
    assert "Importar base" not in html
    assert "Agregar beneficiarios" not in html
    assert "banorte-ben-edit" not in html
    token = _token(page.data)
    paste = client.post(
        "/nomina/exportaciones/banorte/paste",
        json={"csrf_token": token, "names": "Persona\n", "amounts": "10\n"},
        headers={"X-CSRF-Token": token},
    )
    assert paste.status_code == 200


def test_catalog_admin_ui_and_workflow_have_no_activation_route(tmp_path):
    app = _make_app(tmp_path, "admin")
    client = app.test_client()
    page = client.get("/nomina/exportaciones/banorte/catalogo")
    assert page.status_code == 200
    assert page.headers["Cache-Control"] == "private, no-store"
    html = page.data.decode("utf-8")
    assert "Catálogo oficial Banorte" in html
    assert "Activación disponible después de Release 2B" in html
    token = _token(page.data)

    uploaded = client.post(
        "/nomina/exportaciones/banorte/catalogo/versions",
        data={"csrf_token": token, "file": (io.BytesIO(_payload()), "synthetic.txt")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert uploaded.status_code == 302

    conn = sqlite3.connect(app.config["DATABASE"])
    version_id = conn.execute("SELECT id FROM nomina_banorte_catalog_versions").fetchone()[0]
    conn.close()
    detail = client.get(f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}")
    assert detail.status_code == 200
    assert "beneficiary_material_state_json" not in detail.get_data(as_text=True)

    analyzed = client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/analyze",
        data={"csrf_token": token},
    )
    assert analyzed.status_code == 302
    pre = client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/pre-reconcile",
        data={"csrf_token": token},
    )
    assert pre.status_code == 302
    ready = client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/ready",
        data={"csrf_token": token},
    )
    assert ready.status_code == 302
    diff = client.get(f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/diff")
    assert diff.status_code == 200
    check = client.get(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/activation-check"
    )
    assert check.status_code == 200
    assert check.get_json()["active_version_id"] is None

    activation_rules = sorted(
        rule.rule
        for rule in app.url_map.iter_rules()
        if "banorte" in rule.rule and ("activate" in rule.rule or "rollback" in rule.rule)
    )
    assert "/nomina/exportaciones/banorte/catalogo/versions/<int:version_id>/activate" in activation_rules
    assert "/nomina/exportaciones/banorte/catalogo/versions/<int:version_id>/rollback" in activation_rules
    activate = client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/activate",
        data={"csrf_token": token},
    )
    assert activate.status_code in {200, 400}
    if activate.status_code == 200:
        assert activate.get_json()["active_version_id"] == version_id


def test_catalog_routes_are_admin_only(tmp_path):
    client = _make_app(tmp_path, "nomina").test_client()
    assert client.get("/nomina/exportaciones/banorte/catalogo").status_code == 403
    assert client.post("/nomina/exportaciones/banorte/catalogo/versions").status_code == 403


def test_admin_keeps_legacy_identity_access_and_controls(tmp_path):
    client = _make_app(tmp_path, "admin").test_client()
    page = client.get("/nomina/exportaciones/banorte")
    assert page.status_code == 200
    html = page.data.decode("utf-8")
    assert "Importar base" in html
    assert "Agregar beneficiarios" in html
    assert "Catálogo oficial" in html
    assert "banorte-ben-edit" not in html  # empty fixture has no row to edit
    token = _token(page.data)
    numbers = client.post(
        "/nomina/exportaciones/banorte/beneficiarios/available-employee-numbers",
        json={"csrf_token": token, "limit": 5},
        headers={"X-CSRF-Token": token},
    )
    assert numbers.status_code == 200
    batch = client.post(
        "/nomina/exportaciones/banorte/beneficiarios/batches",
        json={"csrf_token": token, "origin_kind": "MANUAL"},
        headers={"X-CSRF-Token": token},
    )
    assert batch.status_code == 200


def test_activation_check_counts_only_open_legacy_drafts_and_never_activates(tmp_path):
    app = _make_app(tmp_path, "admin")
    version = stage_catalog_version(
        app.config["DATABASE"], raw=_payload(), filename="synthetic.txt", actor="admin"
    )
    analyze_catalog_version(app.config["DATABASE"], version["id"], actor="admin")
    conn = sqlite3.connect(app.config["DATABASE"])
    draft_values = (
        "admin",
        "admin",
        "2026-08-21T00:00:00",
        "2026-08-21T00:00:00",
        "MANUAL_CAPTURE",
        None,
        None,
        "synthetic-origin",
        None,
        None,
    )
    conn.execute(
        """
        INSERT INTO nomina_banorte_export_drafts (
            created_by,updated_by,created_at,updated_at,origin_kind,calculo_id,
            origin_updated_at,origin_hash,status,consecutive_pref,layout_date_pref
        ) VALUES (?,?,?,?,?,?,?,?, 'OPEN',?,?)
        """,
        draft_values,
    )
    conn.execute(
        """
        INSERT INTO nomina_banorte_export_drafts (
            created_by,updated_by,created_at,updated_at,origin_kind,calculo_id,
            origin_updated_at,origin_hash,status,consecutive_pref,layout_date_pref
        ) VALUES (?,?,?,?,?,?,?,?, 'ABANDONED',?,?)
        """,
        draft_values,
    )
    conn.commit()
    conn.close()
    check = catalog_activation_check(app.config["DATABASE"], version["id"])
    assert check["legacy_open_draft_blockers"] == 1
    assert check["active_version_id"] is None
    assert "LEGACY_OPEN_DRAFTS" not in check["blocker_codes"]
    assert check["can_activate"] is False
