from __future__ import annotations

import sqlite3

import pytest

from modules.nomina.banorte.catalog_parser import CATALOG_HEADER_V1
from modules.nomina.banorte.catalog_service import (
    CatalogVersionError,
    analyze_catalog_version,
    catalog_version_diff,
    get_catalog_version,
    list_catalog_versions,
    mark_catalog_ready_for_review,
    stage_catalog_version,
)
from modules.nomina.db import ensure_nomina_tables


def _row(
    *,
    employee: str,
    rfc: str,
    account: str,
    created: str = "01/ene./2026",
    modified: str = "01/ene./2026",
    name: str = "Persona Sintetica",
    birth: str = "01/ene./1990",
    status: str = "APLICADO",
    result: str = "REGISTRO ACEPTADO",
) -> list[str]:
    return [
        employee,
        name,
        created,
        modified,
        "ADMIN",
        birth,
        rfc,
        "1000",
        "900",
        "NUEVO LEON",
        "01/ene./2020",
        "SEMANAL",
        "NUEVO LEON",
        "CUENTA BANORTE",
        account,
        "0",
        "ALTA",
        "INDIVIDUAL",
        status,
        result,
        "ADMIN",
        modified,
        "",
        "",
    ]


def _payload(*rows: list[str], report_date: str = "20/ago./2026") -> bytes:
    return "\n".join(
        [
            f"FECHA: {report_date}",
            "EMISORA: 67059",
            "",
            "|".join(CATALOG_HEADER_V1) + "|",
            *("|".join(row) + "|" for row in rows),
        ]
    ).encode("utf-8")


@pytest.fixture
def catalog_db(tmp_path):
    path = tmp_path / "catalog.db"
    conn = sqlite3.connect(path)
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()
    return path


def test_stage_and_analyze_projects_safe_persons_and_supports_zero_active(catalog_db):
    staged = stage_catalog_version(
        catalog_db,
        raw=_payload(
            _row(employee="0000000001", rfc="AAA900101AA1", account="1111111111"),
            _row(
                employee="0000000002",
                rfc="BBB900101BB2",
                account="2222222222",
                status="CAPTURADO",
            ),
        ),
        filename="synthetic.txt",
        actor="admin",
    )
    assert staged["status"] == "STAGED"
    assert staged["data_row_count"] == 2

    analyzed = analyze_catalog_version(catalog_db, staged["id"], actor="admin")
    assert analyzed["status"] == "ANALYZED"
    assert analyzed["eligible_row_count"] == 1
    assert analyzed["person_count"] == 2
    assert analyzed["catalog_ready_count"] == 1
    assert analyzed["blocked_person_count"] == 1
    assert analyzed["active_version_id"] is None
    assert analyzed["persons_by_status"] == {"CATALOG_READY": 1, "NO_ELIGIBLE_ROW": 1}

    detail = get_catalog_version(catalog_db, staged["id"])
    assert detail["person_count"] == 2
    assert detail["persons"][0]["issuer_normalized"] == "67059"
    assert {person["person_status"] for person in detail["persons"]} == {
        "CATALOG_READY",
        "NO_ELIGIBLE_ROW",
    }


def test_current_row_uses_recency_and_keeps_employee_and_account_together(catalog_db):
    staged = stage_catalog_version(
        catalog_db,
        raw=_payload(
            _row(
                employee="0000000099",
                rfc="AAA900101AA1",
                account="9999999999",
                modified="01/ene./2026",
            ),
            _row(
                employee="0000000001",
                rfc="AAA900101AA1",
                account="1111111111",
                modified="02/ene./2026",
            ),
        ),
        filename="recency.txt",
        actor="admin",
    )
    analyze_catalog_version(catalog_db, staged["id"], actor="admin")
    detail = get_catalog_version(catalog_db, staged["id"])
    person = detail["persons"][0]
    assert person["person_status"] == "CATALOG_READY"
    assert person["current_selection_method"] == "LATEST_MODIFIED"
    assert person["employee_number_normalized"] == "0000000001"
    assert person["account_number_normalized"] == "1111111111"


def test_final_tie_same_account_is_deterministic_but_different_accounts_is_ambiguous(catalog_db):
    same = stage_catalog_version(
        catalog_db,
        raw=_payload(
            _row(employee="0000000001", rfc="AAA900101AA1", account="1111111111"),
            _row(employee="0000000002", rfc="AAA900101AA1", account="1111111111"),
        ),
        filename="same-account.txt",
        actor="admin",
    )
    analyze_catalog_version(catalog_db, same["id"], actor="admin")
    same_person = get_catalog_version(catalog_db, same["id"])["persons"][0]
    assert same_person["person_status"] == "CATALOG_READY"
    assert same_person["current_selection_method"] == "TIED_SAME_ACCOUNT"
    assert same_person["employee_number_normalized"] == "0000000001"

    ambiguous = stage_catalog_version(
        catalog_db,
        raw=_payload(
            _row(employee="0000000003", rfc="CCC900101CC3", account="3333333333"),
            _row(employee="0000000004", rfc="CCC900101CC3", account="4444444444"),
        ),
        filename="ambiguous.txt",
        actor="admin",
    )
    analyze_catalog_version(catalog_db, ambiguous["id"], actor="admin")
    ambiguous_person = get_catalog_version(catalog_db, ambiguous["id"])["persons"][0]
    assert ambiguous_person["person_status"] == "AMBIGUOUS_CURRENT_ACCOUNT"
    assert ambiguous_person["current_row_id"] is None


def test_identity_conflict_is_fail_closed(catalog_db):
    staged = stage_catalog_version(
        catalog_db,
        raw=_payload(
            _row(employee="0000000001", rfc="AAA900101AA1", account="1111111111"),
            _row(
                employee="0000000002",
                rfc="AAA900101AA1",
                account="2222222222",
                name="Persona Materialmente Distinta",
                birth="02/feb./1991",
                modified="02/ene./2026",
            ),
        ),
        filename="identity-conflict.txt",
        actor="admin",
    )
    analyze_catalog_version(catalog_db, staged["id"], actor="admin")
    person = get_catalog_version(catalog_db, staged["id"])["persons"][0]
    assert person["person_status"] == "IDENTITY_CONFLICT"
    assert person["current_row_id"] is None


def test_ready_transition_diff_and_immutable_analyzed_rows(catalog_db):
    first = stage_catalog_version(
        catalog_db,
        raw=_payload(_row(employee="0000000001", rfc="AAA900101AA1", account="1111111111")),
        filename="first.txt",
        actor="admin",
    )
    with pytest.raises(CatalogVersionError, match="transition_invalid"):
        mark_catalog_ready_for_review(catalog_db, first["id"], actor="admin")
    analyze_catalog_version(catalog_db, first["id"], actor="admin")
    ready = mark_catalog_ready_for_review(catalog_db, first["id"], actor="admin")
    assert ready["status"] == "READY_FOR_REVIEW"

    second = stage_catalog_version(
        catalog_db,
        raw=_payload(
            _row(employee="0000000009", rfc="AAA900101AA1", account="9999999999"),
            _row(employee="0000000002", rfc="BBB900101BB2", account="2222222222"),
        ),
        filename="second.txt",
        actor="admin",
    )
    analyze_catalog_version(catalog_db, second["id"], actor="admin")
    diff = catalog_version_diff(catalog_db, second["id"], comparison_version_id=first["id"])
    assert diff["added"] == 1
    assert diff["changed"] == 1
    assert diff["removed"] == 0
    assert diff["unchanged"] == 0
    assert diff["changes_by_field"] == {"account_number": 1, "employee_number": 1}

    conn = sqlite3.connect(catalog_db)
    with pytest.raises(sqlite3.IntegrityError, match="catalog_rows_immutable"):
        conn.execute(
            "UPDATE nomina_banorte_catalog_rows SET name_normalized='ALTERED' WHERE version_id=?",
            (second["id"],),
        )
    conn.close()
    assert len(list_catalog_versions(catalog_db)) == 2


def test_duplicate_version_contract_is_rejected(catalog_db):
    payload = _payload(_row(employee="0000000001", rfc="AAA900101AA1", account="1111111111"))
    stage_catalog_version(catalog_db, raw=payload, filename="one.txt", actor="admin")
    with pytest.raises(CatalogVersionError, match="duplicate_file"):
        stage_catalog_version(catalog_db, raw=payload, filename="two.txt", actor="admin")
