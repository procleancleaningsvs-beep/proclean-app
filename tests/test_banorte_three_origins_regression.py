"""Fase 2.3C — uniform editor contracts across CALCULO_RUN, EXCEL_NOMINA, MANUAL_CAPTURE."""

from __future__ import annotations

from pathlib import Path

from flask import Flask, g

from modules.nomina.banorte.beneficiary_service import create_manual_beneficiary
from modules.nomina.banorte.calculo_adapter import build_draft_rows_from_calculo
from modules.nomina.banorte.draft_repository import (
    create_draft_from_adapter,
    create_manual_draft_shell,
    save_draft_rows,
)
from modules.nomina.banorte.excel_nomina_service import inspect_excel, prepare_excel_draft
from modules.nomina.banorte.prepare_service import prepare_draft_rows
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.blueprint import register_nomina
from modules.nomina.db import ensure_nomina_tables
from tests.test_banorte_calculo_list import seed_calculo
from tests.test_banorte_excel_nomina import SECRET, _build_workbook


def _app(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(repo / "templates"), static_folder=str(repo / "static"))
    db = str(tmp_path / "app.db")
    app.config.update(TESTING=True, SECRET_KEY=SECRET, DATABASE=db)
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


def _csrf(client):
    html = client.get("/nomina/exportaciones/banorte").data.decode("utf-8")
    marker = 'data-csrf="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


def _assert_editor_mutation_surface(client, draft: dict, token: str):
    headers = {"X-CSRF-Token": token, "Content-Type": "application/json"}
    row_id = int(draft["rows"][0]["id"])
    rev = int(draft["revision"])
    assert "undo_available" in draft

    save = client.post(
        f"/nomina/exportaciones/banorte/drafts/{draft['id']}/save",
        json={
            "csrf_token": token,
            "expected_revision": rev,
            "rows": [
                {
                    "id": row_id,
                    "nombre_recibido": draft["rows"][0]["nombre_recibido"],
                    "amount_final_cents": draft["rows"][0]["amount_final_cents"],
                    "included": draft["rows"][0].get("included", 1),
                    "beneficiary_id": draft["rows"][0].get("beneficiary_id"),
                    "match_kind": draft["rows"][0].get("match_kind"),
                    "row_state": draft["rows"][0].get("row_state"),
                    "warnings": draft["rows"][0].get("warnings") or [],
                    "user_decision": draft["rows"][0].get("user_decision") or {},
                    "account_number_snapshot": draft["rows"][0].get("account_number_snapshot"),
                    "employee_number_snapshot": draft["rows"][0].get("employee_number_snapshot"),
                    "amount_original_cents": draft["rows"][0].get("amount_original_cents"),
                    "position": draft["rows"][0].get("position", 1),
                }
            ],
        },
        headers=headers,
    )
    assert save.status_code == 200, save.get_json()
    body = save.get_json()
    assert body["ok"] is True
    rev = int(body["draft"]["revision"])

    undo = client.post(
        f"/nomina/exportaciones/banorte/drafts/{draft['id']}/undo",
        json={"csrf_token": token, "expected_revision": rev},
        headers=headers,
    )
    # may be no-op if no reversible event yet
    assert undo.status_code in {200, 400}

    get = client.get(f"/nomina/exportaciones/banorte/drafts/{draft['id']}")
    assert get.status_code == 200
    assert get.get_json()["draft"]["origin_kind"] == draft["origin_kind"]
    assert "undo_available" in get.get_json()["draft"]


def test_three_origins_share_editor_endpoints(tmp_path):
    app, db = _app(tmp_path)
    create_manual_beneficiary(
        db, "tester", nombre="JUAN PEREZ", account="1234567890", confirm_effective_from_account=True
    )
    client = app.test_client()
    token = _csrf(client)

    shell = create_manual_draft_shell(db, "tester", names_text="JUAN PEREZ", amounts_text="100")
    draft = shell["draft"]
    rows = prepare_draft_rows(
        db,
        [
            {
                "position": 1,
                "nombre_recibido": "JUAN PEREZ",
                "amount_original_cents": 10000,
                "amount_final_cents": 10000,
                "included": 1,
                "warnings": [],
                "user_decision": {},
            }
        ],
        origin_kind="MANUAL_CAPTURE",
    )
    draft = save_draft_rows(db, int(draft["id"]), "tester", int(draft["revision"]), rows)
    assert draft["origin_kind"] == "MANUAL_CAPTURE"
    _assert_editor_mutation_surface(client, draft, token)

    raw = _build_workbook(rows=[("JUAN PEREZ", "Banorte", 50)])
    ins = inspect_excel(raw, "nomina.xlsx", secret_key=SECRET, user="tester")
    excel_draft = prepare_excel_draft(
        db,
        "tester",
        raw,
        filename="nomina.xlsx",
        sheet="Nomina1",
        token=ins["token"],
        secret_key=SECRET,
    )
    assert excel_draft["origin_kind"] == "EXCEL_NOMINA"
    _assert_editor_mutation_surface(client, excel_draft, token)

    cid = seed_calculo(Path(db), netos=[75.0], bancos=["BANORTE"], cuentas=["1234567890"])
    adapted = build_draft_rows_from_calculo(db, cid)
    calc_draft = create_draft_from_adapter(db, "tester", adapted)
    assert calc_draft["origin_kind"] == "CALCULO_RUN"
    _assert_editor_mutation_surface(client, calc_draft, token)


def test_excel_envelope_still_humanized_no_raw_json_preview():
    js = (
        Path(__file__).resolve().parents[1] / "static/nomina/exportaciones_banorte_editor.js"
    ).read_text(encoding="utf-8")
    assert "formatExcelPreview" in js
    assert "JSON.stringify(out.data.preview" not in js
    assert "enqueueTerminal" in js
    assert "duplicate_file_confirmation_required" in js
