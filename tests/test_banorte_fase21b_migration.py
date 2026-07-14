"""Banorte Fase 2.1B — excluded_at/by migration."""

from __future__ import annotations

import sqlite3

import pytest

from modules.nomina.banorte import repository as banorte_repo
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.db import ensure_nomina_tables


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


@pytest.fixture
def fresh_db(tmp_path):
    path = tmp_path / "f21b.db"
    conn = banorte_repo.connect(path)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    conn.commit()
    yield conn, path
    conn.close()


def test_excluded_columns_present(fresh_db):
    conn, _ = fresh_db
    cols = _cols(conn, "nomina_banorte_export_draft_rows")
    assert "excluded_at" in cols
    assert "excluded_by" in cols


def test_excluded_migration_idempotent(fresh_db):
    conn, _ = fresh_db
    ensure_banorte_tables(conn)
    ensure_banorte_tables(conn)
    assert "excluded_at" in _cols(conn, "nomina_banorte_export_draft_rows")
