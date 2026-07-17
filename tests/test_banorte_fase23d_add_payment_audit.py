"""Fase 2.3D audit gaps — ordinary ADD_ROW, dup confirm, nonce, recon, migration."""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.nomina.banorte.beneficiary_service import create_manual_beneficiary
from modules.nomina.banorte.draft_repository import (
    add_draft_payment,
    create_manual_draft_shell,
    get_draft,
    undo_last_draft_mutation,
)
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.db import ensure_nomina_tables


def _db(tmp_path):
    path = str(tmp_path / "aud.db")
    conn = connect(path)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    conn.commit()
    conn.close()
    return path


def _open_draft(db):
    create_manual_beneficiary(
        db, "u", nombre="ANA PAY", account="1414141414", confirm_effective_from_account=True
    )
    create_manual_beneficiary(
        db, "u", nombre="BOB PAY", account="1515151515", confirm_effective_from_account=True
    )
    shell = create_manual_draft_shell(db, "u", names_text="X", amounts_text="1")
    return shell["draft"]


def test_js_add_payment_uses_ordinary_queue_not_terminal():
    js = (
        Path(__file__).resolve().parents[1] / "static/nomina/exportaciones_banorte_editor.js"
    ).read_text(encoding="utf-8")
    assert "enqueueOrdinary" in js
    assert 'type: "add_payment"' in js
    # must enqueue add_payment via ordinary path
    assert "enqueueOrdinary({" in js or "enqueueOrdinary({" in js.replace(" ", "")
    idx_add = js.find('type: "add_payment"')
    window = js[max(0, idx_add - 200) : idx_add + 80]
    assert "enqueueTerminal" not in window
    assert "enqueueOrdinary" in window or "pendingOrdinary" in js
    # terminal closing only for abandon/generate
    assert 'job.type === "abandon" || job.type === "generate"' in js


def test_add_row_event_and_manual_origin(tmp_path):
    db = _db(tmp_path)
    draft = _open_draft(db)
    conn = connect(db)
    ben = conn.execute(
        "SELECT id FROM nomina_banorte_beneficiaries WHERE account_number='1414141414'"
    ).fetchone()
    conn.close()
    out = add_draft_payment(
        db,
        int(draft["id"]),
        "u",
        int(draft["revision"]),
        beneficiary_id=int(ben["id"]),
        amount_final="10.00",
        request_nonce="n-1",
    )
    added = next(r for r in out["rows"] if r.get("row_origin") == "MANUAL_ADD")
    assert int(added["included"]) == 1
    assert added["row_state"] == "OK"
    conn = connect(db)
    ev = conn.execute(
        """
        SELECT action, reversible FROM nomina_banorte_draft_events
        WHERE draft_id=? AND row_id=? ORDER BY id DESC LIMIT 1
        """,
        (int(draft["id"]), int(added["id"])),
    ).fetchone()
    conn.close()
    assert ev["action"] == "ADD_ROW"
    assert int(ev["reversible"]) == 1
    recon = out["reconciliation"]
    assert recon["manual_added_count"] == 1
    assert recon["manual_added_total_cents"] == 1000
    assert recon["original_count"] == len(out["rows"]) - 1 or recon.get("original_row_count") is not None


def test_duplicate_requires_confirmation(tmp_path):
    db = _db(tmp_path)
    draft = _open_draft(db)
    conn = connect(db)
    ben = conn.execute(
        "SELECT id FROM nomina_banorte_beneficiaries WHERE account_number='1414141414'"
    ).fetchone()
    conn.close()
    out1 = add_draft_payment(
        db,
        int(draft["id"]),
        "u",
        int(draft["revision"]),
        beneficiary_id=int(ben["id"]),
        amount_final="10.00",
        request_nonce="n-a",
    )
    with pytest.raises(ValueError, match="duplicate_beneficiary_payment_confirmation_required"):
        add_draft_payment(
            db,
            int(draft["id"]),
            "u",
            int(out1["revision"]),
            beneficiary_id=int(ben["id"]),
            amount_final="20.00",
            request_nonce="n-b",
        )
    out2 = add_draft_payment(
        db,
        int(draft["id"]),
        "u",
        int(out1["revision"]),
        beneficiary_id=int(ben["id"]),
        amount_final="20.00",
        request_nonce="n-c",
        confirm_duplicate_beneficiary=True,
    )
    manuals = [r for r in out2["rows"] if r.get("row_origin") == "MANUAL_ADD" and int(r["included"]) == 1]
    assert len(manuals) == 2


def test_nonce_idempotent(tmp_path):
    db = _db(tmp_path)
    draft = _open_draft(db)
    conn = connect(db)
    ben = conn.execute(
        "SELECT id FROM nomina_banorte_beneficiaries WHERE account_number='1515151515'"
    ).fetchone()
    conn.close()
    a = add_draft_payment(
        db,
        int(draft["id"]),
        "u",
        int(draft["revision"]),
        beneficiary_id=int(ben["id"]),
        amount_final="33.00",
        request_nonce="same-nonce",
    )
    b = add_draft_payment(
        db,
        int(draft["id"]),
        "u",
        int(a["revision"]),  # client might send stale; nonce should short-circuit
        beneficiary_id=int(ben["id"]),
        amount_final="33.00",
        request_nonce="same-nonce",
    )
    assert a["revision"] == b["revision"]
    manuals = [r for r in b["rows"] if r.get("row_origin") == "MANUAL_ADD"]
    assert len(manuals) == 1


def test_undo_add_row_soft_excludes(tmp_path):
    db = _db(tmp_path)
    draft = _open_draft(db)
    conn = connect(db)
    ben = conn.execute(
        "SELECT id FROM nomina_banorte_beneficiaries WHERE account_number='1414141414'"
    ).fetchone()
    conn.close()
    out = add_draft_payment(
        db,
        int(draft["id"]),
        "u",
        int(draft["revision"]),
        beneficiary_id=int(ben["id"]),
        amount_final="12.00",
        request_nonce="undo-n",
    )
    added = next(r for r in out["rows"] if r.get("row_origin") == "MANUAL_ADD")
    undone = undo_last_draft_mutation(db, int(draft["id"]), "u", int(out["revision"]))
    row = next(r for r in undone["rows"] if int(r["id"]) == int(added["id"]))
    assert int(row["included"]) == 0
    assert row["row_state"] == "EXCLUDED"
    assert row.get("row_origin") == "MANUAL_ADD"
    assert undone["reconciliation"]["manual_added_count"] == 0


def test_draft_events_check_includes_add_row(tmp_path):
    db = _db(tmp_path)
    conn = connect(db)
    ensure_banorte_tables(conn)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='nomina_banorte_draft_events'"
    ).fetchone()[0]
    assert "ADD_ROW" in sql
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    ensure_banorte_tables(conn)  # second run no-op
    sql2 = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='nomina_banorte_draft_events'"
    ).fetchone()[0]
    assert "ADD_ROW" in sql2
    leftovers = conn.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE '%draft_events__old%'"
    ).fetchall()
    assert leftovers == []
    conn.close()


def test_undo_add_row_returns_message_field(tmp_path):
    db = _db(tmp_path)
    draft = _open_draft(db)
    conn = connect(db)
    ben = conn.execute(
        "SELECT id FROM nomina_banorte_beneficiaries WHERE account_number='1414141414'"
    ).fetchone()
    conn.close()
    out = add_draft_payment(
        db,
        int(draft["id"]),
        "u",
        int(draft["revision"]),
        beneficiary_id=int(ben["id"]),
        amount_final="12.00",
        request_nonce="undo-msg",
    )
    undone = undo_last_draft_mutation(db, int(draft["id"]), "u", int(out["revision"]))
    assert undone["last_undone_action"] == "ADD_ROW"
