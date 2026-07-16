"""Fase 2.3A — HTTP mutation sequencing (revision, undo, abandon)."""

from __future__ import annotations

from pathlib import Path

from flask import Flask, g

from modules.nomina.banorte.beneficiary_service import create_manual_beneficiary
from modules.nomina.banorte.draft_repository import create_manual_draft_shell, save_draft_rows
from modules.nomina.banorte.prepare_service import prepare_draft_rows
from modules.nomina.banorte.repository import connect
from modules.nomina.blueprint import register_nomina
from modules.nomina.db import ensure_nomina_tables


def _app(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(repo / "templates"), static_folder=str(repo / "static"))
    db = str(tmp_path / "app.db")
    app.config.update(TESTING=True, SECRET_KEY="fase23a", DATABASE=db)
    conn = connect(db)
    ensure_nomina_tables(conn)
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


def _seed_draft(db: str) -> dict:
    create_manual_beneficiary(
        db, "tester", nombre="JUAN PEREZ", account="1234567890", confirm_effective_from_account=True
    )
    shell = create_manual_draft_shell(db, "tester", names_text="X", amounts_text="1")
    draft = shell["draft"]
    rows = prepare_draft_rows(
        db,
        [
            {
                "position": 1,
                "nombre_recibido": "X",
                "amount_original_cents": 1000,
                "amount_final_cents": 1000,
                "included": 1,
                "match_kind": "NONE",
                "row_state": "NEEDS_REVIEW",
                "warnings": [],
                "user_decision": {},
            }
        ],
        origin_kind="MANUAL_CAPTURE",
    )
    return save_draft_rows(db, int(draft["id"]), "tester", int(draft["revision"]), rows)


def test_apply_exclude_undo_abandon_http(tmp_path):
    app, db = _app(tmp_path)
    draft = _seed_draft(db)
    client = app.test_client()
    html = client.get("/nomina/exportaciones/banorte").data.decode("utf-8")
    marker = 'data-csrf="'
    start = html.index(marker) + len(marker)
    token = html[start : html.index('"', start)]
    row_id = int(draft["rows"][0]["id"])
    bid = 1
    headers = {"X-CSRF-Token": token, "Content-Type": "application/json"}

    r1 = client.post(
        f"/nomina/exportaciones/banorte/drafts/{draft['id']}/rows/{row_id}/apply",
        json={
            "csrf_token": token,
            "expected_revision": draft["revision"],
            "beneficiary_id": bid,
            "amount_final": "100.00",
        },
        headers=headers,
    )
    assert r1.status_code == 200
    d1 = r1.get_json()
    assert d1["ok"]
    assert d1["draft"]["undo_available"] is True
    rev = d1["draft"]["revision"]

    r2 = client.post(
        f"/nomina/exportaciones/banorte/drafts/{draft['id']}/exclude-row",
        json={"csrf_token": token, "expected_revision": rev, "row_id": row_id, "confirm": True},
        headers=headers,
    )
    assert r2.status_code == 200
    rev = r2.get_json()["draft"]["revision"]

    r3 = client.post(
        f"/nomina/exportaciones/banorte/drafts/{draft['id']}/undo",
        json={"csrf_token": token, "expected_revision": rev},
        headers=headers,
    )
    assert r3.status_code == 200
    body = r3.get_json()["draft"]
    assert body["rows"][0]["excluded_at"] is None
    rev = body["revision"]

    # stale revision rejected
    stale = client.post(
        f"/nomina/exportaciones/banorte/drafts/{draft['id']}/rows/{row_id}/apply",
        json={"csrf_token": token, "expected_revision": rev - 1, "amount_final": "50"},
        headers=headers,
    )
    assert stale.status_code == 409
    assert "más reciente" in (stale.get_json().get("message") or "")

    ab = client.post(
        f"/nomina/exportaciones/banorte/drafts/{draft['id']}/abandon",
        json={"csrf_token": token, "expected_revision": rev, "confirm": True},
        headers=headers,
    )
    assert ab.status_code == 200
    assert ab.get_json()["draft"]["status"] == "ABANDONED"
