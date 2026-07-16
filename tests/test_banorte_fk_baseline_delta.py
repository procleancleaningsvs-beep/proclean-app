"""Fase 2.3A — FK baseline/delta must not block on identical non-Banorte orphans."""

from __future__ import annotations

import pytest

from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import (
    BANORTE_CHILD_TABLES_FOR_FK_CHECK,
    _assert_fk_delta_ok,
    _fk_check_tuples,
    _migrate_drafts_excel_nomina,
    ensure_banorte_tables,
)
from modules.nomina.db import ensure_nomina_tables


def _seed_drafts_without_excel(conn) -> None:
    """Create drafts table CHECK without EXCEL_NOMINA so migration must rebuild."""
    conn.execute("DROP TABLE IF EXISTS nomina_banorte_export_draft_rows")
    conn.execute("DROP TABLE IF EXISTS nomina_banorte_export_drafts")
    conn.execute(
        """
        CREATE TABLE nomina_banorte_export_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_by TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            origin_kind TEXT NOT NULL
                CHECK (origin_kind IN ('CALCULO_RUN', 'MANUAL_CAPTURE')),
            calculo_id INTEGER,
            origin_updated_at TEXT,
            origin_hash TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN ('OPEN', 'GENERATED', 'ABANDONED', 'BLOCKED_DRIFT')),
            revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
            consecutive_pref TEXT,
            layout_date_pref TEXT,
            source_filename TEXT,
            source_sha256 TEXT,
            source_sheet TEXT,
            source_file_size INTEGER,
            CHECK (
                (origin_kind = 'CALCULO_RUN' AND calculo_id IS NOT NULL)
                OR (origin_kind = 'MANUAL_CAPTURE' AND calculo_id IS NULL)
            ),
            CHECK (length(origin_hash) > 0)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE nomina_banorte_export_draft_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            calculo_row_id INTEGER,
            nombre_recibido TEXT NOT NULL,
            nss_snapshot TEXT,
            banco_snapshot TEXT,
            beneficiary_id INTEGER,
            employee_number_snapshot TEXT,
            account_number_snapshot TEXT,
            amount_original_cents INTEGER NOT NULL,
            amount_final_cents INTEGER NOT NULL,
            included INTEGER NOT NULL,
            match_kind TEXT NOT NULL,
            alias_id INTEGER,
            row_state TEXT NOT NULL,
            warnings_json TEXT NOT NULL,
            user_decision_json TEXT NOT NULL,
            excluded_at TEXT,
            excluded_by TEXT
        )
        """
    )


def _seed_asistencia_orphan(conn) -> None:
    """Pre-existing non-Banorte orphan matching production shape (asistencia FK)."""
    # Real asistencia tables already exist after ensure_nomina_tables; insert orphan with FK off.
    if conn.in_transaction:
        conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 0
    conn.execute(
        """
        INSERT INTO nomina_asistencia_rows (
            id, import_id, row_number, nombre_empleado
        ) VALUES (18, 0, 1, 'ORPHAN SYNTH')
        """
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def test_fk_check_tuples_accepts_tuple_and_row(tmp_path):
    db = str(tmp_path / "t.db")
    conn = connect(db)
    ensure_nomina_tables(conn)
    _seed_asistencia_orphan(conn)
    conn.commit()
    tuples = _fk_check_tuples(conn)
    assert ("nomina_asistencia_rows", 18, "nomina_asistencia_imports", 0) in tuples
    conn.row_factory = None
    tuples2 = _fk_check_tuples(conn)
    assert ("nomina_asistencia_rows", 18, "nomina_asistencia_imports", 0) in tuples2
    conn.close()


def test_identical_non_banorte_orphan_does_not_block_draft_migration(tmp_path):
    db = str(tmp_path / "a.db")
    conn = connect(db)
    ensure_nomina_tables(conn)
    _seed_drafts_without_excel(conn)
    _seed_asistencia_orphan(conn)
    conn.commit()
    baseline = _fk_check_tuples(conn)
    assert any(t[0] == "nomina_asistencia_rows" for t in baseline)

    _migrate_drafts_excel_nomina(conn)

    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='nomina_banorte_export_drafts'"
    ).fetchone()[0]
    assert "EXCEL_NOMINA" in sql
    after = _fk_check_tuples(conn)
    assert ("nomina_asistencia_rows", 18, "nomina_asistencia_imports", 0) in after
    conn.close()


def test_new_fk_violation_blocks(tmp_path):
    baseline = {("nomina_asistencia_rows", 18, "nomina_asistencia_imports", 0)}
    after = baseline | {("nomina_banorte_export_draft_rows", 1, "nomina_banorte_export_drafts", 0)}
    with pytest.raises(RuntimeError, match="fk_delta"):
        _assert_fk_delta_ok(baseline, after)


def test_new_non_banorte_violation_also_blocks():
    baseline = {("nomina_asistencia_rows", 18, "nomina_asistencia_imports", 0)}
    after = baseline | {("other_mod_rows", 1, "other_mod_parent", 0)}
    with pytest.raises(RuntimeError, match="fk_delta"):
        _assert_fk_delta_ok(baseline, after)


def test_banorte_child_tables_enumerated():
    assert "nomina_banorte_export_draft_rows" in BANORTE_CHILD_TABLES_FOR_FK_CHECK
    assert all("*" not in t for t in BANORTE_CHILD_TABLES_FOR_FK_CHECK)


def test_ensure_banorte_with_orphan_asistencia_completes(tmp_path):
    db = str(tmp_path / "full.db")
    conn = connect(db)
    ensure_nomina_tables(conn)
    _seed_drafts_without_excel(conn)
    _seed_asistencia_orphan(conn)
    conn.commit()
    ensure_banorte_tables(conn)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='nomina_banorte_export_drafts'"
    ).fetchone()[0]
    assert "EXCEL_NOMINA" in sql
    conn.close()
