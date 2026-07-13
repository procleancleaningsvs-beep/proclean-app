from __future__ import annotations

import sqlite3

from modules.nomina.banorte.schema import BANORTE_TABLES
from modules.nomina.db import ensure_nomina_tables


def test_banorte_migration_preserves_prior_nomina_data(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    ensure_nomina_tables(conn)
    conn.execute(
        """
        INSERT INTO nomina_asistencia_imports (
            semana, fecha_inicio, fecha_fin, cliente, coordinador,
            filename, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-W01",
            "2026-01-01",
            "2026-01-07",
            "Cliente Demo",
            "Coord",
            "x.xlsx",
            "ok",
            "2026-01-01T00:00:00",
            "2026-01-01T00:00:00",
        ),
    )
    conn.commit()
    before = conn.execute("SELECT COUNT(*) FROM nomina_asistencia_imports").fetchone()[0]
    ensure_nomina_tables(conn)
    after = conn.execute("SELECT COUNT(*) FROM nomina_asistencia_imports").fetchone()[0]
    assert before == after == 1
    for table in BANORTE_TABLES:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        assert row is not None, table
    conn.close()


def test_ensure_banorte_tables_idempotent(tmp_path):
    db = tmp_path / "idem.db"
    conn = sqlite3.connect(db)
    ensure_nomina_tables(conn)
    ensure_nomina_tables(conn)
    names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'nomina_banorte_%'"
        ).fetchall()
    }
    assert set(BANORTE_TABLES) <= names
    conn.close()
