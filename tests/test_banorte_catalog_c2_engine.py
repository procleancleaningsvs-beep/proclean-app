"""Five focal C2 engine checks from the frozen Catalog Admin V2 contract."""
from __future__ import annotations

import sqlite3

import pytest

from modules.nomina.banorte.catalog_activation import (
    CatalogActivationError,
    activate_catalog_version,
    catalog_activation_check,
)
from modules.nomina.banorte.catalog_application_plan import catalog_apply_preview
from modules.nomina.banorte.catalog_parser import CATALOG_HEADER_V1
from modules.nomina.banorte.catalog_reconciliation import manual_confirm_catalog_lineage
from modules.nomina.banorte.catalog_service import analyze_catalog_version, stage_catalog_version
from modules.nomina.banorte.payment_authority import (
    evaluate_payment_authority,
    load_catalog_authority_bundle,
)
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.banorte.validators import normalize_name
from modules.nomina.db import ensure_nomina_tables


def _row(
    employee: str,
    name: str,
    rfc: str,
    account: str,
    *,
    birth: str = "01/ene./1990",
) -> list[str]:
    return [
        employee, name, "01/ene./2026", "20/ago./2026", "ADMIN", birth, rfc,
        "1000", "900", "NUEVO LEON", "01/ene./2020", "SEMANAL", "NUEVO LEON",
        "CUENTA BANORTE", account, "0", "ALTA", "INDIVIDUAL", "APLICADO",
        "REGISTRO ACEPTADO", "ADMIN", "", "", "",
    ]


def _payload(report_date: str, *rows: list[str]) -> bytes:
    return "\n".join(
        [
            f"FECHA: {report_date}",
            "EMISORA: 67059 EMPRESA SINTETICA",
            "",
            "|".join(CATALOG_HEADER_V1) + "|",
            *("|".join(row) + "|" for row in rows),
        ]
    ).encode("utf-8")


def _ready(db: str, report_date: str, filename: str, *rows: list[str]) -> int:
    staged = stage_catalog_version(
        db, raw=_payload(report_date, *rows), filename=filename, actor="tester"
    )
    analyze_catalog_version(db, staged["id"], actor="tester")
    conn = connect(db)
    conn.execute(
        "UPDATE nomina_banorte_catalog_versions SET status='READY_FOR_REVIEW' WHERE id=?",
        (int(staged["id"]),),
    )
    conn.commit()
    conn.close()
    return int(staged["id"])


def _db(tmp_path, name: str) -> str:
    path = str(tmp_path / name)
    conn = sqlite3.connect(path)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    conn.commit()
    conn.close()
    return path


def _first_active(db: str, *rows: list[str]) -> int:
    version_id = _ready(db, "20/ago./2026", "prior.txt", *rows)
    activate_catalog_version(db, version_id, actor="tester")
    return version_id


def _insert_beneficiary(
    db: str,
    *,
    employee: str,
    account: str,
    name: str,
    curp: str | None,
    created_at: str,
    record_status: str = "ACTIVO",
) -> int:
    conn = connect(db)
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original,nombre_normalizado,curp,employee_number_requested,
            employee_number_effective,account_number,source_kind,validation_status,
            record_status,banorte_employee_substituted,manual_effective_from_account,
            imported_at,imported_by,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,'ALTA_MANUAL','IMPORTADO_EXITOSO',?,0,0,?,'tester',?,?)
        """,
        (
            name, normalize_name(name), curp, employee, employee, account, record_status,
            created_at, created_at, created_at,
        ),
    )
    conn.commit()
    conn.close()
    return int(cur.lastrowid)


def _current_bindings(conn: sqlite3.Connection, version_id: int) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT rec.*,p.rfc_normalized,b.employee_number_effective,b.account_number,b.record_status
            FROM nomina_banorte_catalog_reconciliations rec
            JOIN nomina_banorte_catalog_persons p ON p.id=rec.person_id
            JOIN nomina_banorte_beneficiaries b ON b.id=rec.beneficiary_id
            WHERE rec.version_id=? AND rec.is_current=1 ORDER BY p.id
            """,
            (int(version_id),),
        )
    ]


def test_check_1_completely_new_baseline_unconfirmed_lineage(tmp_path):
    db = _db(tmp_path, "c2-new.db")
    prior_id = _first_active(
        db, _row("1001", "PERSONA ANTERIOR", "PERS900101A12", "1111")
    )
    conn = connect(db)
    prior_beneficiary_id = int(
        conn.execute(
            "SELECT beneficiary_id FROM nomina_banorte_catalog_reconciliations WHERE version_id=?",
            (prior_id,),
        ).fetchone()[0]
    )
    conn.close()
    target_id = _ready(
        db, "30/ago./2026", "target-new.txt",
        _row("2002", "PERSONA NUEVA", "NUEV910202B23", "2222", birth="02/feb./1991"),
    )
    preview = catalog_apply_preview(db, target_id)
    activation_check = catalog_activation_check(db, target_id)
    assert preview["can_apply"] is True
    assert preview["operational_blockers"] == []
    assert preview["lineage_unconfirmed_count"] == 1
    assert preview["actions"][0]["lineage_status"] == "UNCONFIRMED"
    assert preview["actions"][0]["match_method"] == "NONE"
    assert activation_check["can_activate"] is True
    assert activation_check["reconciliation_pending"] == 1
    assert activation_check["lineage_unconfirmed_count"] == 1

    activate_catalog_version(
        db, target_id, actor="tester", expected_preview_fingerprint=preview["preview_fingerprint"]
    )
    conn = connect(db)
    assert conn.execute("SELECT status FROM nomina_banorte_catalog_versions WHERE id=?", (target_id,)).fetchone()[0] == "ACTIVE"
    assert conn.execute("SELECT record_status FROM nomina_banorte_beneficiaries WHERE id=?", (prior_beneficiary_id,)).fetchone()[0] == "INACTIVO_MANUAL"
    binding = _current_bindings(conn, target_id)[0]
    assert (binding["reconciliation_status"], binding["lineage_status"], binding["match_method"]) == ("CATALOG_BOUND", "UNCONFIRMED", "NONE")
    person, reconciliation, beneficiary = load_catalog_authority_bundle(
        conn, catalog_person_id=int(binding["person_id"]), active_version_id=target_id
    )
    authority = evaluate_payment_authority(
        conn=conn, person=person, reconciliation=reconciliation,
        beneficiary=beneficiary, active_version_id=target_id,
    )
    assert authority["payment_enabled"] is True
    assert conn.execute("SELECT COUNT(*) FROM nomina_banorte_catalog_persons WHERE version_id=?", (prior_id,)).fetchone()[0] == 1
    legacy_row = conn.execute("SELECT reconciliation_status,lineage_status FROM nomina_banorte_catalog_reconciliations WHERE version_id=? AND is_current=1", (prior_id,)).fetchone()
    assert tuple(legacy_row) == ("AUTO_MATCHED", None)
    conn.close()


def test_check_2_confirmed_lineage_and_identifier_changes(tmp_path):
    db = _db(tmp_path, "c2-lineage.db")
    prior_id = _first_active(
        db,
        _row("1001", "MARIA EXACTA", "MEXA900101A12", "1111"),
        _row("2002", "PERSONA CAMBIO", "PCAM910202B23", "2222", birth="02/feb./1991"),
        _row("3003", "PERSONA MANUAL", "PMAN920303C34", "3333", birth="03/mar./1992"),
    )
    conn = connect(db)
    prior = {row["rfc_normalized"]: row for row in _current_bindings(conn, prior_id)}
    conn.close()
    target_id = _ready(
        db, "30/ago./2026", "target-lineage.txt",
        _row("1001", "MARIA EXACTA", "MEXA900101A12", "1111"),
        _row("9009", "PERSONA CAMBIO", "PCAM910202B23", "9999", birth="02/feb./1991"),
        _row("8008", "IDENTIDAD RENOMBRADA", "IDEN930404D45", "8888", birth="04/abr./1993"),
    )
    conn = connect(db)
    manual_person_id = int(
        conn.execute(
            "SELECT id FROM nomina_banorte_catalog_persons WHERE version_id=? AND rfc_normalized='IDEN930404D45'",
            (target_id,),
        ).fetchone()[0]
    )
    conn.close()
    manual_confirm_catalog_lineage(
        db,
        manual_person_id,
        int(prior["PMAN920303C34"]["beneficiary_id"]),
        actor="tester",
        reason="Continuidad confirmada en expediente controlado",
    )
    preview = catalog_apply_preview(db, target_id)
    assert preview["can_apply"] is True
    actions = {action["rfc"]: action for action in preview["actions"]}
    assert actions["MEXA900101A12"]["lineage_status"] == "CONFIRMED"
    assert actions["MEXA900101A12"]["match_method"] == "EXACT_EMPLOYEE_ACCOUNT_RAW_NAME"
    assert actions["PCAM910202B23"]["lineage_status"] == "CONFIRMED"
    assert actions["PCAM910202B23"]["match_method"] == "PREVIOUS_ACTIVE_RFC_BIRTH_RAW_NAME"
    assert actions["PCAM910202B23"]["identifier_change"] == "BOTH"
    assert actions["IDEN930404D45"]["match_method"] == "MANUAL_CONTINUITY_CONFIRMED"
    assert preview["lineage_confirmed_manual"] == 1

    activate_catalog_version(db, target_id, actor="tester", expected_preview_fingerprint=preview["preview_fingerprint"])
    conn = connect(db)
    current = {row["rfc_normalized"]: row for row in _current_bindings(conn, target_id)}
    changed = current["PCAM910202B23"]
    assert (changed["employee_number_effective"], changed["account_number"]) == ("9009", "9999")
    assert changed["beneficiary_id"] != prior["PCAM910202B23"]["beneficiary_id"]
    old = conn.execute("SELECT employee_number_effective,account_number,record_status FROM nomina_banorte_beneficiaries WHERE id=?", (prior["PCAM910202B23"]["beneficiary_id"],)).fetchone()
    assert tuple(old) == ("2002", "2222", "INACTIVO_REEMPLAZADO")
    predecessors = [row[0] for row in conn.execute("SELECT lineage_predecessor_beneficiary_id FROM nomina_banorte_catalog_reconciliations WHERE version_id=? AND lineage_status='CONFIRMED' AND is_current=1", (target_id,))]
    assert len(predecessors) == len(set(predecessors)) == 3
    conn.close()


def test_check_3_no_match_name_only_split_and_historical_exclusion(tmp_path):
    db = _db(tmp_path, "c2-conflicts.db")
    _first_active(
        db,
        _row("1101", "NOMBRE SOLO", "NOMS900101A12", "5101"),
        _row("1102", "NOMBRE SOLO", "NOMT900101A12", "5102"),
        _row("1103", "EMPLOYEE SOURCE", "EMP900101A12", "5103"),
        _row("1104", "ACCOUNT SOURCE", "ACCS900101A12", "5104"),
        _row("1105", "SPLIT A", "SPLA900101A12", "5105"),
        _row("1106", "SPLIT B", "SPLB900101A12", "5106"),
    )
    historical_id = _insert_beneficiary(
        db, employee="7777", account="8888", name="HISTORICO",
        curp="HIST900101A12", created_at="2026-01-01T00:00:00+00:00",
        record_status="INACTIVO_REEMPLAZADO",
    )
    target_id = _ready(
        db, "30/ago./2026", "target-conflicts.txt",
        _row("2101", "NOMBRE SOLO", "NEWS920303C34", "6101", birth="03/mar./1992"),
        _row("1103", "EMPLOYEE SOURCE", "EMP900101A12", "6103"),
        _row("2104", "OTRA PERSONA DOS", "NEWA920303C34", "5104", birth="03/mar./1992"),
        _row("1105", "SPLIT TARGET", "SPLT920303C34", "5106", birth="03/mar./1992"),
        _row("7777", "HISTORICO", "HIST900101A12", "8888"),
    )
    preview = catalog_apply_preview(db, target_id)
    assert preview["can_apply"] is False
    assert "SPLIT_PRIOR_CURRENT_IDENTIFIERS" in {item["code"] for item in preview["operational_blockers"]}
    non_split = [action for action in preview["actions"] if action["employee"] != "1105"]
    assert all(action["lineage_status"] == "UNCONFIRMED" for action in non_split)
    historical = next(action for action in preview["actions"] if action["employee"] == "7777")
    assert historical["predecessor_beneficiary_id"] is None
    assert historical_id != historical.get("predecessor_beneficiary_id")


def test_check_4_post_snapshot_absorb_drop_and_distinct_materialization(tmp_path):
    db = _db(tmp_path, "c2-post.db")
    _first_active(db, _row("1001", "BASE", "BASE900101A12", "1111"))
    dropped_id = _insert_beneficiary(
        db, employee="3001", account="3301", name="POST DROP", curp="PDRP900101A12",
        created_at="2026-08-25T00:00:00+00:00",
    )
    absorbed_id = _insert_beneficiary(
        db, employee="3002", account="3302", name="POST ABSORB", curp="PABS900101A12",
        created_at="2026-08-25T00:00:00+00:00",
    )
    distinct_id = _insert_beneficiary(
        db, employee="3003", account="3303", name="POST OLD NAME", curp="PDST900101A12",
        created_at="2026-08-25T00:00:00+00:00",
    )
    target_id = _ready(
        db, "30/ago./2026", "target-post.txt",
        _row("3002", "POST ABSORB", "PABS900101A12", "3302"),
        _row("3003", "POST NEW DISTINCT", "PDST900101A12", "3303"),
    )
    preview = catalog_apply_preview(db, target_id)
    assert preview["can_apply"] is True
    assert preview["post_additions_absorbed"] == 1
    assert preview["post_additions_dropped"] == 2
    distinct_action = next(action for action in preview["actions"] if action["employee"] == "3003")
    assert distinct_action["lineage_status"] == "UNCONFIRMED"

    activate_catalog_version(db, target_id, actor="tester", expected_preview_fingerprint=preview["preview_fingerprint"])
    conn = connect(db)
    statuses = {row["id"]: row["record_status"] for row in conn.execute("SELECT id,record_status FROM nomina_banorte_beneficiaries WHERE id IN (?,?,?)", (dropped_id, absorbed_id, distinct_id))}
    assert statuses[dropped_id] == "INACTIVO_MANUAL"
    assert statuses[absorbed_id] == "ACTIVO"
    assert statuses[distinct_id] == "INACTIVO_MANUAL"
    distinct_binding = next(row for row in _current_bindings(conn, target_id) if row["employee_number_effective"] == "3003")
    assert distinct_binding["beneficiary_id"] != distinct_id
    assert distinct_binding["lineage_status"] == "UNCONFIRMED"
    conn.close()


def test_check_5_preview_drift_rejected_with_complete_rollback(tmp_path):
    db = _db(tmp_path, "c2-drift.db")
    prior_id = _first_active(db, _row("1001", "BASE", "BASE900101A12", "1111"))
    target_id = _ready(
        db, "30/ago./2026", "target-drift.txt",
        _row("2002", "TARGET", "TARG910202B23", "2222", birth="02/feb./1991"),
    )
    preview = catalog_apply_preview(db, target_id)
    assert preview["preview_fingerprint"] == catalog_apply_preview(db, target_id)["preview_fingerprint"]
    conn = connect(db)
    prior_beneficiary_id = int(conn.execute("SELECT beneficiary_id FROM nomina_banorte_catalog_reconciliations WHERE version_id=?", (prior_id,)).fetchone()[0])
    conn.execute("UPDATE nomina_banorte_beneficiaries SET account_number='9991' WHERE id=?", (prior_beneficiary_id,))
    conn.commit()
    before = {
        "active": conn.execute("SELECT id FROM nomina_banorte_catalog_versions WHERE status='ACTIVE'").fetchone()[0],
        "beneficiaries": conn.execute("SELECT COUNT(*) FROM nomina_banorte_beneficiaries").fetchone()[0],
        "bound": conn.execute("SELECT COUNT(*) FROM nomina_banorte_catalog_reconciliations WHERE reconciliation_status='CATALOG_BOUND'").fetchone()[0],
    }
    conn.close()
    with pytest.raises(CatalogActivationError, match="PREVIEW_FINGERPRINT_DRIFT"):
        activate_catalog_version(
            db, target_id, actor="tester", expected_preview_fingerprint=preview["preview_fingerprint"]
        )
    conn = connect(db)
    after = {
        "active": conn.execute("SELECT id FROM nomina_banorte_catalog_versions WHERE status='ACTIVE'").fetchone()[0],
        "beneficiaries": conn.execute("SELECT COUNT(*) FROM nomina_banorte_beneficiaries").fetchone()[0],
        "bound": conn.execute("SELECT COUNT(*) FROM nomina_banorte_catalog_reconciliations WHERE reconciliation_status='CATALOG_BOUND'").fetchone()[0],
    }
    assert after == before
    assert conn.execute("SELECT status FROM nomina_banorte_catalog_versions WHERE id=?", (target_id,)).fetchone()[0] == "READY_FOR_REVIEW"
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
