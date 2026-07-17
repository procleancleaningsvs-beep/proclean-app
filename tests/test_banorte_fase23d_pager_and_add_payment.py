"""Fase 2.3D — pager inicial + agregar pago al borrador."""

from __future__ import annotations

from pathlib import Path

from flask import Flask, g

from modules.nomina.banorte.beneficiary_service import create_manual_beneficiary
from modules.nomina.banorte.draft_repository import (
    add_draft_payment,
    create_manual_draft_shell,
    undo_last_draft_mutation,
)
from modules.nomina.banorte.money import parse_money, to_cents
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.blueprint import register_nomina
from modules.nomina.db import ensure_nomina_tables


def _app(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(repo / "templates"), static_folder=str(repo / "static"))
    db = str(tmp_path / "app.db")
    app.config.update(TESTING=True, SECRET_KEY="d23", DATABASE=db)
    conn = connect(db)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, role TEXT, password_hash TEXT, created_at TEXT)"
    )
    conn.execute(
        "INSERT INTO users (id, username, role, password_hash, created_at) VALUES (1,?,?,?,?)",
        ("tester", "admin", "x", "t"),
    )
    conn.commit()
    conn.close()

    @app.route("/login")
    def login():
        return "login"

    register_nomina(app)

    @app.before_request
    def _auth():
        g.user = {"id": 1, "username": "tester", "role": "admin"}

    return app, db


def test_hub_initial_html_includes_pager_controls(tmp_path):
    app, _db = _app(tmp_path)
    html = app.test_client().get("/nomina/exportaciones/banorte").data.decode("utf-8")
    assert "banorte-ben-pager" in html
    assert "Primera" in html
    assert "Última" in html
    assert "Página" in html and "de" in html
    assert "Mostrando" in html
    js = (
        Path(__file__).resolve().parents[1] / "static/nomina/exportaciones_banorte_editor.js"
    ).read_text(encoding="utf-8")
    assert "loadBenefListing(" in js
    # initial hydrate must run without waiting for a mutation
    assert "hydrateBenefListing" in js or "loadBenefListing(1)" in js or "loadBenefListing(benPage)" in js


def test_add_draft_payment_and_undo(tmp_path):
    app, db = _app(tmp_path)
    ben = create_manual_beneficiary(
        db, "tester", nombre="PAGO UNO", account="1212121212", confirm_effective_from_account=True
    )
    shell = create_manual_draft_shell(db, "tester", names_text="X", amounts_text="1")
    draft = shell["draft"]
    money = parse_money("250.50")
    assert money.ok
    out = add_draft_payment(
        db,
        int(draft["id"]),
        "tester",
        int(draft["revision"]),
        beneficiary_id=int(ben["id"]),
        amount_final="250.50",
        request_nonce="pager-test-1",
    )
    assert out["revision"] == draft["revision"] + 1
    rows = out["rows"]
    assert any(int(r.get("beneficiary_id") or 0) == int(ben["id"]) for r in rows)
    added = next(r for r in rows if int(r.get("beneficiary_id") or 0) == int(ben["id"]))
    assert int(added["amount_final_cents"]) == to_cents(money.amount)
    assert int(added["included"]) == 1
    assert added["row_state"] == "OK"
    assert out.get("undo_available") is True

    undone = undo_last_draft_mutation(db, int(draft["id"]), "tester", int(out["revision"]))
    added2 = next(r for r in undone["rows"] if int(r["id"]) == int(added["id"]))
    assert int(added2["included"]) == 0 or added2["row_state"] == "EXCLUDED"


def test_add_payment_rejects_zero_and_inactive(tmp_path):
    _app_obj, db = _app(tmp_path)
    ben = create_manual_beneficiary(
        db, "tester", nombre="PAGO DOS", account="1313131313", confirm_effective_from_account=True
    )
    shell = create_manual_draft_shell(db, "tester", names_text="X", amounts_text="1")
    draft = shell["draft"]
    import pytest

    with pytest.raises(ValueError, match="amount"):
        add_draft_payment(
            db,
            int(draft["id"]),
            "tester",
            int(draft["revision"]),
            beneficiary_id=int(ben["id"]),
            amount_final="0",
            request_nonce="z1",
        )

    conn = connect(db)
    conn.execute(
        "UPDATE nomina_banorte_beneficiaries SET record_status='INACTIVO_MANUAL' WHERE id=?",
        (int(ben["id"]),),
    )
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="beneficiary"):
        add_draft_payment(
            db,
            int(draft["id"]),
            "tester",
            int(draft["revision"]),
            beneficiary_id=int(ben["id"]),
            amount_final="10",
            request_nonce="z2",
        )


def test_js_and_html_have_add_payment_controls():
    repo = Path(__file__).resolve().parents[1]
    html = (repo / "templates/nomina/exportaciones_banorte.html").read_text(encoding="utf-8")
    js = (repo / "static/nomina/exportaciones_banorte_editor.js").read_text(encoding="utf-8")
    assert "Agregar pago" in html
    assert "banorte-add-payment" in html or "banorte-add-pay" in html
    assert "add-payment" in js
    assert 'type: "add_payment"' in js
    assert "enqueueOrdinary" in js
    assert "hydrateBenefListing" in js
