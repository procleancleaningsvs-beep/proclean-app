from __future__ import annotations

import pytest

from modules.nomina.banorte.calculo_adapter import build_draft_rows_from_calculo
from modules.nomina.banorte.draft_repository import (
    DraftStaleError,
    abandon_draft,
    compute_reconciliation,
    create_draft_from_adapter,
    get_draft,
    reorder_draft_rows,
    save_draft_rows,
)
from tests.test_banorte_calculo_list import seed_calculo


def test_create_and_reload_draft(tmp_path):
    db = str(tmp_path / "d.db")
    cid = seed_calculo(tmp_path / "d.db", netos=[100.0, 50.0])
    adapted = build_draft_rows_from_calculo(db, cid)
    draft = create_draft_from_adapter(db, "alice", adapted)
    assert draft["status"] == "OPEN"
    assert draft["revision"] == 1
    assert draft["calculo_id"] == cid
    assert len(draft["rows"]) == 2
    assert draft["reconciliation"]["payment_count"] == 2
    again = create_draft_from_adapter(db, "alice", adapted)
    assert again["id"] == draft["id"]


def test_save_stale_revision_no_partial(tmp_path):
    db = str(tmp_path / "e.db")
    cid = seed_calculo(tmp_path / "e.db", netos=[100.0])
    adapted = build_draft_rows_from_calculo(db, cid)
    draft = create_draft_from_adapter(db, "alice", adapted)
    rows = draft["rows"]
    rows[0]["amount_final_cents"] = 99900
    save_draft_rows(db, draft["id"], "alice", 1, rows)
    mid = get_draft(db, draft["id"])
    assert mid["revision"] == 2
    assert mid["rows"][0]["amount_final_cents"] == 99900
    with pytest.raises(DraftStaleError):
        save_draft_rows(db, draft["id"], "alice", 1, rows)
    after = get_draft(db, draft["id"])
    assert after["revision"] == 2
    assert after["rows"][0]["amount_final_cents"] == 99900


def test_reorder_swap_and_stale(tmp_path):
    db = str(tmp_path / "f.db")
    cid = seed_calculo(tmp_path / "f.db", netos=[10.0, 20.0])
    adapted = build_draft_rows_from_calculo(db, cid)
    draft = create_draft_from_adapter(db, "bob", adapted)
    ids = [r["id"] for r in draft["rows"]]
    swapped = list(reversed(ids))
    out = reorder_draft_rows(db, draft["id"], "bob", 1, swapped)
    assert [r["id"] for r in out["rows"]] == swapped
    assert out["revision"] == 2
    with pytest.raises(DraftStaleError):
        reorder_draft_rows(db, draft["id"], "bob", 1, ids)
    assert [r["id"] for r in get_draft(db, draft["id"])["rows"]] == swapped


def test_abandon_requires_revision(tmp_path):
    db = str(tmp_path / "g.db")
    cid = seed_calculo(tmp_path / "g.db", netos=[10.0])
    adapted = build_draft_rows_from_calculo(db, cid)
    draft = create_draft_from_adapter(db, "carol", adapted)
    abandoned = abandon_draft(db, draft["id"], "carol", 1)
    assert abandoned["status"] == "ABANDONED"
    assert abandoned["revision"] == 2


def test_reconciliation_from_rows():
    rows = [
        {"included": 1, "amount_original_cents": 10000, "amount_final_cents": 11000},
        {"included": 0, "amount_original_cents": 5000, "amount_final_cents": 0},
        {"included": 1, "amount_original_cents": 2000, "amount_final_cents": 1500},
    ]
    rec = compute_reconciliation(rows)
    assert rec.included_count == 2
    assert rec.excluded_count == 1
    assert rec.total_final_cents == 12500
    assert rec.adjustments_positive_cents == 1000
    assert rec.adjustments_negative_cents == 500
