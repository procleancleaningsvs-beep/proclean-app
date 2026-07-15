"""Banorte Fase 2.2C — merged hub tabs + Excel envelope/humanize."""

from __future__ import annotations

from pathlib import Path

from flask import Flask, g

from modules.nomina.banorte.excel_nomina_service import inspect_excel, prepare_excel_draft
from modules.nomina.banorte.repository import connect
from modules.nomina.blueprint import register_nomina
from modules.nomina.db import ensure_nomina_tables
from tests.test_banorte_excel_nomina import SECRET, _build_workbook


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


def test_hub_merged_four_tabs(tmp_path):
    html = _app(tmp_path).test_client().get("/nomina/exportaciones/banorte").data.decode("utf-8")
    assert 'data-banorte-tab="import-base"' in html
    assert 'data-banorte-tab="agregar-benef"' in html
    assert 'data-banorte-tab="cargar-pagos"' in html
    assert 'data-banorte-tab="historial"' in html
    assert 'data-banorte-tab="import-altas"' not in html
    assert 'data-banorte-tab="manual"' not in html
    assert 'data-banorte-tab="excel"' not in html
    js = (Path(__file__).resolve().parents[1] / "static/nomina/exportaciones_banorte_editor.js").read_text(
        encoding="utf-8"
    )
    assert "formatExcelPreview" in js
    assert "JSON.stringify(out.data.preview" not in js
    assert "JSON.stringify(out.data.preview," not in js


def test_excel_zero_creates_excluded_row(tmp_path):
    db = str(tmp_path / "z.db")
    conn = connect(db)
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()
    raw = _build_workbook(rows=[("CERO", "Banorte", 0), ("OK", "Banorte", 100)])
    ins = inspect_excel(raw, "nomina.xlsx", secret_key=SECRET, user="u")
    draft = prepare_excel_draft(
        db,
        "u",
        raw,
        filename="nomina.xlsx",
        sheet="Nomina1",
        token=ins["token"],
        secret_key=SECRET,
    )
    states = {r["nombre_recibido"]: r for r in draft["rows"]}
    assert states["CERO"]["row_state"] == "EXCLUDED"
    assert states["CERO"]["included"] == 0
    assert "amount_zero" in (states["CERO"].get("warnings") or [])
    assert states["OK"]["amount_final_cents"] == 10000


def test_excel_negative_not_in_draft_rows(tmp_path):
    db = str(tmp_path / "n.db")
    conn = connect(db)
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()
    raw = _build_workbook(rows=[("NEG", "Banorte", -10), ("OK", "Banorte", 50)])
    ins = inspect_excel(raw, "nomina.xlsx", secret_key=SECRET, user="u")
    draft = prepare_excel_draft(
        db,
        "u",
        raw,
        filename="nomina.xlsx",
        sheet="Nomina1",
        token=ins["token"],
        secret_key=SECRET,
    )
    names = {r["nombre_recibido"] for r in draft["rows"]}
    assert "NEG" not in names
    assert "OK" in names
    assert any(e.get("causa") == "amount_negative" for e in draft.get("amount_errors") or [])
