"""Banorte Fase 2.1B — trash, undo, restore-last."""

from __future__ import annotations

import pytest

from modules.nomina.banorte.draft_repository import (
    DraftStaleError,
    create_manual_draft_shell,
    exclude_draft_row,
    get_draft,
    restore_last_excluded,
    save_draft_rows,
)
from modules.nomina.banorte.prepare_service import prepare_draft_rows
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.db import ensure_nomina_tables


def _seed_manual_draft(db_path: str, user: str = "u") -> dict:
    conn = connect(db_path)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, employee_number_effective, account_number,
            source_kind, validation_status, record_status,
            imported_at, imported_by, created_at, updated_at
        ) VALUES ('JUAN PEREZ','JUAN PEREZ','42','1234567890','ALTA_MANUAL','IMPORTADO_EXITOSO','ACTIVO','t','u','t','t')
        """
    )
    conn.commit()
    conn.close()
    shell = create_manual_draft_shell(db_path, user, names_text="JUAN PEREZ", amounts_text="100.00")
    draft = shell["draft"]
    rows = prepare_draft_rows(
        db_path,
        [
            {
                "position": 1,
                "nombre_recibido": "JUAN PEREZ",
                "amount_original_cents": 10000,
                "amount_final_cents": 10000,
                "included": 1,
                "match_kind": "EXACT",
                "row_state": "OK",
                "warnings": [],
                "user_decision": {},
            }
        ],
        origin_kind="MANUAL_CAPTURE",
    )
    return save_draft_rows(db_path, int(draft["id"]), user, int(draft["revision"]), rows)


def test_exclude_sets_excluded_metadata(tmp_path):
    db = str(tmp_path / "t.db")
    draft = _seed_manual_draft(db)
    row_id = draft["rows"][0]["id"]
    out = exclude_draft_row(db, int(draft["id"]), int(row_id), "u", int(draft["revision"]))
    row = out["rows"][0]
    assert row["included"] == 0
    assert row["row_state"] == "EXCLUDED"
    assert row["excluded_at"]
    assert row["excluded_by"] == "u"


def test_restore_last_reverses_most_recent(tmp_path):
    db = str(tmp_path / "r.db")
    draft = _seed_manual_draft(db)
    row_id = draft["rows"][0]["id"]
    ex = exclude_draft_row(db, int(draft["id"]), int(row_id), "u", int(draft["revision"]))
    restored = restore_last_excluded(db, int(ex["id"]), "u", int(ex["revision"]))
    row = restored["rows"][0]
    assert row["included"] == 1
    assert row["excluded_at"] is None
    assert row["excluded_by"] is None


def test_restore_last_stale_409(tmp_path):
    db = str(tmp_path / "s.db")
    draft = _seed_manual_draft(db)
    row_id = draft["rows"][0]["id"]
    ex = exclude_draft_row(db, int(draft["id"]), int(row_id), "u", int(draft["revision"]))
    with pytest.raises(DraftStaleError):
        restore_last_excluded(db, int(ex["id"]), "u", int(draft["revision"]))


def test_restore_persists_after_reload(tmp_path):
    db = str(tmp_path / "p.db")
    draft = _seed_manual_draft(db)
    row_id = draft["rows"][0]["id"]
    ex = exclude_draft_row(db, int(draft["id"]), int(row_id), "u", int(draft["revision"]))
    restore_last_excluded(db, int(ex["id"]), "u", int(ex["revision"]))
    reloaded = get_draft(db, int(draft["id"]))
    assert reloaded["rows"][0]["included"] == 1
