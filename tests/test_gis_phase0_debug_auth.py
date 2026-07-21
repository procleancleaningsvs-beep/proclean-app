"""Phase 0: anonymous access blocked on exportacion-imss debug-reporte."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from flask import Flask, g

from modules.exportacion_imss.routes import exportacion_imss_bp


def _app(tmp_path: Path, *, authenticated: bool):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="gis-phase0", DATA_DIR=str(tmp_path))
    app.register_blueprint(exportacion_imss_bp)

    @app.before_request
    def _load_user():
        if authenticated:
            g.user = {"id": 1, "username": "tester", "role": "admin", "is_admin": True}
        else:
            g.user = None

    return app


def test_debug_reporte_anonymous_blocked(tmp_path):
    app = _app(tmp_path, authenticated=False)
    client = app.test_client()
    res = client.get("/exportacion-imss/debug-reporte/ClienteX/2026/3")
    assert res.status_code in {401, 403, 302}
    if res.status_code == 302:
        assert "/login" in (res.headers.get("Location") or "")
    body = res.get_data(as_text=True)
    assert "total_fijos" not in body
    assert "fijo_ejemplo" not in body
    assert "rotativo_ejemplo" not in body


def test_debug_reporte_authenticated_keeps_payload_contract(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    reports = data_dir / "reportes_mensuales"
    reports.mkdir(parents=True)
    payload = {
        "cliente": "ClienteX",
        "fijos": [{"nombre": "A"}],
        "rotativos": [{"nombre": "B"}],
        "extra": 1,
    }
    (reports / "ClienteX_2026-03.json").write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    # Routes import DATA_DIR from comparativo_service at call time via local import.
    import modules.comparativo.comparativo_service as cs

    monkeypatch.setattr(cs, "DATA_DIR", str(data_dir))

    app = _app(tmp_path, authenticated=True)
    client = app.test_client()
    res = client.get("/exportacion-imss/debug-reporte/ClienteX/2026/3")
    assert res.status_code == 200
    data = res.get_json()
    assert data["total_fijos"] == 1
    assert data["total_rotativos"] == 1
    assert "keys_reporte" in data
    assert data["fijo_ejemplo"]["nombre"] == "A"
    assert data["rotativo_ejemplo"]["nombre"] == "B"


def test_nearby_movimientos_route_still_requires_auth(tmp_path):
    app = _app(tmp_path, authenticated=False)
    client = app.test_client()
    res = client.get("/exportacion-imss/movimientos")
    assert res.status_code in {401, 403, 302}
