from __future__ import annotations

from decimal import Decimal

import pytest

from modules.nomina.banorte.calculo_adapter import build_draft_rows_from_calculo
from modules.nomina.banorte.draft_repository import (
    DraftStaleError,
    create_draft_from_adapter,
    save_draft_rows,
)
from modules.nomina.banorte.export_service import (
    ExportBlockedError,
    generate_from_persistent_draft,
    get_export_blob,
)
from modules.nomina.banorte.prepare_service import prepare_draft_rows
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.db import ensure_nomina_tables
from tests.test_banorte_calculo_list import seed_calculo


def _seed_ben(db: str, *, emp: str, account: str, nombre: str) -> int:
    conn = connect(db)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, employee_number_effective, account_number,
            source_kind, validation_status, record_status,
            imported_at, imported_by, created_at, updated_at
        ) VALUES (?,?,?,?,'ALTA_MANUAL','IMPORTADO_EXITOSO','ACTIVO','t','u','t','t')
        """,
        (nombre, nombre.upper(), emp, account),
    )
    conn.commit()
    bid = int(cur.lastrowid)
    conn.close()
    return bid


def _prepare_matched_draft(tmp_path, netos=(100.0,)):
    db_path = tmp_path / "gen.db"
    db = str(db_path)
    account = "1234567890"
    bid = _seed_ben(db, emp="10", account=account, nombre="TRABAJADOR 1")
    cid = seed_calculo(db_path, netos=list(netos), cuentas=[account] * len(netos))
    adapted = build_draft_rows_from_calculo(db, cid)
    draft = create_draft_from_adapter(db, "alice", adapted)
    prepared = prepare_draft_rows(db, draft["rows"])
    for p in prepared:
        if p.get("beneficiary_id") is None:
            p["beneficiary_id"] = bid
            p["included"] = 1
            p["row_state"] = "OK"
            p["match_kind"] = "EXACT"
            p["account_number_snapshot"] = account
            p["employee_number_snapshot"] = "10"
            p["amount_final_cents"] = p["amount_original_cents"]
    draft = save_draft_rows(db, draft["id"], "alice", draft["revision"], prepared)
    return db, draft, cid


def test_generate_idempotent_same_draft(tmp_path):
    db, draft, cid = _prepare_matched_draft(tmp_path)
    r1 = generate_from_persistent_draft(
        db, "alice", draft["id"], expected_revision=draft["revision"], consecutive="01"
    )
    r2 = generate_from_persistent_draft(
        db, "alice", draft["id"], expected_revision=draft["revision"], consecutive="01"
    )
    assert r1.export_id == r2.export_id
    assert r1.file_sha256 == r2.file_sha256
    assert r1.file_bytes == r2.file_bytes
    conn = connect(db)
    n = conn.execute("SELECT COUNT(*) AS c FROM nomina_banorte_exports WHERE draft_id=?", (draft["id"],)).fetchone()
    assert int(n["c"]) == 1
    exp = conn.execute("SELECT calculo_id, draft_id, capture_origin FROM nomina_banorte_exports WHERE id=?", (r1.export_id,)).fetchone()
    assert int(exp["calculo_id"]) == cid
    assert int(exp["draft_id"]) == draft["id"]
    assert exp["capture_origin"] == "CALCULO_RUN"
    item = conn.execute("SELECT calculo_row_id FROM nomina_banorte_export_items WHERE export_id=?", (r1.export_id,)).fetchone()
    assert item["calculo_row_id"] is not None
    conn.close()
    name, blob, digest = get_export_blob(db, r1.export_id)
    assert blob == r1.file_bytes
    assert digest == r1.file_sha256


def test_generate_stale_revision_blocked(tmp_path):
    db, draft, _ = _prepare_matched_draft(tmp_path)
    with pytest.raises(DraftStaleError):
        generate_from_persistent_draft(
            db, "alice", draft["id"], expected_revision=draft["revision"] - 1, consecutive="02"
        )
    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) AS c FROM nomina_banorte_exports").fetchone()["c"] == 0
    assert conn.execute("SELECT status FROM nomina_banorte_export_drafts WHERE id=?", (draft["id"],)).fetchone()["status"] == "OPEN"
    conn.close()


def test_generate_total_matches_included(tmp_path):
    db, draft, _ = _prepare_matched_draft(tmp_path, netos=(100.50,))
    r = generate_from_persistent_draft(
        db, "alice", draft["id"], expected_revision=draft["revision"], consecutive="03"
    )
    assert r.total_cents == 10050
    assert r.payment_count == 1


def test_rollback_leaves_no_export_on_block(tmp_path):
    db, draft, _ = _prepare_matched_draft(tmp_path)
    # force beneficiary inactive after save
    conn = connect(db)
    conn.execute("UPDATE nomina_banorte_beneficiaries SET record_status='INACTIVO_REEMPLAZADO'")
    conn.commit()
    conn.close()
    with pytest.raises(ExportBlockedError):
        generate_from_persistent_draft(
            db, "alice", draft["id"], expected_revision=draft["revision"], consecutive="04"
        )
    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) AS c FROM nomina_banorte_exports").fetchone()["c"] == 0
    assert conn.execute("SELECT status FROM nomina_banorte_export_drafts").fetchone()["status"] == "OPEN"
    conn.close()
