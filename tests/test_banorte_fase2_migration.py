from __future__ import annotations

import sqlite3

import pytest

from modules.nomina.banorte import repository as banorte_repo
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.db import ensure_nomina_tables


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _indexes(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}


@pytest.fixture
def fresh_db(tmp_path):
    path = tmp_path / "fresh.db"
    conn = banorte_repo.connect(path)
    ensure_nomina_tables(conn)
    conn.commit()
    yield conn, path
    conn.close()


def test_fase2_columns_and_tables_on_new_db(fresh_db):
    conn, _ = fresh_db
    assert "calculo_id" in _cols(conn, "nomina_banorte_exports")
    assert "draft_id" in _cols(conn, "nomina_banorte_exports")
    assert "capture_origin" in _cols(conn, "nomina_banorte_exports")
    assert "calculo_row_id" in _cols(conn, "nomina_banorte_export_items")
    ben = _cols(conn, "nomina_banorte_beneficiaries")
    assert "replace_reason" in ben
    assert "replaced_by" in ben
    assert "replaced_at" in ben
    assert "manual_effective_from_account" in ben
    assert "nomina_banorte_export_drafts" in {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "nomina_banorte_export_draft_rows" in {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    draft_cols = _cols(conn, "nomina_banorte_export_drafts")
    assert "revision" in draft_cols
    assert "notes_json" not in draft_cols
    assert "reconciliation_json" not in draft_cols
    idxs = _indexes(conn)
    assert "uq_banorte_draft_open_user_calculo" in idxs
    assert "uq_banorte_exports_draft_id" in idxs


def test_fase2_migration_idempotent(fresh_db):
    conn, path = fresh_db
    ensure_banorte_tables(conn)
    conn.commit()
    ensure_banorte_tables(conn)
    conn.commit()
    assert "draft_id" in _cols(conn, "nomina_banorte_exports")


def test_fase2_migration_on_fase1_db_preserves_export_blob(tmp_path):
    path = tmp_path / "fase1.db"
    conn = banorte_repo.connect(path)
    # Simulate pre-fase2: create minimal banorte via ensure then strip new cols by using old path —
    # ensure_nomina_tables already applies fase2; insert export and re-ensure.
    ensure_nomina_tables(conn)
    blob = b"HISTORIC-PAG-BYTES"
    conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, employee_number_effective, account_number,
            source_kind, validation_status, record_status,
            imported_at, imported_by, created_at, updated_at
        ) VALUES ('A','A','1','111','ALTA_MANUAL','IMPORTADO_EXITOSO','ACTIVO',
                  't','u','t','t')
        """
    )
    ben_id = conn.execute("SELECT id FROM nomina_banorte_beneficiaries").fetchone()[0]
    conn.execute(
        """
        INSERT INTO nomina_banorte_exports (
            created_by, created_at, timezone, layout_date, layout_date_auto,
            date_override_confirmed, consecutive, filename, payment_count, total_cents,
            capture_origin, incidents_json, manual_row_count, aliases_used_json,
            recommendations_accepted_json, warnings_ignored_json,
            duplicate_consecutive_confirmed, file_sha256, file_size, file_blob, status
        ) VALUES ('u','t','America/Monterrey','20260101','20260101',0,'01','NI6705901.pag',1,100,
                  'PASTE_LISTS','[]',0,'[]','[]','[]',0,'abc',?,?,'GENERATED')
        """,
        (len(blob), blob),
    )
    export_id = int(conn.execute("SELECT id FROM nomina_banorte_exports").fetchone()[0])
    conn.execute(
        """
        INSERT INTO nomina_banorte_export_items (
            export_id, position, nombre_recibido, beneficiary_id, employee_number_effective,
            account_number, amount_cents, match_kind, validation_status, record_status,
            is_manual_beneficiary, warnings_json, user_decision_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (export_id, 1, "A", ben_id, "1", "111", 100, "EXACT", "IMPORTADO_EXITOSO", "ACTIVO", 0, "[]", "{}"),
    )
    conn.commit()
    ensure_banorte_tables(conn)
    conn.commit()
    row = conn.execute(
        "SELECT file_blob, file_sha256, calculo_id, draft_id FROM nomina_banorte_exports WHERE id=?",
        (export_id,),
    ).fetchone()
    assert bytes(row["file_blob"]) == blob
    assert row["file_sha256"] == "abc"
    assert row["calculo_id"] is None
    assert row["draft_id"] is None
    conn.close()


def test_draft_check_included_requires_positive_final(fresh_db):
    conn, _ = fresh_db
    conn.execute(
        """
        INSERT INTO nomina_banorte_export_drafts (
            created_by, updated_by, created_at, updated_at, origin_kind, calculo_id,
            origin_updated_at, origin_hash, status, revision
        ) VALUES ('u','u','t','t','CALCULO_RUN',1,'t','hash1','OPEN',1)
        """
    )
    draft_id = int(conn.execute("SELECT id FROM nomina_banorte_export_drafts").fetchone()[0])
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO nomina_banorte_export_draft_rows (
                draft_id, position, nombre_recibido, amount_original_cents, amount_final_cents,
                included, match_kind, row_state, warnings_json, user_decision_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (draft_id, 1, "X", 100, 0, 1, "NONE", "OK", "[]", "{}"),
        )


def test_unique_open_draft_per_user_calculo(fresh_db):
    conn, _ = fresh_db
    conn.execute(
        """
        INSERT INTO nomina_banorte_export_drafts (
            created_by, updated_by, created_at, updated_at, origin_kind, calculo_id,
            origin_updated_at, origin_hash, status, revision
        ) VALUES ('u','u','t','t','CALCULO_RUN',9,'t','h1','OPEN',1)
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO nomina_banorte_export_drafts (
                created_by, updated_by, created_at, updated_at, origin_kind, calculo_id,
                origin_updated_at, origin_hash, status, revision
            ) VALUES ('u','u','t','t','CALCULO_RUN',9,'t','h2','OPEN',1)
            """
        )


def test_unique_export_per_draft_id(fresh_db):
    conn, _ = fresh_db
    conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, employee_number_effective, account_number,
            source_kind, validation_status, record_status,
            imported_at, imported_by, created_at, updated_at
        ) VALUES ('A','A','1','111','ALTA_MANUAL','IMPORTADO_EXITOSO','ACTIVO','t','u','t','t')
        """
    )
    blob = b"x"
    conn.execute(
        """
        INSERT INTO nomina_banorte_exports (
            created_by, created_at, timezone, layout_date, layout_date_auto,
            date_override_confirmed, consecutive, filename, payment_count, total_cents,
            capture_origin, incidents_json, manual_row_count, aliases_used_json,
            recommendations_accepted_json, warnings_ignored_json,
            duplicate_consecutive_confirmed, file_sha256, file_size, file_blob, status, draft_id
        ) VALUES ('u','t','America/Monterrey','20260101','20260101',0,'01','f.pag',0,0,
                  'CALCULO_RUN','[]',0,'[]','[]','[]',0,'s',?,?,'GENERATED',42)
        """,
        (len(blob), blob),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO nomina_banorte_exports (
                created_by, created_at, timezone, layout_date, layout_date_auto,
                date_override_confirmed, consecutive, filename, payment_count, total_cents,
                capture_origin, incidents_json, manual_row_count, aliases_used_json,
                recommendations_accepted_json, warnings_ignored_json,
                duplicate_consecutive_confirmed, file_sha256, file_size, file_blob, status, draft_id
            ) VALUES ('u','t','America/Monterrey','20260101','20260101',0,'02','g.pag',0,0,
                      'CALCULO_RUN','[]',0,'[]','[]','[]',0,'s2',?,?,'GENERATED',42)
            """,
            (len(blob), blob),
        )
