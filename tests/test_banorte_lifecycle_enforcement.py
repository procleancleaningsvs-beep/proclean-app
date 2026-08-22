from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from flask import Flask, g

from modules.nomina.banorte.catalog_activation import (
    activate_catalog_version,
    rollback_catalog_activation,
)
from modules.nomina.banorte.catalog_lifecycle import legacy_authority_allowed
from modules.nomina.banorte.catalog_parser import CATALOG_HEADER_V1
from modules.nomina.banorte.catalog_reconciliation import pre_reconcile_catalog_version
from modules.nomina.banorte.catalog_row_adapter import prepare_capture_rows
from modules.nomina.banorte.catalog_service import analyze_catalog_version, stage_catalog_version
from modules.nomina.banorte.draft_repository import (
    add_draft_payment,
    create_manual_draft_shell,
    exclude_draft_row,
    restore_last_excluded,
    save_draft_rows,
    undo_last_draft_mutation,
)
from modules.nomina.banorte.payment_authority import enforce_prepared_rows_catalog_authority
from modules.nomina.banorte.prepare_service import prepare_draft_rows
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.banorte.rows_capture import parse_capture_input
from modules.nomina.banorte.validators import normalize_name
from modules.nomina.blueprint import register_nomina
from modules.nomina.db import ensure_nomina_tables


def _make_app(db_path: Path, role: str) -> Flask:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[1]
    app = Flask(
        __name__,
        template_folder=str(repo / "templates"),
        static_folder=str(repo / "static"),
    )
    app.config.update(
        TESTING=True,
        SECRET_KEY="lifecycle-test-secret",
        DATABASE=str(db_path),
    )
    conn = sqlite3.connect(app.config["DATABASE"])
    ensure_nomina_tables(conn)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, role TEXT, password_hash TEXT, created_at TEXT)"
    )
    conn.execute(
        "INSERT INTO users (id,username,role,password_hash,created_at) VALUES (1,'tester',?,'x','t')",
        (role,),
    )
    conn.commit()
    conn.close()
    register_nomina(app)

    @app.before_request
    def _auth():
        g.user = {"id": 1, "username": "tester", "role": role}

    return app


def _catalog_row(name: str = "PERSONA LIFECYCLE") -> list[str]:
    return [
        "0000000001",
        name,
        "01/01/2026",
        "20/08/2026",
        "ADMIN",
        "01/01/1990",
        "LIF900101AA1",
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


def _seed_ready_catalog(db: str, *, beneficiary_account: str = "1111111111") -> tuple[int, int]:
    conn = sqlite3.connect(db)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
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
            "PERSONA LIFECYCLE",
            normalize_name("PERSONA LIFECYCLE"),
            "LIF900101AA1",
            "0000000001",
            "0000000001",
            beneficiary_account,
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
    beneficiary_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    payload = "\n".join(
        [
            "FECHA: 20/ago./2026",
            "EMISORA: 67059 EMPRESA SINTETICA",
            "",
            "|".join(CATALOG_HEADER_V1) + "|",
            "|".join(_catalog_row()) + "|",
        ]
    ).encode("utf-8")
    staged = stage_catalog_version(db, raw=payload, filename="life.txt", actor="admin")
    analyze_catalog_version(db, staged["id"], actor="admin")
    pre_reconcile_catalog_version(db, staged["id"], actor="admin")
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE nomina_banorte_catalog_versions SET status='READY_FOR_REVIEW' WHERE id=?",
        (staged["id"],),
    )
    conn.commit()
    conn.close()
    return int(staged["id"]), beneficiary_id


def _active_catalog(db: str) -> tuple[int, int]:
    version_id, beneficiary_id = _seed_ready_catalog(db)
    activate_catalog_version(db, version_id, actor="admin")
    return version_id, beneficiary_id


def test_undo_post_activation_stale_restore_is_needs_review(tmp_path):
    db = str(tmp_path / "undo.db")
    version_id, beneficiary_id = _active_catalog(db)
    shell = create_manual_draft_shell(db, "nomina", names_text="", amounts_text="")
    rows = prepare_capture_rows(
        db,
        parse_capture_input(
            rows_payload=[{"name_raw": "PERSONA LIFECYCLE", "amount_raw": "100.00"}]
        ),
        origin_kind="MANUAL_CAPTURE",
    )
    draft = save_draft_rows(db, int(shell["draft"]["id"]), "nomina", 1, rows)
    assert draft["rows"][0]["row_state"] == "OK"
    excluded = exclude_draft_row(
        db, int(draft["id"]), int(draft["rows"][0]["id"]), "nomina", int(draft["revision"])
    )
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE nomina_banorte_beneficiaries SET account_number='2222222222' WHERE id=?",
        (beneficiary_id,),
    )
    conn.commit()
    conn.close()
    undone = undo_last_draft_mutation(
        db, int(excluded["id"]), "nomina", int(excluded["revision"])
    )
    row = undone["rows"][0]
    assert row["row_state"] == "NEEDS_REVIEW"
    assert int(row["included"]) == 0


def test_restore_post_activation_revalidates_authority(tmp_path):
    db = str(tmp_path / "restore.db")
    _active_catalog(db)
    shell = create_manual_draft_shell(db, "nomina", names_text="", amounts_text="")
    rows = prepare_capture_rows(
        db,
        parse_capture_input(
            rows_payload=[{"name_raw": "PERSONA LIFECYCLE", "amount_raw": "50.00"}]
        ),
        origin_kind="MANUAL_CAPTURE",
    )
    draft = save_draft_rows(db, int(shell["draft"]["id"]), "nomina", 1, rows)
    excluded = exclude_draft_row(
        db, int(draft["id"]), int(draft["rows"][0]["id"]), "nomina", int(draft["revision"])
    )
    restored = restore_last_excluded(db, int(excluded["id"]), "nomina", int(excluded["revision"]))
    assert restored["rows"][0]["row_state"] == "OK"


def test_restore_ignores_tampered_account_snapshot(tmp_path):
    db = str(tmp_path / "tamper-restore.db")
    _active_catalog(db)
    shell = create_manual_draft_shell(db, "nomina", names_text="", amounts_text="")
    rows = prepare_capture_rows(
        db,
        parse_capture_input(
            rows_payload=[{"name_raw": "PERSONA LIFECYCLE", "amount_raw": "75.00"}]
        ),
        origin_kind="MANUAL_CAPTURE",
    )
    draft = save_draft_rows(db, int(shell["draft"]["id"]), "nomina", 1, rows)
    conn = sqlite3.connect(db)
    conn.execute(
        """
        UPDATE nomina_banorte_export_draft_rows
        SET account_number_snapshot='9999999999', excluded_at=?, excluded_by=?
        WHERE draft_id=?
        """,
        ("t", "nomina", int(draft["id"])),
    )
    conn.commit()
    conn.close()
    restored = restore_last_excluded(db, int(draft["id"]), "nomina", int(draft["revision"]))
    assert restored["rows"][0]["account_number_snapshot"] == "1111111111"


def test_pre_activation_legacy_prepare_still_ok(tmp_path):
    db = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    conn.commit()
    conn.close()
    assert legacy_authority_allowed(sqlite3.connect(db)) is True
    draft = {"catalog_mode": "LEGACY", "catalog_version_id": None}
    rows = prepare_draft_rows(
        db,
        [
            {
                "position": 1,
                "nombre_recibido": "LEGACY PERSON",
                "amount_original_cents": 10000,
                "amount_final_cents": 10000,
                "included": 1,
                "match_kind": "NONE",
                "row_state": "NEEDS_REVIEW",
                "warnings": [],
                "user_decision": {},
            }
        ],
        origin_kind="CALCULO_RUN",
    )
    enforced = enforce_prepared_rows_catalog_authority(db, draft, rows)
    assert enforced == rows


def test_post_activation_calculo_rows_require_catalog_authority(tmp_path):
    db = str(tmp_path / "calculo.db")
    version_id, beneficiary_id = _active_catalog(db)
    draft = {"catalog_mode": "CATALOG", "catalog_version_id": version_id}
    rows = prepare_draft_rows(
        db,
        [
            {
                "position": 1,
                "nombre_recibido": "PERSONA LIFECYCLE",
                "amount_original_cents": 10000,
                "amount_final_cents": 10000,
                "included": 1,
                "match_kind": "EXACT",
                "row_state": "OK",
                "warnings": [],
                "user_decision": {},
                "beneficiary_id": beneficiary_id,
            }
        ],
        origin_kind="CALCULO_RUN",
    )
    enforced = enforce_prepared_rows_catalog_authority(db, draft, rows)
    assert enforced[0]["row_state"] in {"OK", "NEEDS_REVIEW"}
    if enforced[0]["row_state"] == "OK":
        assert enforced[0].get("catalog_person_id") is not None


def test_add_payment_post_activation_requires_catalog(tmp_path):
    db = str(tmp_path / "addpay.db")
    _active_catalog(db)
    conn = sqlite3.connect(db)
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original,nombre_normalizado,employee_number_effective,account_number,
            source_kind,validation_status,record_status,manual_effective_from_account,
            imported_at,imported_by,created_at,updated_at
        ) VALUES (?,?,?,?, 'ALTA_MANUAL','IMPORTADO_EXITOSO','ACTIVO',0,'t','u','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')
        """,
        ("LEGACY PRE ACTIVATION", normalize_name("LEGACY PRE ACTIVATION"), "0000000311", "3111111111"),
    )
    legacy_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    shell = create_manual_draft_shell(db, "nomina", names_text="", amounts_text="")
    draft_id = int(shell["draft"]["id"])
    with pytest.raises(ValueError, match="catalog_authority_required|beneficiary_not_usable"):
        add_draft_payment(
            db,
            draft_id,
            "nomina",
            1,
            beneficiary_id=legacy_id,
            amount_final="100.00",
        )


@pytest.mark.parametrize(
    ("role", "status"),
    [("admin", 200), ("nomina", 403), ("usuario", 403)],
)
def test_activate_permissions(tmp_path, role, status):
    db_path = tmp_path / f"act-{role}.db"
    app = _make_app(db_path, role)
    db = str(db_path)
    version_id, _ = _seed_ready_catalog(db)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["banorte_csrf_token"] = "x" * 32
    resp = client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/activate",
        json={"csrf_token": "x" * 32},
    )
    assert resp.status_code == status
    if status == 200:
        assert "no-store" in resp.headers.get("Cache-Control", "")


def test_activate_requires_csrf(tmp_path):
    db_path = tmp_path / "csrf.db"
    app = _make_app(db_path, "admin")
    db = str(db_path)
    version_id, _ = _seed_ready_catalog(db)
    client = app.test_client()
    resp = client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/activate",
        json={},
    )
    assert resp.status_code == 403


def test_fail_closed_after_first_activation_and_rollback(tmp_path):
    db = str(tmp_path / "failclosed.db")
    version_id, _ = _seed_ready_catalog(db)
    activate_catalog_version(db, version_id, actor="admin")
    rollback_catalog_activation(db, version_id, actor="admin")
    conn = sqlite3.connect(db)
    assert legacy_authority_allowed(conn) is False
    conn.close()


def test_only_one_active_version(tmp_path):
    db = str(tmp_path / "single-active.db")
    version_id, _ = _seed_ready_catalog(db)
    activate_catalog_version(db, version_id, actor="admin")
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM nomina_banorte_catalog_versions WHERE status='ACTIVE'").fetchone()[0] == 1
    conn.close()
