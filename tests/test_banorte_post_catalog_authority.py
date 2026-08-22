"""Post-catalog payment authority — focal regression suite."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from modules.nomina.banorte.catalog_activation import activate_catalog_version
from modules.nomina.banorte.catalog_parser import CATALOG_HEADER_V1
from modules.nomina.banorte.catalog_reconciliation import pre_reconcile_catalog_version
from modules.nomina.banorte.catalog_row_adapter import prepare_capture_rows
from modules.nomina.banorte.catalog_search_service import search_catalog_sidebar
from modules.nomina.banorte.catalog_service import analyze_catalog_version, stage_catalog_version
from modules.nomina.banorte.draft_repository import (
    add_draft_payment,
    create_manual_draft_shell,
    save_draft_rows,
)
from modules.nomina.banorte.export_readiness import evaluate_pag_export_blockers
from modules.nomina.banorte.export_service import generate_from_persistent_draft
from modules.nomina.banorte.payment_authority import (
    evaluate_payment_authority,
    rehydrate_row_authority,
)
from modules.nomina.banorte.post_catalog_authority import (
    beneficiary_created_after_snapshot,
    evaluate_post_catalog_addition,
    parse_catalog_report_date,
    parse_utc_timestamp,
    resolve_beneficiary_payment_authority,
)
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.rows_capture import parse_capture_input
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.banorte.validators import normalize_name
from modules.nomina.db import ensure_nomina_tables


def _catalog_row(employee: str, name: str, account: str) -> list[str]:
    return [
        employee,
        name,
        "01/01/2026",
        "20/08/2026",
        "ADMIN",
        "01/01/1990",
        f"CUR{employee[-4:]}01AA1",
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


def _seed_and_activate(
    db: str,
    *,
    catalog_employee: str = "0000000001",
    catalog_name: str = "PERSONA CATALOGO",
    catalog_account: str = "1111111111",
    mirror_created_at: str = "2026-01-01T00:00:00+00:00",
) -> tuple[int, int]:
    conn = connect(db)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original,nombre_normalizado,employee_number_effective,account_number,
            source_kind,validation_status,record_status,manual_effective_from_account,
            imported_at,imported_by,created_at,updated_at
        ) VALUES (?,?,?,?,'ALTA_MANUAL','IMPORTADO_EXITOSO','ACTIVO',0,'t','u',?,?)
        """,
        (
            catalog_name,
            normalize_name(catalog_name),
            catalog_employee,
            catalog_account,
            mirror_created_at,
            mirror_created_at,
        ),
    )
    beneficiary_id = int(cur.lastrowid)
    conn.commit()
    payload = "\n".join(
        [
            "FECHA: 20/ago./2026",
            "EMISORA: 67059 EMPRESA SINTETICA",
            "",
            "|".join(CATALOG_HEADER_V1) + "|",
            "|".join(_catalog_row(catalog_employee, catalog_name, catalog_account)) + "|",
        ]
    ).encode("utf-8")
    staged = stage_catalog_version(db, raw=payload, filename="emp.txt", actor="admin")
    analyze_catalog_version(db, staged["id"], actor="admin")
    pre_reconcile_catalog_version(db, staged["id"], actor="admin")
    conn = connect(db)
    conn.execute(
        "UPDATE nomina_banorte_catalog_versions SET status='READY_FOR_REVIEW' WHERE id=?",
        (staged["id"],),
    )
    conn.commit()
    conn.close()
    activate_catalog_version(db, int(staged["id"]), actor="admin")
    conn = connect(db)
    activated_at = str(
        conn.execute(
            "SELECT activated_at FROM nomina_banorte_catalog_versions WHERE id=?",
            (int(staged["id"]),),
        ).fetchone()["activated_at"]
    )
    conn.close()
    return int(staged["id"]), activated_at


def _insert_beneficiary(
    db: str,
    *,
    name: str,
    employee: str,
    account: str,
    source_kind: str,
    validation_status: str,
    created_at: str,
    record_status: str = "ACTIVO",
    manual_effective: int = 0,
) -> int:
    conn = connect(db)
    ensure_banorte_tables(conn)
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original,nombre_normalizado,employee_number_effective,account_number,
            source_kind,validation_status,record_status,manual_effective_from_account,
            imported_at,imported_by,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,?,?, 't','u',?,?)
        """,
        (
            name,
            normalize_name(name),
            employee,
            account,
            source_kind,
            validation_status,
            record_status,
            manual_effective,
            created_at,
            created_at,
        ),
    )
    bid = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return bid


def test_post_snapshot_before_technical_activation_enabled(tmp_path):
    """Delta after TXT report_date qualifies even if created before activated_at."""
    db = str(tmp_path / "snapshot_boundary.db")
    version_id, activated_at = _seed_and_activate(db)
    conn = connect(db)
    report_date = str(
        conn.execute(
            "SELECT report_date FROM nomina_banorte_catalog_versions WHERE id=?",
            (version_id,),
        ).fetchone()["report_date"]
    )
    conn.close()
    bid = _insert_beneficiary(
        db,
        name="DELTA PRE ACTIVATION",
        employee="0000000770",
        account="7707707707",
        source_kind="REPORTE_DETALLADO",
        validation_status="IMPORTADO_EXITOSO",
        created_at="2026-08-21T10:00:00+00:00",
    )
    conn = connect(db)
    row = dict(conn.execute("SELECT * FROM nomina_banorte_beneficiaries WHERE id=?", (bid,)).fetchone())
    auth = evaluate_post_catalog_addition(conn, row)
    activated = parse_utc_timestamp(activated_at)
    created = parse_utc_timestamp(str(row["created_at"]))
    conn.close()
    assert parse_catalog_report_date(report_date) == parse_catalog_report_date("2026-08-20")
    assert created is not None and activated is not None
    assert created < activated, "fixture must predate technical activation"
    assert beneficiary_created_after_snapshot(row, report_date=report_date)
    assert auth["payment_enabled"] is True


def test_same_snapshot_day_creation_not_post_snapshot(tmp_path):
    db = str(tmp_path / "same_day.db")
    _seed_and_activate(db)
    bid = _insert_beneficiary(
        db,
        name="SAME DAY DELTA",
        employee="0000000769",
        account="7697697697",
        source_kind="REPORTE_DETALLADO",
        validation_status="IMPORTADO_EXITOSO",
        created_at="2026-08-20T23:59:59+00:00",
    )
    conn = connect(db)
    auth = evaluate_post_catalog_addition(
        conn, dict(conn.execute("SELECT * FROM nomina_banorte_beneficiaries WHERE id=?", (bid,)).fetchone())
    )
    conn.close()
    assert auth["payment_enabled"] is False
    assert "PRE_CATALOG_LEGACY_EXCLUDED" in auth["reason_codes"]


def test_active_catalog_member_still_payment_enabled(tmp_path):
    db = str(tmp_path / "catalog.db")
    version_id, activated_at = _seed_and_activate(db)
    conn = connect(db)
    cpid = conn.execute(
        "SELECT id FROM nomina_banorte_catalog_persons WHERE version_id=? LIMIT 1",
        (version_id,),
    ).fetchone()["id"]
    bundle = resolve_beneficiary_payment_authority(
        conn, int(conn.execute(
            "SELECT beneficiary_id FROM nomina_banorte_catalog_reconciliations WHERE version_id=? LIMIT 1",
            (version_id,),
        ).fetchone()["beneficiary_id"])
    )
    assert bundle["payment_enabled"] is True
    assert bundle["catalog_person_id"] == int(cpid)
    conn.close()


def test_post_reporte_after_activation_enabled_and_searchable(tmp_path):
    db = str(tmp_path / "reporte.db")
    _seed_and_activate(db)
    bid = _insert_beneficiary(
        db,
        name="ALTA REPORTE SINTETICA",
        employee="0000000999",
        account="9999999999",
        source_kind="REPORTE_DETALLADO",
        validation_status="IMPORTADO_EXITOSO",
        created_at="2026-09-01T12:00:00+00:00",
    )
    conn = connect(db)
    post = evaluate_post_catalog_addition(
        conn, dict(conn.execute("SELECT * FROM nomina_banorte_beneficiaries WHERE id=?", (bid,)).fetchone())
    )
    assert post["payment_enabled"] is True
    conn.close()
    out = search_catalog_sidebar(db, secret_key="k", q="REPORTE SINTETICA")
    assert any(item.get("beneficiary_id") == bid and item["payment_enabled"] for item in out["items"])


def test_manual_capture_prepares_post_catalog_row(tmp_path):
    db = str(tmp_path / "capture.db")
    _seed_and_activate(db)
    bid = _insert_beneficiary(
        db,
        name="ALTA MANUAL VALIDA",
        employee="0000000888",
        account="8888888888",
        source_kind="ALTA_MANUAL",
        validation_status="IMPORTADO_EXITOSO",
        created_at="2026-09-01T12:00:00+00:00",
    )
    rows = prepare_capture_rows(
        db,
        parse_capture_input(
            rows_payload=[
                {
                    "name_raw": "ALTA MANUAL VALIDA",
                    "amount_raw": "150.00",
                    "beneficiary_id": bid,
                }
            ]
        ),
        origin_kind="MANUAL_CAPTURE",
    )
    assert rows[0]["beneficiary_id"] == bid
    assert rows[0]["account_number_snapshot"] == "8888888888"
    assert rows[0]["row_state"] == "OK"
    assert rows[0]["included"] == 1


def test_editor_add_payment_and_export_readiness(tmp_path):
    db = str(tmp_path / "editor.db")
    _seed_and_activate(db)
    bid = _insert_beneficiary(
        db,
        name="PAGO EDITOR POST",
        employee="0000000777",
        account="7777777777",
        source_kind="REPORTE_DETALLADO",
        validation_status="IMPORTADO_EXITOSO",
        created_at="2026-09-01T12:00:00+00:00",
    )
    shell = create_manual_draft_shell(db, "u", names_text="", amounts_text="")
    draft = add_draft_payment(
        db,
        int(shell["draft"]["id"]),
        "u",
        1,
        beneficiary_id=bid,
        amount_final="250.00",
    )
    row = draft["rows"][0]
    assert row["row_state"] == "OK"
    conn = connect(db)
    blocked = evaluate_pag_export_blockers(conn, draft["rows"], draft=draft)
    conn.close()
    assert blocked == []
    generated = generate_from_persistent_draft(
        db,
        "u",
        int(draft["id"]),
        expected_revision=int(draft["revision"]),
        consecutive="01",
        confirm_manuals=True,
    )
    assert generated.payment_count == 1


def test_pre_catalog_legacy_orphan_disabled(tmp_path):
    db = str(tmp_path / "legacy.db")
    _seed_and_activate(db)
    bid = _insert_beneficiary(
        db,
        name="LEGACY ORPHAN",
        employee="0000000666",
        account="6666666666",
        source_kind="ALTA_MANUAL",
        validation_status="IMPORTADO_EXITOSO",
        created_at="2026-01-01T00:00:00+00:00",
    )
    conn = connect(db)
    auth = resolve_beneficiary_payment_authority(conn, bid)
    conn.close()
    assert auth["payment_enabled"] is False
    assert "PRE_CATALOG_LEGACY_EXCLUDED" in auth["reason_codes"]


def test_manual_pending_post_catalog_disabled(tmp_path):
    db = str(tmp_path / "pending.db")
    _seed_and_activate(db)
    bid = _insert_beneficiary(
        db,
        name="MANUAL PENDING",
        employee="0000000555",
        account="5555555555",
        source_kind="ALTA_MANUAL",
        validation_status="MANUAL_PENDIENTE_VALIDACION",
        created_at="2026-09-01T12:00:00+00:00",
    )
    conn = connect(db)
    auth = evaluate_post_catalog_addition(
        conn, dict(conn.execute("SELECT * FROM nomina_banorte_beneficiaries WHERE id=?", (bid,)).fetchone())
    )
    conn.close()
    assert auth["payment_enabled"] is False


def test_catalog_employee_collision_blocked(tmp_path):
    db = str(tmp_path / "collision.db")
    _seed_and_activate(db, catalog_employee="0000000123", catalog_account="1234567890")
    conn = connect(db)
    beneficiary = {
        "id": 99999,
        "employee_number_effective": "0000000123",
        "account_number": "9876543210",
        "source_kind": "REPORTE_DETALLADO",
        "validation_status": "IMPORTADO_EXITOSO",
        "record_status": "ACTIVO",
        "manual_effective_from_account": 0,
        "created_at": "2026-09-01T12:00:00+00:00",
    }
    auth = evaluate_post_catalog_addition(conn, beneficiary)
    conn.close()
    assert auth["payment_enabled"] is False
    assert "CATALOG_EMPLOYEE_COLLISION" in auth["reason_codes"]


def test_next_catalog_activation_drops_provisional_addition(tmp_path):
    db = str(tmp_path / "next.db")
    version_id, _ = _seed_and_activate(db)
    bid = _insert_beneficiary(
        db,
        name="PROVISIONAL ADDITION",
        employee="0000000444",
        account="4444444444",
        source_kind="REPORTE_DETALLADO",
        validation_status="IMPORTADO_EXITOSO",
        created_at="2026-09-01T12:00:00+00:00",
    )
    conn = connect(db)
    assert evaluate_post_catalog_addition(
        conn, dict(conn.execute("SELECT * FROM nomina_banorte_beneficiaries WHERE id=?", (bid,)).fetchone())
    )["payment_enabled"]
    conn.close()
    payload = "\n".join(
        [
            "FECHA: 21/ago./2026",
            "EMISORA: 67059 EMPRESA SINTETICA",
            "",
            "|".join(CATALOG_HEADER_V1) + "|",
            "|".join(_catalog_row("0000000001", "PERSONA CATALOGO", "1111111111")) + "|",
        ]
    ).encode("utf-8")
    staged = stage_catalog_version(db, raw=payload, filename="emp2.txt", actor="admin")
    analyze_catalog_version(db, staged["id"], actor="admin")
    pre_reconcile_catalog_version(db, staged["id"], actor="admin")
    conn = connect(db)
    conn.execute(
        "UPDATE nomina_banorte_catalog_versions SET status='READY_FOR_REVIEW' WHERE id=?",
        (staged["id"],),
    )
    conn.commit()
    conn.close()
    activate_catalog_version(db, int(staged["id"]), actor="admin")
    conn = connect(db)
    auth = evaluate_post_catalog_addition(
        conn, dict(conn.execute("SELECT * FROM nomina_banorte_beneficiaries WHERE id=?", (bid,)).fetchone())
    )
    conn.close()
    assert auth["payment_enabled"] is False


def test_add_payment_rejects_pre_catalog_legacy(tmp_path):
    db = str(tmp_path / "addpay.db")
    _seed_and_activate(db)
    bid = _insert_beneficiary(
        db,
        name="LEGACY ADD PAY",
        employee="0000000333",
        account="3333333333",
        source_kind="ALTA_MANUAL",
        validation_status="IMPORTADO_EXITOSO",
        created_at="2026-01-01T00:00:00+00:00",
    )
    shell = create_manual_draft_shell(db, "u", names_text="", amounts_text="")
    with pytest.raises(ValueError, match="catalog_authority_required|beneficiary_not_usable"):
        add_draft_payment(
            db,
            int(shell["draft"]["id"]),
            "u",
            1,
            beneficiary_id=bid,
            amount_final="100.00",
        )
