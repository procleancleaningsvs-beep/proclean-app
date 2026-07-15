"""TDD: beneficiary migration must work with production row_factory=None (tuples)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

from modules.nomina.banorte.schema import (
    _beneficiaries_sql_allows_inactivo_manual,
    _migrate_beneficiaries_inactivo_manual,
    _sql_count_map,
    ensure_banorte_tables,
)
from tests.test_banorte_beneficiary_migration_fk import _seed_old_schema_with_children


def test_sql_count_map_accepts_tuples_and_rows():
    assert _sql_count_map([("ACTIVO", 2), ("INACTIVO_REEMPLAZADO", 1)]) == {
        "ACTIVO": 2,
        "INACTIVO_REEMPLAZADO": 1,
    }
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t(k TEXT, c INTEGER)")
    conn.execute("INSERT INTO t VALUES ('ACTIVO', 3)")
    rows = conn.execute("SELECT k, c FROM t").fetchall()
    assert _sql_count_map(rows) == {"ACTIVO": 3}
    conn.close()


def test_migration_succeeds_with_row_factory_none(tmp_path):
    path = str(tmp_path / "plain.db")
    before = _seed_old_schema_with_children(path)
    conn = sqlite3.connect(path)
    assert conn.row_factory is None
    conn.execute("PRAGMA foreign_keys = ON")
    assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1

    before_by_record = _sql_count_map(
        conn.execute(
            "SELECT record_status, COUNT(*) AS c FROM nomina_banorte_beneficiaries GROUP BY record_status"
        )
    )
    before_by_validation = _sql_count_map(
        conn.execute(
            "SELECT validation_status, COUNT(*) AS c FROM nomina_banorte_beneficiaries GROUP BY validation_status"
        )
    )

    _migrate_beneficiaries_inactivo_manual(conn)

    assert _beneficiaries_sql_allows_inactivo_manual(conn)
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_beneficiaries").fetchone()[0]) == before[
        "beneficiaries"
    ]
    ids = [int(r[0]) for r in conn.execute("SELECT id FROM nomina_banorte_beneficiaries ORDER BY id")]
    assert ids == before["ids"]
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_aliases").fetchone()[0]) == before["aliases"]
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_export_items").fetchone()[0]) == before["items"]
    assert (
        int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_export_draft_rows").fetchone()[0])
        == before["draft_rows"]
    )
    after_by_record = _sql_count_map(
        conn.execute(
            "SELECT record_status, COUNT(*) AS c FROM nomina_banorte_beneficiaries GROUP BY record_status"
        )
    )
    after_by_validation = _sql_count_map(
        conn.execute(
            "SELECT validation_status, COUNT(*) AS c FROM nomina_banorte_beneficiaries GROUP BY validation_status"
        )
    )
    assert after_by_record == before_by_record
    assert after_by_validation == before_by_validation
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    assert (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE name='nomina_banorte_beneficiaries__new'"
        ).fetchone()
        is None
    )
    conn.close()


def test_migration_succeeds_with_sqlite_row_factory(tmp_path):
    path = str(tmp_path / "row.db")
    before = _seed_old_schema_with_children(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate_beneficiaries_inactivo_manual(conn)
    assert _beneficiaries_sql_allows_inactivo_manual(conn)
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_beneficiaries").fetchone()[0]) == before[
        "beneficiaries"
    ]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_ensure_banorte_tables_plain_connection_migrates(tmp_path):
    """Closest path to init_db: plain sqlite3.connect + ensure_*."""
    path = str(tmp_path / "boot.db")
    before = _seed_old_schema_with_children(path)
    conn = sqlite3.connect(path)
    assert conn.row_factory is None
    # Isolate beneficiary migration from unrelated drafts CHECK rebuild (FK children).
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TABLE IF EXISTS nomina_banorte_export_draft_rows")
    conn.execute("DROP TABLE IF EXISTS nomina_banorte_export_drafts")
    conn.execute("PRAGMA foreign_keys = ON")
    ensure_banorte_tables(conn)
    conn.commit()
    assert _beneficiaries_sql_allows_inactivo_manual(conn)
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_beneficiaries").fetchone()[0]) == before[
        "beneficiaries"
    ]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    ensure_banorte_tables(conn)
    conn.commit()
    assert _beneficiaries_sql_allows_inactivo_manual(conn)
    conn.close()


def test_create_app_init_db_with_isolated_sqlite(tmp_path):
    """Startup smoke via subprocess: PROCLEAN_INSTANCE_DIR isolates from real volume."""
    isolated = tmp_path / "instance"
    isolated.mkdir()
    generated = tmp_path / "generated"
    generated.mkdir()
    repo = Path(__file__).resolve().parents[1]
    db_path = isolated / "proclean.db"
    script = textwrap.dedent(
        """
        import sqlite3
        from pathlib import Path

        import app as app_module
        from modules.nomina.banorte.schema import _beneficiaries_sql_allows_inactivo_manual

        db_path = Path(r"{db_path}")
        assert app_module.DB_PATH.resolve() == db_path.resolve()

        app_module.init_db()
        assert db_path.exists()

        conn = sqlite3.connect(str(db_path))
        assert conn.row_factory is None
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DROP TABLE IF EXISTS nomina_banorte_beneficiary_events")
        rows = conn.execute(
            "SELECT id, nombre_original, nombre_normalizado, employee_number_effective, account_number, "
            "source_kind, validation_status, record_status, imported_at, imported_by, created_at, updated_at "
            "FROM nomina_banorte_beneficiaries"
        ).fetchall()
        conn.execute("DROP TABLE nomina_banorte_beneficiaries")
        conn.executescript('''
            CREATE TABLE nomina_banorte_beneficiaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre_original TEXT NOT NULL,
                nombre_normalizado TEXT NOT NULL,
                curp TEXT,
                employee_number_requested TEXT,
                employee_number_effective TEXT NOT NULL,
                account_number TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                validation_status TEXT NOT NULL,
                record_status TEXT NOT NULL
                    CHECK (record_status IN (
                        'ACTIVO',
                        'INACTIVO_REEMPLAZADO',
                        'CONFLICTO_CRITICO'
                    )),
                banorte_employee_substituted INTEGER NOT NULL DEFAULT 0,
                banorte_comment TEXT,
                source_filename TEXT,
                source_sheet TEXT,
                source_row INTEGER,
                report_date TEXT,
                imported_at TEXT NOT NULL,
                imported_by TEXT NOT NULL,
                replaces_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                replace_reason TEXT,
                replaced_by TEXT,
                replaced_at TEXT,
                manual_effective_from_account INTEGER NOT NULL DEFAULT 0
            )
        ''')
        if not rows:
            conn.execute(
                "INSERT INTO nomina_banorte_beneficiaries ("
                "nombre_original, nombre_normalizado, employee_number_effective, account_number, "
                "source_kind, validation_status, record_status, imported_at, imported_by, created_at, updated_at"
                ") VALUES ('A','A','1','111','ALTA_MANUAL','IMPORTADO_EXITOSO','ACTIVO','t','u','t','t')"
            )
            bid = 1
        else:
            for r in rows:
                conn.execute(
                    "INSERT INTO nomina_banorte_beneficiaries ("
                    "id, nombre_original, nombre_normalizado, employee_number_effective, account_number, "
                    "source_kind, validation_status, record_status, imported_at, imported_by, created_at, updated_at"
                    ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    r,
                )
            bid = int(rows[0][0])
        conn.execute(
            "INSERT OR IGNORE INTO nomina_banorte_aliases ("
            "alias_original, alias_normalizado, beneficiary_id, is_active, created_by, created_at"
            ") VALUES ('X','X',?,1,'u','t')",
            (bid,),
        )
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
        assert not _beneficiaries_sql_allows_inactivo_manual(conn)
        conn.close()

        app_module.init_db()
        flask_app = app_module.create_app()
        assert flask_app is not None
        conn = sqlite3.connect(str(db_path))
        assert conn.row_factory is None
        assert _beneficiaries_sql_allows_inactivo_manual(conn)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        conn.close()
        print("STARTUP_SMOKE_OK")
        """
    ).format(db_path=str(db_path))
    env = os.environ.copy()
    env["PROCLEAN_INSTANCE_DIR"] = str(isolated)
    env["PROCLEAN_GENERATED_DIR"] = str(generated)
    env["SECRET_KEY"] = "test-secret-key-for-startup-smoke"
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    assert "STARTUP_SMOKE_OK" in proc.stdout
