from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from modules.nomina.banorte.beneficiary_material import beneficiary_material_fingerprint
from modules.nomina.banorte.catalog_activation import (
    CatalogActivationError,
    activate_catalog_version,
    catalog_activation_check,
)
from modules.nomina.banorte.catalog_legacy_sync import (
    CatalogLegacySyncError,
    apply_catalog_legacy_sync,
    build_catalog_legacy_sync_plan,
)
from modules.nomina.banorte.catalog_parser import CATALOG_HEADER_V1
from modules.nomina.banorte.catalog_reconciliation import pre_reconcile_catalog_version
from modules.nomina.banorte.payment_authority import evaluate_payment_authority
from modules.nomina.banorte.catalog_service import analyze_catalog_version, stage_catalog_version
from modules.nomina.banorte.catalog_row_adapter import prepare_capture_rows
from modules.nomina.banorte.draft_repository import create_manual_draft_shell, save_draft_rows
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.rows_capture import parse_capture_input
from modules.nomina.banorte.schema import ensure_banorte_tables
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
    curp: str | None = None,
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
            curp,
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


def _ready_version(
    db: str,
    *rows: list[str],
    beneficiaries: list[dict] | None = None,
    pre_reconcile: bool = True,
) -> int:
    conn = sqlite3.connect(db)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    if beneficiaries:
        for item in beneficiaries:
            _insert_beneficiary(conn, **item)
    conn.commit()
    conn.close()
    staged = stage_catalog_version(db, raw=_payload(*rows), filename="sync.txt", actor="admin")
    analyze_catalog_version(db, staged["id"], actor="admin")
    if pre_reconcile:
        pre_reconcile_catalog_version(db, staged["id"], actor="admin")
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE nomina_banorte_catalog_versions SET status='READY_FOR_REVIEW' WHERE id=?",
        (int(staged["id"]),),
    )
    conn.commit()
    conn.close()
    return int(staged["id"])


@pytest.fixture
def sync_db(tmp_path):
    path = tmp_path / "catalog_legacy_sync.db"
    conn = sqlite3.connect(path)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    conn.commit()
    conn.close()
    return str(path)


def test_sync_plan_keep_exact_match(sync_db):
    version_id = _ready_version(
        sync_db,
        _catalog_row(
            employee="0000000001", name="PERSONA UNO", rfc="AAA900101AA1", account="1111111111"
        ),
        beneficiaries=[
            {
                "employee": "0000000001",
                "account": "1111111111",
                "name": "PERSONA UNO",
                "curp": "AAA900101AA1",
            }
        ],
    )
    conn = connect(sync_db)
    plan = build_catalog_legacy_sync_plan(conn, version_id)
    conn.close()
    assert plan.valid
    assert plan.aggregates["KEEP"] == 1


def test_sync_plan_unmatched_create(sync_db):
    version_id = _ready_version(
        sync_db,
        _catalog_row(
            employee="0000000004", name="SIN LEGACY", rfc="DDD900101DD4", account="4444444444"
        ),
    )
    conn = connect(sync_db)
    plan = build_catalog_legacy_sync_plan(conn, version_id)
    conn.close()
    assert plan.valid
    assert plan.aggregates["CREATE"] == 1


def test_sync_plan_employee_mismatch_supersede(sync_db):
    version_id = _ready_version(
        sync_db,
        _catalog_row(
            employee="0000000030",
            name="EMPLOYEE MISMATCH",
            rfc="CCC900101CC3",
            account="3030303030",
        ),
        beneficiaries=[
            {"employee": "0000000098", "account": "3030303030", "name": "EMPLOYEE MISMATCH"}
        ],
    )
    conn = connect(sync_db)
    plan = build_catalog_legacy_sync_plan(conn, version_id)
    conn.close()
    assert plan.valid
    assert plan.aggregates["SUPERSEDE"] == 1


def test_sync_plan_account_mismatch_supersede(sync_db):
    version_id = _ready_version(
        sync_db,
        _catalog_row(
            employee="0000000020",
            name="ACCOUNT MISMATCH",
            rfc="BBB900101BB2",
            account="2020202020",
        ),
        beneficiaries=[
            {"employee": "0000000020", "account": "9999999998", "name": "ACCOUNT MISMATCH"}
        ],
    )
    conn = connect(sync_db)
    plan = build_catalog_legacy_sync_plan(conn, version_id)
    conn.close()
    assert plan.valid
    assert plan.aggregates["SUPERSEDE"] == 1


def test_keep_when_rfc_differs_from_curp_but_birth_compatible(sync_db):
    """A: RFC != CURP string must not alone force SUPERSEDE."""
    version_id = _ready_version(
        sync_db,
        _catalog_row(
            employee="0000000070", name="RFC CURP OK", rfc="RFC900101XX1", account="7070707070"
        ),
        beneficiaries=[
            {
                "employee": "0000000070",
                "account": "7070707070",
                "name": "RFC CURP OK",
                "curp": "LEG900101HDFRRC09",
            }
        ],
    )
    conn = connect(sync_db)
    plan = build_catalog_legacy_sync_plan(conn, version_id)
    conn.close()
    assert plan.valid
    assert plan.actions[0].action == "KEEP"


def test_keep_when_curp_absent_and_pair_exact(sync_db):
    """B: missing CURP must not force identity SUPERSEDE."""
    version_id = _ready_version(
        sync_db,
        _catalog_row(
            employee="0000000071", name="NO CURP LEGACY", rfc="NCR900101AA1", account="7171717171"
        ),
        beneficiaries=[
            {
                "employee": "0000000071",
                "account": "7171717171",
                "name": "NO CURP LEGACY",
                "curp": None,
            }
        ],
    )
    conn = connect(sync_db)
    plan = build_catalog_legacy_sync_plan(conn, version_id)
    conn.close()
    assert plan.valid
    assert plan.actions[0].action == "KEEP"


def test_manual_legacy_exact_pair_supersedes_to_payment_enabled(sync_db):
    """C: manual legacy residue must not survive KEEP."""
    version_id = _ready_version(
        sync_db,
        _catalog_row(
            employee="0000000072",
            name="MANUAL LEGACY",
            rfc="MNL900101AA1",
            account="7272727272",
        ),
        beneficiaries=[
            {
                "employee": "0000000072",
                "account": "7272727272",
                "name": "MANUAL LEGACY",
                "validation": "MANUAL_PENDIENTE_VALIDACION",
                "manual_effective": 1,
            }
        ],
    )
    conn = connect(sync_db)
    plan = build_catalog_legacy_sync_plan(conn, version_id)
    assert plan.actions[0].action == "SUPERSEDE"
    conn.execute("BEGIN IMMEDIATE")
    apply_catalog_legacy_sync(conn, version_id, actor="admin")
    conn.execute(
        """
        UPDATE nomina_banorte_catalog_versions
        SET status='ACTIVE', activated_by='admin', activated_at='t'
        WHERE id=?
        """,
        (version_id,),
    )
    conn.commit()
    person = conn.execute(
        """
        SELECT p.id, p.version_id, p.person_status, r.eligibility,
               r.employee_number_normalized, r.account_number_normalized
        FROM nomina_banorte_catalog_persons p
        JOIN nomina_banorte_catalog_rows r ON r.id=p.current_row_id
        WHERE p.version_id=? AND p.person_status='CATALOG_READY'
        """,
        (version_id,),
    ).fetchone()
    rec = conn.execute(
        "SELECT * FROM nomina_banorte_catalog_reconciliations WHERE person_id=? AND is_current=1",
        (int(person["id"]),),
    ).fetchone()
    beneficiary = dict(
        conn.execute(
            "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
            (int(rec["beneficiary_id"]),),
        ).fetchone()
    )
    auth = evaluate_payment_authority(
        conn=conn,
        person=dict(person),
        reconciliation=dict(rec),
        beneficiary=beneficiary,
        active_version_id=version_id,
    )
    conn.close()
    assert beneficiary["validation_status"] == "IMPORTADO_EXITOSO"
    assert int(beneficiary["manual_effective_from_account"] or 0) == 0
    assert auth["payment_enabled"] is True


def test_real_birth_identity_conflict_supersedes_catalog_wins(sync_db):
    """D: CURP birth digits conflicting with catalog birth force SUPERSEDE."""
    version_id = _ready_version(
        sync_db,
        _catalog_row(
            employee="0000000050", name="IDENTITY OFFICIAL", rfc="OFF900101AA1", account="5050505050"
        ),
        beneficiaries=[
            {
                "employee": "0000000050",
                "account": "5050505050",
                "name": "IDENTITY OFFICIAL",
                "curp": "ABCD850101HDFXXX01",
            }
        ],
    )
    conn = connect(sync_db)
    plan = build_catalog_legacy_sync_plan(conn, version_id)
    assert plan.actions[0].action == "SUPERSEDE"
    conn.execute("BEGIN IMMEDIATE")
    apply_catalog_legacy_sync(conn, version_id, actor="admin")
    conn.commit()
    row = conn.execute(
        "SELECT curp, validation_status, source_kind FROM nomina_banorte_beneficiaries WHERE record_status='ACTIVO'"
    ).fetchone()
    conn.close()
    assert row["curp"] == "OFF900101AA1"
    assert row["validation_status"] == "IMPORTADO_EXITOSO"
    assert row["source_kind"] == "ALTAS_NOMINA_BANORTE"


def test_sync_plan_identifiers_both_mismatch_catalog_wins(sync_db):
    version_id = _ready_version(
        sync_db,
        _catalog_row(
            employee="0000000060", name="CANONICAL ONE", rfc="CAN900101AA1", account="6060606060"
        ),
        beneficiaries=[
            {"employee": "0000000091", "account": "9191919191", "name": "CANONICAL ONE"}
        ],
    )
    conn = connect(sync_db)
    conn.execute("BEGIN IMMEDIATE")
    apply_catalog_legacy_sync(conn, version_id, actor="admin")
    active = conn.execute(
        """
        SELECT employee_number_effective,account_number
        FROM nomina_banorte_beneficiaries WHERE record_status='ACTIVO'
        """
    ).fetchone()
    conn.close()
    assert active["employee_number_effective"] == "0000000060"
    assert active["account_number"] == "6060606060"


def test_sync_plan_split_identifiers_single_canonical(sync_db):
    version_id = _ready_version(
        sync_db,
        _catalog_row(employee="0000000010", name="SPLIT", rfc="AAA900101AA1", account="1010101010"),
        beneficiaries=[
            {"employee": "0000000010", "account": "9999999999", "name": "SPLIT"},
            {"employee": "0000000099", "account": "1010101010", "name": "SPLIT"},
        ],
    )
    conn = connect(sync_db)
    conn.execute("BEGIN IMMEDIATE")
    apply_catalog_legacy_sync(conn, version_id, actor="admin")
    actives = conn.execute(
        """
        SELECT employee_number_effective,account_number
        FROM nomina_banorte_beneficiaries WHERE record_status='ACTIVO'
        ORDER BY id
        """
    ).fetchall()
    conn.close()
    assert len(actives) == 1
    assert actives[0]["employee_number_effective"] == "0000000010"
    assert actives[0]["account_number"] == "1010101010"


def test_sync_plan_legacy_not_usable_creates_successor(sync_db):
    version_id = _ready_version(
        sync_db,
        _catalog_row(employee="0000000040", name="UNUSABLE", rfc="DDD900101DD4", account="4040404040"),
        beneficiaries=[
            {
                "employee": "0000000040",
                "account": "4040404040",
                "name": "UNUSABLE",
                "validation": "IMPORTADO_EXITOSO",
                "record": "INACTIVO_MANUAL",
            }
        ],
    )
    conn = connect(sync_db)
    conn.execute("BEGIN IMMEDIATE")
    apply_catalog_legacy_sync(conn, version_id, actor="admin")
    active = conn.execute(
        """
        SELECT validation_status,source_kind
        FROM nomina_banorte_beneficiaries WHERE record_status='ACTIVO'
        """
    ).fetchone()
    conn.close()
    assert active["validation_status"] == "IMPORTADO_EXITOSO"
    assert active["source_kind"] == "ALTAS_NOMINA_BANORTE"


def test_sync_inactivates_active_legacy_extra(sync_db):
    version_id = _ready_version(
        sync_db,
        _catalog_row(employee="0000000001", name="PERSONA UNO", rfc="AAA900101AA1", account="1111111111"),
        beneficiaries=[
            {"employee": "0000000001", "account": "1111111111", "name": "PERSONA UNO"},
            {"employee": "0000008888", "account": "8888888888", "name": "EXTRA LEGACY"},
        ],
    )
    conn = connect(sync_db)
    conn.execute("BEGIN IMMEDIATE")
    result = apply_catalog_legacy_sync(conn, version_id, actor="admin")
    conn.commit()
    extra = conn.execute(
        "SELECT record_status FROM nomina_banorte_beneficiaries WHERE account_number='8888888888'"
    ).fetchone()
    conn.close()
    assert result["aggregates"]["INACTIVATE_EXTRA"] == 1
    assert extra["record_status"] == "INACTIVO_MANUAL"


def _insert_historical_export(conn: sqlite3.Connection, beneficiary_id: int) -> tuple[int, bytes, str]:
    blob = b"HISTORICAL_PAG_BLOB"
    sha = "abc123historical"
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_exports (
            created_by, created_at, timezone, layout_date, layout_date_auto,
            date_override_confirmed, consecutive, filename, payment_count, total_cents,
            capture_origin, incidents_json, manual_row_count, aliases_used_json,
            recommendations_accepted_json, warnings_ignored_json,
            duplicate_consecutive_confirmed, file_sha256, file_size, file_blob, status
        ) VALUES ('tester','t','America/Monterrey','20260101','20260101',0,'01','hist.pag',1,10000,
                  'PASTE_LISTS','[]',0,'[]','[]','[]',0,?,?,?, 'GENERATED')
        """,
        (sha, len(blob), blob),
    )
    export_id = int(cur.lastrowid)
    conn.execute(
        """
        INSERT INTO nomina_banorte_export_items (
            export_id, position, nombre_recibido, beneficiary_id, employee_number_effective,
            account_number, amount_cents, match_kind, validation_status, record_status,
            is_manual_beneficiary, warnings_json, user_decision_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            export_id,
            1,
            "PERSONA UNO",
            beneficiary_id,
            "0000000001",
            "1111111111",
            10000,
            "EXACT",
            "IMPORTADO_EXITOSO",
            "ACTIVO",
            0,
            "[]",
            "{}",
        ),
    )
    return export_id, blob, sha


def test_historical_export_preserved_after_sync(sync_db):
    version_id = _ready_version(
        sync_db,
        _catalog_row(employee="0000000001", name="PERSONA UNO", rfc="AAA900101AA1", account="1111111111"),
        beneficiaries=[
            {"employee": "0000000001", "account": "1111111111", "name": "PERSONA UNO"}
        ],
    )
    conn = connect(sync_db)
    beneficiary_id = int(
        conn.execute("SELECT id FROM nomina_banorte_beneficiaries LIMIT 1").fetchone()[0]
    )
    export_id, blob, sha = _insert_historical_export(conn, beneficiary_id)
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    apply_catalog_legacy_sync(conn, version_id, actor="admin")
    conn.commit()
    export = conn.execute(
        "SELECT file_blob,file_sha256,filename FROM nomina_banorte_exports WHERE id=?",
        (export_id,),
    ).fetchone()
    item = conn.execute(
        "SELECT beneficiary_id,amount_cents FROM nomina_banorte_export_items WHERE export_id=?",
        (export_id,),
    ).fetchone()
    conn.close()
    assert export["file_blob"] == blob
    assert export["file_sha256"] == sha
    assert export["filename"] == "hist.pag"
    assert int(item["beneficiary_id"]) == beneficiary_id
    assert int(item["amount_cents"]) == 10000


def test_open_legacy_draft_blocked_drift_preserves_rows(sync_db):
    version_id = _ready_version(
        sync_db,
        _catalog_row(employee="0000000001", name="PERSONA UNO", rfc="AAA900101AA1", account="1111111111"),
        beneficiaries=[
            {"employee": "0000000001", "account": "1111111111", "name": "PERSONA UNO"}
        ],
    )
    shell = create_manual_draft_shell(sync_db, "tester", names_text="", amounts_text="")
    rows = prepare_capture_rows(
        sync_db,
        parse_capture_input(
            rows_payload=[{"name_raw": "PERSONA UNO", "amount_raw": "123.45"}]
        ),
        origin_kind="MANUAL_CAPTURE",
    )
    draft = save_draft_rows(sync_db, int(shell["draft"]["id"]), "tester", 1, rows)
    conn = connect(sync_db)
    conn.execute("BEGIN IMMEDIATE")
    apply_catalog_legacy_sync(conn, version_id, actor="admin")
    conn.commit()
    status = conn.execute(
        "SELECT status FROM nomina_banorte_export_drafts WHERE id=?",
        (int(draft["id"]),),
    ).fetchone()[0]
    saved = conn.execute(
        """
        SELECT beneficiary_id,amount_final_cents,nombre_recibido
        FROM nomina_banorte_export_draft_rows WHERE draft_id=?
        """,
        (int(draft["id"]),),
    ).fetchone()
    conn.close()
    assert status == "BLOCKED_DRIFT"
    assert int(saved["amount_final_cents"]) == 12345
    assert saved["nombre_recibido"] == "PERSONA UNO"
    assert saved["beneficiary_id"] is not None


def test_sync_failure_rolls_back(sync_db):
    version_id = _ready_version(
        sync_db,
        _catalog_row(employee="0000000001", name="PERSONA UNO", rfc="AAA900101AA1", account="1111111111"),
    )
    conn = connect(sync_db)
    conn.execute("BEGIN IMMEDIATE")
    with patch(
        "modules.nomina.banorte.catalog_legacy_sync._create_catalog_mirror",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            apply_catalog_legacy_sync(conn, version_id, actor="admin")
    conn.rollback()
    active = conn.execute(
        "SELECT COUNT(*) FROM nomina_banorte_beneficiaries WHERE record_status='ACTIVO'"
    ).fetchone()[0]
    version_status = conn.execute(
        "SELECT status FROM nomina_banorte_catalog_versions WHERE id=?",
        (version_id,),
    ).fetchone()[0]
    conn.close()
    assert int(active) == 0
    assert version_status == "READY_FOR_REVIEW"


def test_unique_account_collision_safe_ordering(sync_db):
    version_id = _ready_version(
        sync_db,
        _catalog_row(employee="0000000010", name="SPLIT", rfc="AAA900101AA1", account="1010101010"),
        beneficiaries=[
            {"employee": "0000000010", "account": "9999999999", "name": "SPLIT"},
            {"employee": "0000000099", "account": "1010101010", "name": "SPLIT"},
        ],
    )
    conn = connect(sync_db)
    conn.execute("BEGIN IMMEDIATE")
    apply_catalog_legacy_sync(conn, version_id, actor="admin")
    conn.commit()
    conn.close()


def test_reconciliation_and_fingerprint_after_sync(sync_db):
    version_id = _ready_version(
        sync_db,
        _catalog_row(employee="0000000001", name="PERSONA UNO", rfc="AAA900101AA1", account="1111111111"),
        beneficiaries=[
            {"employee": "0000000001", "account": "1111111111", "name": "PERSONA UNO"}
        ],
    )
    conn = connect(sync_db)
    conn.execute("BEGIN IMMEDIATE")
    apply_catalog_legacy_sync(conn, version_id, actor="admin")
    conn.commit()
    row = conn.execute(
        """
        SELECT r.reconciliation_status,r.match_method,r.reason_code,r.beneficiary_material_fingerprint,b.*
        FROM nomina_banorte_catalog_reconciliations r
        JOIN nomina_banorte_beneficiaries b ON b.id=r.beneficiary_id
        WHERE r.version_id=? AND r.is_current=1
        """,
        (version_id,),
    ).fetchone()
    conn.close()
    assert row["reconciliation_status"] == "AUTO_MATCHED"
    assert row["reason_code"] == "CATALOG_SYNC_KEEP"
    assert row["beneficiary_material_fingerprint"] == beneficiary_material_fingerprint(dict(row)).sha256


def test_activation_check_pending_reconciliation_resolvable(sync_db):
    version_id = _ready_version(
        sync_db,
        _catalog_row(employee="0000000010", name="SPLIT", rfc="AAA900101AA1", account="1010101010"),
        beneficiaries=[
            {"employee": "0000000010", "account": "9999999999", "name": "SPLIT"},
            {"employee": "0000000099", "account": "1010101010", "name": "SPLIT"},
        ],
    )
    check = catalog_activation_check(sync_db, version_id)
    assert check["sync_plan_valid"] is True
    assert "RECONCILIATION_PENDING" not in check["blocker_codes"]
    assert check["can_activate"] is True


def test_activation_check_invalid_sync_plan_reports_blocker(sync_db):
    version_id = _ready_version(
        sync_db,
        _catalog_row(employee="0000000001", name="PERSONA UNO", rfc="AAA900101AA1", account="1111111111"),
        beneficiaries=[
            {"employee": "0000000001", "account": "1111111111", "name": "PERSONA UNO"}
        ],
    )
    conn = connect(sync_db)
    old_id = int(
        conn.execute("SELECT id FROM nomina_banorte_beneficiaries WHERE record_status='ACTIVO'").fetchone()[0]
    )
    conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original,nombre_normalizado,curp,employee_number_requested,
            employee_number_effective,account_number,source_kind,validation_status,
            record_status,banorte_employee_substituted,manual_effective_from_account,
            imported_at,imported_by,replaces_id,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "SUCCESSOR",
            normalize_name("SUCCESSOR"),
            None,
            "0000009999",
            "0000009999",
            "9999999999",
            "ALTA_MANUAL",
            "IMPORTADO_EXITOSO",
            "ACTIVO",
            0,
            0,
            "t",
            "tester",
            old_id,
            "t",
            "t",
        ),
    )
    conn.commit()
    conn.close()
    check = catalog_activation_check(sync_db, version_id)
    assert check["sync_plan_valid"] is False
    assert "CATALOG_SYNC_INCOMPLETE" in check["blocker_codes"]


def test_first_activation_sets_single_active_version(sync_db):
    version_id = _ready_version(
        sync_db,
        _catalog_row(employee="0000000001", name="PERSONA UNO", rfc="AAA900101AA1", account="1111111111"),
        beneficiaries=[
            {"employee": "0000000001", "account": "1111111111", "name": "PERSONA UNO"}
        ],
    )
    shell = create_manual_draft_shell(sync_db, "tester", names_text="", amounts_text="")
    rows = prepare_capture_rows(
        sync_db,
        parse_capture_input(rows_payload=[{"name_raw": "PERSONA UNO", "amount_raw": "10.00"}]),
        origin_kind="MANUAL_CAPTURE",
    )
    save_draft_rows(sync_db, int(shell["draft"]["id"]), "tester", 1, rows)
    result = activate_catalog_version(sync_db, version_id, actor="admin")
    conn = connect(sync_db)
    active_count = conn.execute(
        "SELECT COUNT(*) FROM nomina_banorte_catalog_versions WHERE status='ACTIVE'"
    ).fetchone()[0]
    draft_status = conn.execute(
        "SELECT status FROM nomina_banorte_export_drafts WHERE id=?",
        (int(shell["draft"]["id"]),),
    ).fetchone()[0]
    conn.close()
    assert int(active_count) == 1
    assert result["active_version_id"] == version_id
    assert draft_status == "BLOCKED_DRIFT"


def test_activate_catalog_version_raises_on_invalid_plan(sync_db):
    version_id = _ready_version(
        sync_db,
        _catalog_row(employee="0000000001", name="PERSONA UNO", rfc="AAA900101AA1", account="1111111111"),
        beneficiaries=[
            {"employee": "0000000001", "account": "1111111111", "name": "PERSONA UNO"}
        ],
    )
    conn = connect(sync_db)
    old_id = int(
        conn.execute("SELECT id FROM nomina_banorte_beneficiaries WHERE record_status='ACTIVO'").fetchone()[0]
    )
    conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original,nombre_normalizado,curp,employee_number_requested,
            employee_number_effective,account_number,source_kind,validation_status,
            record_status,banorte_employee_substituted,manual_effective_from_account,
            imported_at,imported_by,replaces_id,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "SUCCESSOR",
            normalize_name("SUCCESSOR"),
            None,
            "0000009999",
            "0000009999",
            "9999999999",
            "ALTA_MANUAL",
            "IMPORTADO_EXITOSO",
            "ACTIVO",
            0,
            0,
            "t",
            "tester",
            old_id,
            "t",
            "t",
        ),
    )
    conn.commit()
    conn.close()
    with pytest.raises(CatalogActivationError):
        activate_catalog_version(sync_db, version_id, actor="admin")


def test_pag_golden_unchanged():
    from tests.test_banorte_pag_layout import test_synthetic_golden_byte_identical

    test_synthetic_golden_byte_identical()
