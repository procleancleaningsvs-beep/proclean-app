"""Fase 2.3B — hub UI contracts (pager, staging, inline edit)."""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask, g

from modules.nomina.banorte.beneficiary_service import (
    BeneficiaryError,
    apply_beneficiary_action,
    create_manual_beneficiary,
    list_beneficiary_events,
)
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.blueprint import register_nomina
from modules.nomina.db import ensure_nomina_tables


def _app(tmp_path, role="admin"):
    repo = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(repo / "templates"), static_folder=str(repo / "static"))
    app.config.update(TESTING=True, SECRET_KEY="x", DATABASE=str(tmp_path / "app.db"))
    conn = connect(app.config["DATABASE"])
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
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


def _db(tmp_path):
    path = str(tmp_path / "b.db")
    conn = connect(path)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    conn.commit()
    conn.close()
    return path


def test_hub_has_pager_staging_and_available_numbers(tmp_path):
    html = _app(tmp_path).test_client().get("/nomina/exportaciones/banorte").data.decode("utf-8")
    assert "banorte-ben-pager" in html
    assert "banorte-ben-sort" in html
    assert "banorte-alta-form" in html
    assert "batch-use-acct" in html
    assert "Número de empleado" in html
    assert "banorte-available-emps" in html
    assert "banorte-batch-confirm" in html
    assert "confirm_reimport" not in html
    assert "reimport_confirmed" not in html


def test_js_pager_inline_and_staging_contracts():
    js = (
        Path(__file__).resolve().parents[1] / "static/nomina/exportaciones_banorte_editor.js"
    ).read_text(encoding="utf-8")
    assert 'textContent = "Primera"' in js or 'mk("Primera"' in js
    assert 'mk("Última"' in js or 'textContent = "Última"' in js
    assert "Mostrando" in js
    assert "openBenEdit" in js or "banorte-ben-edit" in js
    assert "/beneficiarios/" in js and "/actions" in js
    assert "/history" in js
    assert "batch-use-acct" in js
    assert "banorte-available-num" in js
    assert "prepare-batch" in js


def test_actions_require_reason(tmp_path):
    db = _db(tmp_path)
    created = create_manual_beneficiary(
        db, "u", nombre="ANA", account="1234567890", confirm_effective_from_account=True
    )
    with pytest.raises(BeneficiaryError) as exc:
        apply_beneficiary_action(
            db, "u", int(created["id"]), action="deactivate", reason="   "
        )
    assert exc.value.code == "reason_required"


def test_replace_for_identity_and_events(tmp_path):
    db = _db(tmp_path)
    created = create_manual_beneficiary(
        db, "u", nombre="ANA OLD", account="1234567890", confirm_effective_from_account=True
    )
    out = apply_beneficiary_action(
        db,
        "u",
        int(created["id"]),
        action="replace",
        reason="corrección de nombre",
        nombre="ANA NEW",
        account="1234567890",
        employee_number_effective="1234567890",
    )
    assert out["id"] != created["id"]
    assert out["record_status"] == "ACTIVO"
    events = list_beneficiary_events(db, int(created["id"]))
    assert events
    assert events[0]["action"] == "replace"
    assert events[0]["reason"] == "corrección de nombre"
