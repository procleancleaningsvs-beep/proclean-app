from __future__ import annotations

import json
import sqlite3

import pytest

from modules.nomina.banorte.beneficiary_material import (
    BENEFICIARY_MATERIAL_FINGERPRINT_VERSION,
    BENEFICIARY_MATERIAL_KEYS,
    beneficiary_material_fingerprint,
    beneficiary_material_state,
    beneficiary_material_state_json,
)
from modules.nomina.banorte.catalog_parser import CATALOG_HEADER_V1
from modules.nomina.banorte.catalog_reconciliation import (
    CatalogReconciliationError,
    manual_reconcile_catalog_person,
    pre_reconcile_catalog_version,
    refresh_stale_reconciliations,
)
from modules.nomina.banorte.catalog_service import (
    analyze_catalog_version,
    get_catalog_version,
    stage_catalog_version,
)
from modules.nomina.banorte.validators import normalize_name
from modules.nomina.db import ensure_nomina_tables


def _catalog_row(*, employee: str, name: str, rfc: str, account: str) -> list[str]:
    return [
        employee,
        name,
        "01/01/2026",
        "20/08/2026",
        "ADMIN",
        "01/01/1990",
        rfc,
        "1000",
        "900",
        "NUEVO LEON",
        "01/01/2020",
        "SEMANAL",
        "NUEVO LEON",
        "CUENTA BANORTE",
        account,
        "0",
        "ALTA",
        "INDIVIDUAL",
        "APLICADO",
        "REGISTRO ACEPTADO",
        "ADMIN",
        "",
        "",
        "",
    ]


def _payload(*rows: list[str]) -> bytes:
    return "\n".join(
        [
            "FECHA: 20/ago./2026",
            "EMISORA: 67059 EMPRESA SINTETICA",
            "",
            "|".join(CATALOG_HEADER_V1) + "|",
            *("|".join(row) + "|" for row in rows),
        ]
    ).encode("utf-8")


def _insert_beneficiary(
    conn: sqlite3.Connection,
    *,
    employee: str,
    account: str,
    name: str,
    validation: str = "IMPORTADO_EXITOSO",
    record: str = "ACTIVO",
    manual_effective: int = 0,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original,nombre_normalizado,curp,employee_number_requested,
            employee_number_effective,account_number,source_kind,validation_status,
            record_status,banorte_employee_substituted,manual_effective_from_account,
            imported_at,imported_by,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            name,
            normalize_name(name),
            None,
            employee,
            employee,
            account,
            "ALTA_MANUAL",
            validation,
            record,
            0,
            manual_effective,
            "2026-01-01T00:00:00",
            "tester",
            "2026-01-01T00:00:00",
            "2026-01-01T00:00:00",
        ),
    )
    return int(cur.lastrowid)


@pytest.fixture
def reconciliation_db(tmp_path):
    path = tmp_path / "reconciliation.db"
    conn = sqlite3.connect(path)
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()
    return path


def _stage_analyze(path, *rows: list[str]) -> int:
    staged = stage_catalog_version(
        path,
        raw=_payload(*rows),
        filename="synthetic.txt",
        actor="admin",
    )
    analyze_catalog_version(path, staged["id"], actor="admin")
    return int(staged["id"])


def test_material_state_is_the_only_canonical_fingerprint_source():
    beneficiary = {
        "id": 7,
        "nombre_normalizado": "PERSONA DEMO",
        "curp": None,
        "employee_number_requested": "0000000007",
        "employee_number_effective": "0000000007",
        "account_number": "7777777777",
        "source_kind": "ALTA_MANUAL",
        "validation_status": "IMPORTADO_EXITOSO",
        "record_status": "ACTIVO",
        "banorte_employee_substituted": 0,
        "manual_effective_from_account": 0,
        "replaces_id": None,
        "updated_at": "first",
        "banorte_comment": "not material",
        "source_filename": "must-not-leak.xlsx",
    }
    state = beneficiary_material_state(beneficiary)
    assert tuple(state) == BENEFICIARY_MATERIAL_KEYS
    assert set(state) == set(BENEFICIARY_MATERIAL_KEYS)
    serialized = beneficiary_material_state_json(beneficiary)
    assert json.loads(serialized) == state
    assert "updated_at" not in serialized
    assert "comment" not in serialized
    assert "filename" not in serialized
    original = beneficiary_material_fingerprint(beneficiary)
    assert original.version == BENEFICIARY_MATERIAL_FINGERPRINT_VERSION
    beneficiary["updated_at"] = "second"
    beneficiary["banorte_comment"] = "changed auxiliary metadata"
    assert beneficiary_material_fingerprint(beneficiary).sha256 == original.sha256
    beneficiary["account_number"] = "8888888888"
    assert beneficiary_material_fingerprint(beneficiary).sha256 != original.sha256


def test_pre_reconciliation_uses_only_exact_canonical_and_controlled_name_matches(
    reconciliation_db,
):
    version_id = _stage_analyze(
        reconciliation_db,
        _catalog_row(
            employee="0000000001", name="PERSONA UNO", rfc="AAA900101AA1", account="1111111111"
        ),
        _catalog_row(
            employee="0000000002", name="José Demo", rfc="BBB900101BB2", account="2222222222"
        ),
        _catalog_row(
            employee="0000000003", name="Ma. de Jesus Demo", rfc="CCC900101CC3", account="3333333333"
        ),
        _catalog_row(
            employee="0000000004", name="SIN LEGACY", rfc="DDD900101DD4", account="4444444444"
        ),
    )
    conn = sqlite3.connect(reconciliation_db)
    _insert_beneficiary(
        conn, employee="0000000001", account="1111111111", name="PERSONA UNO"
    )
    _insert_beneficiary(
        conn, employee="0000000002", account="2222222222", name="Jose, Demo"
    )
    _insert_beneficiary(
        conn, employee="0000000003", account="3333333333", name="Maria de Jesus Demo"
    )
    conn.commit()
    conn.close()

    summary = pre_reconcile_catalog_version(reconciliation_db, version_id, actor="admin")
    assert summary["total"] == 4
    assert summary["by_status"] == {"AUTO_MATCHED": 3, "UNMATCHED": 1}
    assert summary["by_method"] == {
        "EXACT_EMPLOYEE_ACCOUNT_CANONICAL_NAME": 1,
        "EXACT_EMPLOYEE_ACCOUNT_CONTROLLED_MA": 1,
        "EXACT_EMPLOYEE_ACCOUNT_RAW_NAME": 1,
        "NONE": 1,
    }

    conn = sqlite3.connect(reconciliation_db)
    conn.row_factory = sqlite3.Row
    matched = conn.execute(
        """
        SELECT * FROM nomina_banorte_catalog_reconciliations
        WHERE reconciliation_status='AUTO_MATCHED' ORDER BY id LIMIT 1
        """
    ).fetchone()
    assert matched is not None
    state = json.loads(matched["beneficiary_material_state_json"])
    assert set(state) == set(BENEFICIARY_MATERIAL_KEYS)
    assert matched["beneficiary_material_fingerprint"] == beneficiary_material_fingerprint(state).sha256
    conn.close()


def test_split_identifiers_mismatches_and_unusable_remain_for_admin(reconciliation_db):
    version_id = _stage_analyze(
        reconciliation_db,
        _catalog_row(
            employee="0000000010", name="SPLIT", rfc="AAA900101AA1", account="1010101010"
        ),
        _catalog_row(
            employee="0000000020", name="ACCOUNT MISMATCH", rfc="BBB900101BB2", account="2020202020"
        ),
        _catalog_row(
            employee="0000000030", name="EMPLOYEE MISMATCH", rfc="CCC900101CC3", account="3030303030"
        ),
        _catalog_row(
            employee="0000000040", name="UNUSABLE", rfc="DDD900101DD4", account="4040404040"
        ),
    )
    conn = sqlite3.connect(reconciliation_db)
    _insert_beneficiary(conn, employee="0000000010", account="9999999999", name="SPLIT")
    _insert_beneficiary(conn, employee="0000000099", account="1010101010", name="SPLIT")
    _insert_beneficiary(
        conn, employee="0000000020", account="9999999998", name="ACCOUNT MISMATCH"
    )
    _insert_beneficiary(
        conn, employee="0000000098", account="3030303030", name="EMPLOYEE MISMATCH"
    )
    _insert_beneficiary(
        conn,
        employee="0000000040",
        account="4040404040",
        name="UNUSABLE",
        record="CONFLICTO_CRITICO",
    )
    conn.commit()
    conn.close()
    summary = pre_reconcile_catalog_version(reconciliation_db, version_id, actor="admin")
    assert summary["by_status"] == {
        "ACCOUNT_MISMATCH": 1,
        "EMPLOYEE_MISMATCH": 1,
        "LEGACY_NOT_USABLE": 1,
        "MULTIPLE_CANDIDATES": 1,
    }


def test_manual_reconciliation_requires_reason_and_material_compatibility(reconciliation_db):
    version_id = _stage_analyze(
        reconciliation_db,
        _catalog_row(
            employee="0000000001", name="PERSONA UNO", rfc="AAA900101AA1", account="1111111111"
        ),
    )
    conn = sqlite3.connect(reconciliation_db)
    beneficiary_id = _insert_beneficiary(
        conn, employee="0000000001", account="1111111111", name="PERSONA UNO"
    )
    conn.commit()
    conn.close()
    pre_reconcile_catalog_version(reconciliation_db, version_id, actor="admin")
    person_id = get_catalog_version(reconciliation_db, version_id)["persons"][0]["id"]

    with pytest.raises(CatalogReconciliationError, match="manual_reason_required"):
        manual_reconcile_catalog_person(
            reconciliation_db,
            person_id,
            beneficiary_id,
            actor="admin",
            reason="",
        )
    result = manual_reconcile_catalog_person(
        reconciliation_db,
        person_id,
        beneficiary_id,
        actor="admin",
        reason="Confirmación sintética controlada",
    )
    assert result["reconciliation_status"] == "MANUAL_MATCHED"
    assert result["match_method"] == "MANUAL_SELECTION"

    conn = sqlite3.connect(reconciliation_db)
    assert conn.execute(
        "SELECT COUNT(*) FROM nomina_banorte_catalog_reconciliations WHERE person_id=?",
        (person_id,),
    ).fetchone()[0] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM nomina_banorte_catalog_reconciliations WHERE person_id=? AND is_current=1",
        (person_id,),
    ).fetchone()[0] == 1
    conn.close()


def test_stale_detection_appends_history_and_never_uses_updated_at_alone(reconciliation_db):
    version_id = _stage_analyze(
        reconciliation_db,
        _catalog_row(
            employee="0000000001", name="PERSONA UNO", rfc="AAA900101AA1", account="1111111111"
        ),
    )
    conn = sqlite3.connect(reconciliation_db)
    beneficiary_id = _insert_beneficiary(
        conn, employee="0000000001", account="1111111111", name="PERSONA UNO"
    )
    conn.commit()
    conn.close()
    pre_reconcile_catalog_version(reconciliation_db, version_id, actor="admin")

    conn = sqlite3.connect(reconciliation_db)
    conn.execute(
        "UPDATE nomina_banorte_beneficiaries SET updated_at=? WHERE id=?",
        ("2026-08-21T01:00:00", beneficiary_id),
    )
    conn.commit()
    conn.close()
    assert refresh_stale_reconciliations(reconciliation_db, version_id, actor="admin") == 0

    conn = sqlite3.connect(reconciliation_db)
    conn.execute(
        "UPDATE nomina_banorte_beneficiaries SET account_number=?,updated_at=? WHERE id=?",
        ("9999999999", "2026-08-21T02:00:00", beneficiary_id),
    )
    conn.commit()
    conn.close()
    assert refresh_stale_reconciliations(reconciliation_db, version_id, actor="admin") == 1

    conn = sqlite3.connect(reconciliation_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM nomina_banorte_catalog_reconciliations ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["is_current"] == 0
    assert rows[1]["is_current"] == 1
    assert rows[1]["reconciliation_status"] == "STALE_RECONCILIATION"
    assert conn.execute(
        "SELECT COUNT(*) FROM nomina_banorte_catalog_events WHERE event_type='STALE_DETECTED'"
    ).fetchone()[0] == 1
    conn.close()
