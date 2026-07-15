"""Banorte Fase 2.2A — row apply + state recalculation."""

from __future__ import annotations

import pytest

from modules.nomina.banorte.draft_repository import (
    DraftStaleError,
    apply_draft_row,
    create_manual_draft_shell,
    exclude_draft_row,
    restore_last_excluded,
    save_draft_rows,
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
                "amount_original_cents": 0,
                "amount_final_cents": 0,
                "included": 0,
                "match_kind": "NONE",
                "row_state": "EXCLUDED",
                "warnings": ["amount_zero"],
                "user_decision": {},
            }
        ],
        origin_kind="MANUAL_CAPTURE",
    )
    return save_draft_rows(db, int(draft["id"]), "u", int(draft["revision"]), rows)


def test_apply_amount_zero_to_positive_with_beneficiary(tmp_path):
    db = str(tmp_path / "a.db")
    draft = _seed(db)
    row_id = draft["rows"][0]["id"]
    conn = connect(db)
    bid = int(conn.execute("SELECT id FROM nomina_banorte_beneficiaries").fetchone()[0])
    conn.close()
    out = apply_draft_row(
        db,
        int(draft["id"]),
        int(row_id),
        "u",
        int(draft["revision"]),
        beneficiary_id=bid,
        amount_final="100.00",
    )
    row = out["rows"][0]
    assert row["included"] == 1
    assert row["row_state"] == "OK"
    assert out["revision"] == draft["revision"] + 1


def test_apply_positive_to_zero_excludes(tmp_path):
    db = str(tmp_path / "b.db")
    draft = _seed(db)
    row_id = draft["rows"][0]["id"]
    conn = connect(db)
    bid = int(conn.execute("SELECT id FROM nomina_banorte_beneficiaries").fetchone()[0])
    conn.close()
    mid = apply_draft_row(
        db, int(draft["id"]), int(row_id), "u", int(draft["revision"]), beneficiary_id=bid, amount_final="50"
    )
    out = apply_draft_row(
        db, int(mid["id"]), int(row_id), "u", int(mid["revision"]), beneficiary_id=bid, amount_final="0"
    )
    row = out["rows"][0]
    assert row["included"] == 0
    assert row["row_state"] == "EXCLUDED"
    assert "amount_zero" in (row.get("warnings") or [])


def test_apply_stale_revision(tmp_path):
    db = str(tmp_path / "c.db")
    draft = _seed(db)
    row_id = draft["rows"][0]["id"]
    with pytest.raises(DraftStaleError):
        apply_draft_row(
            db, int(draft["id"]), int(row_id), "u", int(draft["revision"]) + 5, amount_final="10"
        )


def test_exclude_then_restore_recalculates(tmp_path):
    db = str(tmp_path / "d.db")
    draft = _seed(db)
    row_id = draft["rows"][0]["id"]
    conn = connect(db)
    bid = int(conn.execute("SELECT id FROM nomina_banorte_beneficiaries").fetchone()[0])
    conn.close()
    mid = apply_draft_row(
        db, int(draft["id"]), int(row_id), "u", int(draft["revision"]), beneficiary_id=bid, amount_final="80"
    )
    ex = exclude_draft_row(db, int(mid["id"]), int(row_id), "u", int(mid["revision"]))
    assert ex["rows"][0]["row_state"] == "EXCLUDED"
    assert ex["rows"][0]["excluded_at"]
    restored = restore_last_excluded(db, int(ex["id"]), "u", int(ex["revision"]))
    assert restored["rows"][0]["included"] == 1
    assert restored["rows"][0]["row_state"] == "OK"
