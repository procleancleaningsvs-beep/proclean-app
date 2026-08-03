"""GIS Nóminas — schema idempotente."""

from __future__ import annotations

import sqlite3

import pytest

from modules.gestion_idse_sua.nominas.schema import GIS_NOMINA_TABLES, ensure_gis_nominas_tables


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "gis_schema.db"
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


def test_tables_created_idempotently(conn):
    ensure_gis_nominas_tables(conn)
    conn.commit()
    first = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'gis_%'"
        )
    }
    ensure_gis_nominas_tables(conn)
    conn.commit()
    second = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'gis_%'"
        )
    }
    assert first == second
    for table in GIS_NOMINA_TABLES:
        assert table in first


def test_seed_planta_cliente_idempotent(conn):
    ensure_gis_nominas_tables(conn)
    conn.commit()
    count1 = conn.execute("SELECT COUNT(*) FROM gis_planta_cliente").fetchone()[0]
    ensure_gis_nominas_tables(conn)
    conn.commit()
    count2 = conn.execute("SELECT COUNT(*) FROM gis_planta_cliente").fetchone()[0]
    assert count1 == count2 >= 3
    row = conn.execute(
        "SELECT cliente FROM gis_planta_cliente WHERE planta_normalizada = 'FLOTADO'"
    ).fetchone()
    assert row["cliente"] == "VITROFLEX"


def test_no_drop_on_rerun(conn):
    ensure_gis_nominas_tables(conn)
    conn.execute(
        "INSERT INTO gis_nomina_imports (original_filename, file_hash, uploaded_at, status) VALUES (?,?,?,?)",
        ("a.xlsx", "abc", "2026-01-01", "uploaded"),
    )
    conn.commit()
    ensure_gis_nominas_tables(conn)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM gis_nomina_imports").fetchone()[0] == 1


def test_import_archive_and_file_columns_are_migrated(conn):
    conn.execute(
        """
        CREATE TABLE gis_nomina_imports (
            id INTEGER PRIMARY KEY,
            original_filename TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            uploaded_by TEXT,
            uploaded_at TEXT NOT NULL,
            status TEXT NOT NULL
        )
        """
    )
    ensure_gis_nominas_tables(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(gis_nomina_imports)")}
    assert {"file_content", "archived_at", "archived_by", "archive_reason"} <= columns


def test_weekly_result_visibility_columns_and_audit_table_exist(conn):
    ensure_gis_nominas_tables(conn)
    result_columns = {row[1] for row in conn.execute("PRAGMA table_info(gis_nomina_results)")}
    assert {"hidden_at", "hidden_by", "hidden_reason"} <= result_columns
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gis_workspace_audit'"
    ).fetchone()
