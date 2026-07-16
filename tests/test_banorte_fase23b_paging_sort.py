"""Fase 2.3B — beneficiary paging metadata and numeric employee sort."""

from __future__ import annotations

import pytest

from modules.nomina.banorte.beneficiary_service import create_manual_beneficiary, list_beneficiaries
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.db import ensure_nomina_tables


def _db(tmp_path):
    path = str(tmp_path / "p.db")
    conn = connect(path)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    conn.commit()
    conn.close()
    return path


def test_listing_metadata_31_rows(tmp_path):
    db = _db(tmp_path)
    for i in range(31):
        create_manual_beneficiary(
            db, "u", nombre=f"P{i:02d}", account=str(3000000000 + i), confirm_effective_from_account=True
        )
    page1 = list_beneficiaries(db, page=1)
    assert page1["page_size"] == 15
    assert page1["total"] == 31
    assert page1["total_pages"] == 3
    assert page1["has_previous"] is False
    assert page1["has_next"] is True
    assert page1["start_index"] == 1
    assert page1["end_index"] == 15
    page3 = list_beneficiaries(db, page=3)
    assert page3["has_previous"] is True
    assert page3["has_next"] is False
    assert page3["start_index"] == 31
    assert page3["end_index"] == 31
    assert len(page3["rows"]) == 1


def test_emp_sort_numeric_with_padding_and_anomalies(tmp_path):
    db = _db(tmp_path)
    conn = connect(db)
    ensure_banorte_tables(conn)
    samples = [
        ("A", "9", "9000000001"),
        ("B", "10", "9000000002"),
        ("C", "0000000009", "9000000003"),
        ("D", "0000000010", "9000000004"),
        ("E", "", "9000000005"),
        ("F", "ABC", "9000000006"),
    ]
    for nombre, emp, acct in samples:
        conn.execute(
            """
            INSERT INTO nomina_banorte_beneficiaries (
                nombre_original, nombre_normalizado, employee_number_effective, account_number,
                source_kind, validation_status, record_status,
                imported_at, imported_by, created_at, updated_at
            ) VALUES (?,?,?,?,'ALTA_MANUAL','IMPORTADO_EXITOSO','ACTIVO','t','u','t','t')
            """,
            (nombre, nombre, emp, acct),
        )
    conn.commit()
    conn.close()
    asc = list_beneficiaries(db, page=1, sort="emp_asc")
    emps = [r["employee_number_effective"] for r in asc["rows"]]
    # numeric first: 9, 0000000009, 10, 0000000010 then anomalies
    assert emps[:4] in (
        ["9", "0000000009", "10", "0000000010"],
        ["0000000009", "9", "0000000010", "10"],
        ["9", "0000000009", "0000000010", "10"],
        ["0000000009", "9", "10", "0000000010"],
    )
    assert set(emps[:4]) == {"9", "0000000009", "10", "0000000010"}
    assert emp_key(emps[0]) <= emp_key(emps[1]) <= emp_key(emps[2]) <= emp_key(emps[3])
    assert emps[-2:] == ["", "ABC"] or set(emps[-2:]) == {"", "ABC"}


def emp_key(v: str) -> int:
    d = "".join(ch for ch in str(v) if ch.isdigit())
    return int(d) if d else 10**18


def test_sort_allowlist_rejects_injection(tmp_path):
    db = _db(tmp_path)
    create_manual_beneficiary(
        db, "u", nombre="Z", account="4100000001", confirm_effective_from_account=True
    )
    with pytest.raises(ValueError, match="invalid_sort"):
        list_beneficiaries(db, page=1, sort="name_asc; DROP TABLE x")
