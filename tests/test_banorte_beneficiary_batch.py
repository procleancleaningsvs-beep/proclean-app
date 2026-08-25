"""Fase 2.3B — beneficiary staging batches (revision + all-or-nothing confirm)."""

from __future__ import annotations

import pytest

from modules.nomina.banorte.batch_service import (
    BatchStaleError,
    abandon_batch,
    add_batch_row,
    add_batch_rows_bulk,
    confirm_batch,
    create_batch,
    delete_batch_row,
    get_batch,
)
from modules.nomina.banorte.beneficiary_service import list_beneficiaries
from modules.nomina.banorte.employee_number_service import (
    BANORTE_RESERVED_EMPLOYEE_NUMBERS,
    collect_occupied_employee_numbers,
)
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
    conn = connect(db)
    conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiary_batch_rows (
            batch_id, position, nombre, cuenta, employee_number,
            use_account_as_employee_number, row_state, created_at, updated_at
        ) VALUES (?, 2, 'B', '1111111111', '1111111111', 1, 'OK', 't', 't')
        """,
        (int(r1["id"]),),
    )
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="batch_row_errors"):
        confirm_batch(db, int(r1["id"]), "u", int(r1["revision"]))
    assert list_beneficiaries(db, page=1)["total"] == 0
    still = get_batch(db, int(r1["id"]))
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


def test_open_staging_reserves_and_delete_or_abandon_releases(tmp_path):
    db = _db(tmp_path)
    batch = create_batch(db, "u", origin_kind="MANUAL")
    added = add_batch_row(
        db,
        int(batch["id"]),
        "u",
        int(batch["revision"]),
        nombre="",
        cuenta="0000000007",
        employee_number="0000000070",
        use_account_as_employee_number=True,
    )
    conn = connect(db)
    assert "0000000007" in collect_occupied_employee_numbers(conn)
    assert "0000000070" not in collect_occupied_employee_numbers(conn)
    conn.close()

    deleted = delete_batch_row(
        db,
        int(added["id"]),
        int(added["rows"][0]["id"]),
        "u",
        int(added["revision"]),
    )
    conn = connect(db)
    assert "0000000007" not in collect_occupied_employee_numbers(conn)
    conn.close()

    added_again = add_batch_row(
        db,
        int(deleted["id"]),
        "u",
        int(deleted["revision"]),
        nombre="",
        cuenta="0000000007",
        employee_number="0000000070",
        use_account_as_employee_number=True,
    )
    abandon_batch(db, int(added_again["id"]), "u", int(added_again["revision"]))
    conn = connect(db)
    assert "0000000007" not in collect_occupied_employee_numbers(conn)
    conn.close()


def test_batches_compete_atomically_confirm_own_rows_and_reject_reserved(tmp_path):
    db = _db(tmp_path)
    first = create_batch(db, "first", origin_kind="MANUAL")
    second = create_batch(db, "second", origin_kind="MANUAL")
    payload = [
        {
            "nombre": "WINNER",
            "cuenta": "8888888888",
            "employee_number": "0000000008",
        }
    ]
    winner = add_batch_rows_bulk(
        db, int(first["id"]), "first", int(first["revision"]), payload
    )
    with pytest.raises(ValueError, match="duplicate_employee_number"):
        add_batch_rows_bulk(
            db, int(second["id"]), "second", int(second["revision"]), payload
        )
    unchanged = get_batch(db, int(second["id"]))
    assert unchanged["revision"] == second["revision"]
    assert unchanged["rows"] == []

    confirmed = confirm_batch(
        db, int(winner["id"]), "first", int(winner["revision"])
    )
    assert confirmed["status"] == "CONFIRMED"

    internal = create_batch(db, "internal", origin_kind="MANUAL")
    duplicate_payload = [
        {"nombre": "A", "cuenta": "7777777771", "employee_number": "0000000009"},
        {"nombre": "B", "cuenta": "7777777772", "employee_number": "0000000009"},
    ]
    with pytest.raises(ValueError, match="duplicate_employee_number"):
        add_batch_rows_bulk(
            db,
            int(internal["id"]),
            "internal",
            int(internal["revision"]),
            duplicate_payload,
        )
    assert get_batch(db, int(internal["id"]))["rows"] == []

    reserved = create_batch(db, "reserved", origin_kind="MANUAL")
    for number in sorted(BANORTE_RESERVED_EMPLOYEE_NUMBERS):
        with pytest.raises(ValueError, match="duplicate_employee_number"):
            add_batch_row(
                db,
                int(reserved["id"]),
                "reserved",
                int(reserved["revision"]),
                nombre="RESERVED",
                cuenta="9999999999",
                employee_number=number,
            )
    current = get_batch(db, int(reserved["id"]))
    assert current["revision"] == reserved["revision"]

    conn = connect(db)
    now = "2026-08-25T00:00:00-06:00"
    conn.executemany(
        """
        INSERT INTO nomina_banorte_beneficiary_batch_rows (
            batch_id, position, nombre, cuenta, employee_number,
            use_account_as_employee_number, row_state, created_at, updated_at
        ) VALUES (?, ?, 'RESERVED', ?, ?, 0, 'OK', ?, ?)
        """,
        [
            (int(reserved["id"]), pos, str(9000000000 + pos), number, now, now)
            for pos, number in enumerate(sorted(BANORTE_RESERVED_EMPLOYEE_NUMBERS), start=1)
        ],
    )
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="batch_row_errors"):
        confirm_batch(
            db,
            int(reserved["id"]),
            "reserved",
            int(reserved["revision"]),
        )
