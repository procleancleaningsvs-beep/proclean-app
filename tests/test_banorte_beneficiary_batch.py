"""Fase 2.3B — beneficiary staging batches (revision + all-or-nothing confirm)."""

from __future__ import annotations

import pytest

from modules.nomina.banorte.batch_service import (
    BatchStaleError,
    abandon_batch,
    add_batch_row,
    confirm_batch,
    create_batch,
    get_batch,
)
from modules.nomina.banorte.beneficiary_service import list_beneficiaries
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.db import ensure_nomina_tables


def _db(tmp_path):
    path = str(tmp_path / "batch.db")
    conn = connect(path)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    conn.commit()
    conn.close()
    return path


def test_one_open_batch_per_user_origin(tmp_path):
    db = _db(tmp_path)
    a = create_batch(db, "u", origin_kind="MANUAL")
    b = create_batch(db, "u", origin_kind="MANUAL")
    assert a["id"] == b["id"]
    assert a["status"] == "OPEN"


def test_add_row_and_confirm_all_or_nothing(tmp_path):
    db = _db(tmp_path)
    batch = create_batch(db, "u", origin_kind="MANUAL")
    mid = add_batch_row(
        db,
        int(batch["id"]),
        "u",
        int(batch["revision"]),
        nombre="ANA DEMO",
        cuenta="1234567890",
        employee_number="1234567890",
        use_account_as_employee_number=True,
    )
    assert mid["revision"] == batch["revision"] + 1
    out = confirm_batch(db, int(mid["id"]), "u", int(mid["revision"]))
    assert out["status"] == "CONFIRMED"
    listing = list_beneficiaries(db, page=1)
    assert listing["total"] == 1
    assert listing["rows"][0]["manual_effective_from_account"] == 1


def test_confirm_rolls_back_on_duplicate(tmp_path):
    db = _db(tmp_path)
    batch = create_batch(db, "u", origin_kind="MANUAL")
    r1 = add_batch_row(
        db, int(batch["id"]), "u", int(batch["revision"]),
        nombre="A", cuenta="1111111111", employee_number="1111111111",
        use_account_as_employee_number=True,
    )
    r2 = add_batch_row(
        db, int(r1["id"]), "u", int(r1["revision"]),
        nombre="B", cuenta="1111111111", employee_number="1111111111",
        use_account_as_employee_number=True,
    )
    with pytest.raises(ValueError, match="batch_row_errors"):
        confirm_batch(db, int(r2["id"]), "u", int(r2["revision"]))
    assert list_beneficiaries(db, page=1)["total"] == 0
    still = get_batch(db, int(r2["id"]))
    assert still["status"] == "OPEN"


def test_account_9_digits_rejected_for_auto_mode(tmp_path):
    db = _db(tmp_path)
    batch = create_batch(db, "u", origin_kind="MANUAL")
    with pytest.raises(ValueError, match="account_must_be_exactly_10"):
        add_batch_row(
            db, int(batch["id"]), "u", int(batch["revision"]),
            nombre="A", cuenta="123456789", employee_number="123456789",
            use_account_as_employee_number=True,
        )


def test_batch_stale_revision(tmp_path):
    db = _db(tmp_path)
    batch = create_batch(db, "u", origin_kind="MANUAL")
    add_batch_row(
        db, int(batch["id"]), "u", int(batch["revision"]),
        nombre="A", cuenta="2222222222", employee_number="2222222222",
        use_account_as_employee_number=True,
    )
    with pytest.raises(BatchStaleError):
        add_batch_row(
            db, int(batch["id"]), "u", int(batch["revision"]),
            nombre="B", cuenta="3333333333", employee_number="3333333333",
            use_account_as_employee_number=True,
        )


def test_abandon_batch(tmp_path):
    db = _db(tmp_path)
    batch = create_batch(db, "u", origin_kind="MANUAL")
    out = abandon_batch(db, int(batch["id"]), "u", int(batch["revision"]))
    assert out["status"] == "ABANDONED"
