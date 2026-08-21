from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from modules.nomina.banorte.catalog_activation import (
    activate_catalog_version,
    catalog_activation_check,
    rollback_catalog_activation,
)
from modules.nomina.banorte.catalog_lifecycle import legacy_authority_allowed
from modules.nomina.banorte.catalog_parser import CATALOG_HEADER_V1
from modules.nomina.banorte.catalog_reconciliation import pre_reconcile_catalog_version
from modules.nomina.banorte.catalog_service import analyze_catalog_version, stage_catalog_version
from modules.nomina.banorte.payment_authority import evaluate_payment_authority, rehydrate_row_authority
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.banorte.validators import normalize_name
from modules.nomina.db import ensure_nomina_tables


def _row() -> list[str]:
    return [
        "0000000001",
        "PERSONA AUTHORITY",
        "01/01/2026",
        "20/08/2026",
        "ADMIN",
        "01/01/1990",
        "AUT900101AA1",
        "1000",
        "900",
        "NUEVO LEON",
        "01/01/2020",
        "SEMANAL",
        "NUEVO LEON",
        "CUENTA BANORTE",
        "1111111111",
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


def _payload() -> bytes:
    return "\n".join(
        [
            "FECHA: 20/ago./2026",
            "EMISORA: 67059 EMPRESA SINTETICA",
            "",
            "|".join(CATALOG_HEADER_V1) + "|",
            "|".join(_row()) + "|",
        ]
    ).encode("utf-8")


def _seed_ready_catalog(db: str) -> int:
    conn = sqlite3.connect(db)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original,nombre_normalizado,curp,employee_number_requested,
            employee_number_effective,account_number,source_kind,validation_status,
            record_status,banorte_employee_substituted,manual_effective_from_account,
            imported_at,imported_by,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "PERSONA AUTHORITY",
            normalize_name("PERSONA AUTHORITY"),
            "AUT900101AA1",
            "0000000001",
            "0000000001",
            "1111111111",
            "ALTA_MANUAL",
            "IMPORTADO_EXITOSO",
            "ACTIVO",
            0,
            0,
            "t",
            "admin",
            "t",
            "t",
        ),
    )
    conn.commit()
    conn.close()
    staged = stage_catalog_version(db, raw=_payload(), filename="auth.txt", actor="admin")
    analyze_catalog_version(db, staged["id"], actor="admin")
    pre_reconcile_catalog_version(db, staged["id"], actor="admin")
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE nomina_banorte_catalog_versions SET status='READY_FOR_REVIEW' WHERE id=?",
        (staged["id"],),
    )
    conn.commit()
    conn.close()
    return int(staged["id"])


def test_legacy_allowed_before_first_activation(tmp_path):
    db = str(tmp_path / "life.db")
    conn = sqlite3.connect(db)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    conn.commit()
    assert legacy_authority_allowed(conn) is True
    conn.close()


def test_activation_check_can_activate_when_ready(tmp_path):
    db = str(tmp_path / "act.db")
    version_id = _seed_ready_catalog(db)
    check = catalog_activation_check(db, version_id)
    assert check["can_activate"] is True
    assert "RELEASE_2B" not in " ".join(check["blocker_codes"])


def test_account_tampering_rehydrate_ignores_client_snapshot(tmp_path):
    db = str(tmp_path / "tamper.db")
    version_id = _seed_ready_catalog(db)
    activate_catalog_version(db, version_id, actor="admin")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    person_id = conn.execute(
        "SELECT id FROM nomina_banorte_catalog_persons WHERE version_id=?",
        (version_id,),
    ).fetchone()[0]
    beneficiary_id = conn.execute(
        "SELECT beneficiary_id FROM nomina_banorte_catalog_reconciliations WHERE person_id=?",
        (person_id,),
    ).fetchone()[0]
    draft_id = conn.execute(
        """
        INSERT INTO nomina_banorte_export_drafts (
            created_by,updated_by,created_at,updated_at,origin_kind,calculo_id,
            origin_updated_at,origin_hash,status,revision,catalog_mode,catalog_version_id
        ) VALUES ('a','a','t','t','MANUAL_CAPTURE',NULL,NULL,'h','OPEN',1,'CATALOG',?)
        """,
        (version_id,),
    ).lastrowid
    row_id = conn.execute(
        """
        INSERT INTO nomina_banorte_export_draft_rows (
            draft_id,position,nombre_recibido,beneficiary_id,employee_number_snapshot,
            account_number_snapshot,amount_original_cents,amount_final_cents,included,
            match_kind,row_state,warnings_json,user_decision_json,catalog_person_id
        ) VALUES (?,1,'PERSONA AUTHORITY',?, '0000000001','9999999999',10000,10000,1,'CATALOG','OK','[]','{}',?)
        """,
        (draft_id, beneficiary_id, person_id),
    ).lastrowid
    conn.commit()
    draft = dict(
        conn.execute("SELECT * FROM nomina_banorte_export_drafts WHERE id=?", (draft_id,)).fetchone()
    )
    row = dict(conn.execute("SELECT * FROM nomina_banorte_export_draft_rows WHERE id=?", (row_id,)).fetchone())
    hydrated = rehydrate_row_authority(conn, draft=draft, row=row)
    assert hydrated["account_number_snapshot"] == "1111111111"
    assert hydrated["account_number_snapshot"] != "9999999999"
    conn.close()


def test_fail_closed_after_activation_history_without_active(tmp_path):
    db = str(tmp_path / "failclosed.db")
    version_id = _seed_ready_catalog(db)
    activate_catalog_version(db, version_id, actor="admin")
    rollback_catalog_activation(db, version_id, actor="admin")
    conn = sqlite3.connect(db)
    assert legacy_authority_allowed(conn) is False
    authority = evaluate_payment_authority(conn=conn)
    assert authority["fail_closed"] is True
    conn.close()
