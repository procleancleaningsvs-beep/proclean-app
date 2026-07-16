"""Fase 2.3A — draft_events append-only undo with stable row_id."""

from __future__ import annotations

import pytest

from modules.nomina.banorte.draft_repository import (
    apply_draft_row,
    create_manual_draft_shell,
    exclude_draft_row,
    get_draft,
    save_draft_rows,
    undo_last_draft_mutation,
)
from modules.nomina.banorte.prepare_service import prepare_draft_rows
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.db import ensure_nomina_tables


def _seed(db: str) -> dict:
    conn = connect(db)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, employee_number_effective, account_number,
            source_kind, validation_status, record_status,
            imported_at, imported_by, created_at, updated_at
        ) VALUES ('JUAN PEREZ','JUAN PEREZ','1234567890','123456789012','ALTA_MANUAL',
                  'IMPORTADO_EXITOSO','ACTIVO','t','u','t','t')
        """
    )
    conn.commit()
    conn.close()
    shell = create_manual_draft_shell(db, "u", names_text="X", amounts_text="1")
    draft = shell["draft"]
    rows = prepare_draft_rows(
        db,
        [
            {
                "position": 1,
                "nombre_recibido": "DESCONOCIDO",
                "amount_original_cents": 10000,
                "amount_final_cents": 10000,
                "included": 1,
                "match_kind": "NONE",
                "row_state": "NEEDS_REVIEW",
                "warnings": [],
                "user_decision": {},
            }
        ],
        origin_kind="MANUAL_CAPTURE",
    )
    return save_draft_rows(db, int(draft["id"]), "u", int(draft["revision"]), rows)


def test_draft_events_table_exists(tmp_path):
    db = str(tmp_path / "e.db")
    conn = connect(db)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='nomina_banorte_draft_events'"
    ).fetchone()[0]
    assert "target_event_id" in sql
    assert "reversed_by_event_id" not in sql
    assert "APPLY_AMOUNT" in sql
    conn.close()


def test_apply_amount_creates_reversible_event_and_undo(tmp_path):
    db = str(tmp_path / "a.db")
    draft = _seed(db)
    row_id = int(draft["rows"][0]["id"])
    conn = connect(db)
    bid = int(conn.execute("SELECT id FROM nomina_banorte_beneficiaries").fetchone()[0])
    conn.close()
    mid = apply_draft_row(
        db, int(draft["id"]), row_id, "u", int(draft["revision"]),
        beneficiary_id=bid, amount_final="100.00",
    )
    assert mid["rows"][0]["amount_final_cents"] == 10000
    assert mid["undo_available"] is True
    mid2 = apply_draft_row(
        db, int(mid["id"]), row_id, "u", int(mid["revision"]), amount_final="200.00",
    )
    assert mid2["rows"][0]["amount_final_cents"] == 20000
    undone = undo_last_draft_mutation(db, int(mid2["id"]), "u", int(mid2["revision"]))
    assert undone["rows"][0]["id"] == row_id
    assert undone["rows"][0]["amount_final_cents"] == 10000
    assert undone["undo_available"] is True


def test_exclude_undo_restores_row(tmp_path):
    db = str(tmp_path / "x.db")
    draft = _seed(db)
    row_id = int(draft["rows"][0]["id"])
    conn = connect(db)
    bid = int(conn.execute("SELECT id FROM nomina_banorte_beneficiaries").fetchone()[0])
    conn.close()
    mid = apply_draft_row(
        db, int(draft["id"]), row_id, "u", int(draft["revision"]),
        beneficiary_id=bid, amount_final="80",
    )
    ex = exclude_draft_row(db, int(mid["id"]), row_id, "u", int(mid["revision"]))
    assert ex["rows"][0]["excluded_at"]
    restored = undo_last_draft_mutation(db, int(ex["id"]), "u", int(ex["revision"]))
    assert restored["rows"][0]["id"] == row_id
    assert restored["rows"][0]["excluded_at"] is None
    assert restored["rows"][0]["included"] == 1


def test_save_preserves_row_id_with_events(tmp_path):
    db = str(tmp_path / "s.db")
    draft = _seed(db)
    row_id = int(draft["rows"][0]["id"])
    conn = connect(db)
    bid = int(conn.execute("SELECT id FROM nomina_banorte_beneficiaries").fetchone()[0])
    conn.close()
    mid = apply_draft_row(
        db, int(draft["id"]), row_id, "u", int(draft["revision"]),
        beneficiary_id=bid, amount_final="50",
    )
    rows = list(mid["rows"])
    rows[0] = dict(rows[0])
    rows[0]["nombre_recibido"] = "NUEVO NOMBRE"
    saved = save_draft_rows(
        db, int(mid["id"]), "u", int(mid["revision"]), rows, consecutive_pref="01",
    )
    assert saved["rows"][0]["id"] == row_id
    assert saved["rows"][0]["nombre_recibido"] == "NUEVO NOMBRE"
    # events still reference row
    conn = connect(db)
    ev = conn.execute(
        "SELECT row_id FROM nomina_banorte_draft_events WHERE draft_id=?",
        (int(mid["id"]),),
    ).fetchone()
    conn.close()
    assert int(ev["row_id"]) == row_id
    undone = undo_last_draft_mutation(db, int(saved["id"]), "u", int(saved["revision"]))
    assert undone["rows"][0]["id"] == row_id


def test_events_never_update_prior_rows(tmp_path):
    db = str(tmp_path / "n.db")
    draft = _seed(db)
    row_id = int(draft["rows"][0]["id"])
    mid = apply_draft_row(
        db, int(draft["id"]), row_id, "u", int(draft["revision"]), amount_final="10",
    )
    conn = connect(db)
    first_id = int(
        conn.execute(
            "SELECT id FROM nomina_banorte_draft_events WHERE draft_id=? ORDER BY id",
            (int(draft["id"]),),
        ).fetchone()[0]
    )
    before = dict(
        conn.execute("SELECT * FROM nomina_banorte_draft_events WHERE id=?", (first_id,)).fetchone()
    )
    conn.close()
    undo_last_draft_mutation(db, int(mid["id"]), "u", int(mid["revision"]))
    conn = connect(db)
    after = dict(
        conn.execute("SELECT * FROM nomina_banorte_draft_events WHERE id=?", (first_id,)).fetchone()
    )
    conn.close()
    assert before == after
