"""Hotfix: draft_events ADD_ROW rebuild must survive self-referential UNDO rows."""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest

from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import (
    _DRAFT_EVENTS_COLS,
    _DRAFT_EVENTS_DDL,
    _draft_events_sql_allows_add_row,
    _migrate_draft_events_add_row,
    ensure_banorte_tables,
)
from modules.nomina.db import ensure_nomina_tables

OLD_DRAFT_EVENTS_DDL = """
CREATE TABLE nomina_banorte_draft_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL,
    row_id INTEGER NOT NULL,
    action TEXT NOT NULL
        CHECK (action IN (
            'APPLY_BENEFICIARY',
            'APPLY_AMOUNT',
            'EXCLUDE_ROW',
            'UNDO'
        )),
    reversible INTEGER NOT NULL CHECK (reversible IN (0, 1)),
    target_event_id INTEGER,
    before_nombre_recibido TEXT,
    after_nombre_recibido TEXT,
    before_beneficiary_id INTEGER,
    after_beneficiary_id INTEGER,
    before_amount_final_cents INTEGER,
    after_amount_final_cents INTEGER,
    before_included INTEGER,
    after_included INTEGER,
    before_row_state TEXT,
    after_row_state TEXT,
    before_excluded_at TEXT,
    after_excluded_at TEXT,
    before_excluded_by TEXT,
    after_excluded_by TEXT,
    revision_before INTEGER NOT NULL,
    revision_after INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (draft_id)
        REFERENCES nomina_banorte_export_drafts(id) ON DELETE RESTRICT,
    FOREIGN KEY (row_id)
        REFERENCES nomina_banorte_export_draft_rows(id) ON DELETE RESTRICT,
    FOREIGN KEY (target_event_id)
        REFERENCES nomina_banorte_draft_events(id) ON DELETE RESTRICT,
    CHECK (revision_after >= revision_before),
    CHECK (
        (action = 'UNDO' AND reversible = 0 AND target_event_id IS NOT NULL)
        OR
        (action IN ('APPLY_BENEFICIARY', 'APPLY_AMOUNT', 'EXCLUDE_ROW')
            AND reversible = 1 AND target_event_id IS NULL)
    )
)
"""


def _seed_old_draft_events_with_undo(path: str, *, row_factory=None) -> dict:
    conn = sqlite3.connect(path)
    if row_factory is not None:
        conn.row_factory = row_factory
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
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
            status TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            consecutive_pref TEXT,
            layout_date_pref TEXT
        );
        CREATE TABLE nomina_banorte_export_draft_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            nombre_recibido TEXT NOT NULL,
            amount_original_cents INTEGER NOT NULL DEFAULT 0,
            amount_final_cents INTEGER NOT NULL DEFAULT 100,
            included INTEGER NOT NULL DEFAULT 1,
            match_kind TEXT NOT NULL DEFAULT 'NONE',
            row_state TEXT NOT NULL DEFAULT 'OK',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            user_decision_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (draft_id) REFERENCES nomina_banorte_export_drafts(id)
        );
        """
    )
    conn.execute(
        """
        INSERT INTO nomina_banorte_export_drafts (
            created_by, updated_by, created_at, updated_at, origin_kind, origin_hash, status, revision
        ) VALUES ('u','u','t','t','MANUAL_CAPTURE','hash1','OPEN',3)
        """
    )
    draft1 = int(conn.execute("SELECT id FROM nomina_banorte_export_drafts").fetchone()[0])
    conn.execute(
        """
        INSERT INTO nomina_banorte_export_draft_rows (
            draft_id, position, nombre_recibido, amount_original_cents, amount_final_cents
        ) VALUES (?,1,'A',1000,1000)
        """,
        (draft1,),
    )
    row1 = int(conn.execute("SELECT id FROM nomina_banorte_export_draft_rows").fetchone()[0])
    conn.execute(
        """
        INSERT INTO nomina_banorte_export_drafts (
            created_by, updated_by, created_at, updated_at, origin_kind, origin_hash, status, revision
        ) VALUES ('u','u','t','t','MANUAL_CAPTURE','hash2','OPEN',5)
        """
    )
    draft2 = int(conn.execute("SELECT id FROM nomina_banorte_export_drafts ORDER BY id DESC").fetchone()[0])
    conn.execute(
        """
        INSERT INTO nomina_banorte_export_draft_rows (
            draft_id, position, nombre_recibido, amount_original_cents, amount_final_cents
        ) VALUES (?,1,'B',2000,2000)
        """,
        (draft2,),
    )
    row2 = int(
        conn.execute(
            "SELECT id FROM nomina_banorte_export_draft_rows WHERE draft_id=?",
            (draft2,),
        ).fetchone()[0]
    )
    conn.execute(OLD_DRAFT_EVENTS_DDL)
    event_ids: list[int] = []
    for draft_id, row_id, rev in ((draft1, row1, 1), (draft2, row2, 2)):
        cur = conn.execute(
            """
            INSERT INTO nomina_banorte_draft_events (
                draft_id, row_id, action, reversible, target_event_id,
                revision_before, revision_after, created_by, created_at
            ) VALUES (?, ?, 'APPLY_AMOUNT', 1, NULL, ?, ?, 'u', 't1')
            """,
            (draft_id, row_id, rev - 1, rev),
        )
        apply_id = int(cur.lastrowid)
        event_ids.append(apply_id)
        cur = conn.execute(
            """
            INSERT INTO nomina_banorte_draft_events (
                draft_id, row_id, action, reversible, target_event_id,
                revision_before, revision_after, created_by, created_at
            ) VALUES (?, ?, 'EXCLUDE_ROW', 1, NULL, ?, ?, 'u', 't2')
            """,
            (draft_id, row_id, rev, rev + 1),
        )
        exclude_id = int(cur.lastrowid)
        event_ids.append(exclude_id)
        cur = conn.execute(
            """
            INSERT INTO nomina_banorte_draft_events (
                draft_id, row_id, action, reversible, target_event_id,
                revision_before, revision_after, created_by, created_at
            ) VALUES (?, ?, 'UNDO', 0, ?, ?, ?, 'u', 't3')
            """,
            (draft_id, row_id, exclude_id, rev + 1, rev + 2),
        )
        event_ids.append(int(cur.lastrowid))
    conn.commit()
    assert not _draft_events_sql_allows_add_row(conn)
    snapshot = {
        "count": int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_draft_events").fetchone()[0]),
        "ids": [
            int(r[0])
            for r in conn.execute("SELECT id FROM nomina_banorte_draft_events ORDER BY id")
        ],
        "targets": [
            (int(r[0]), int(r[1]) if r[1] is not None else None)
            for r in conn.execute(
                "SELECT id, target_event_id FROM nomina_banorte_draft_events ORDER BY id"
            )
        ],
        "event_ids": event_ids,
    }
    conn.close()
    return snapshot


def _simulate_broken_rename_first_migration(conn: sqlite3.Connection) -> None:
    """Production defect: rename-first rebuild with FK ON inside a transaction."""
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("ALTER TABLE nomina_banorte_draft_events RENAME TO nomina_banorte_draft_events__old")
    conn.execute(_DRAFT_EVENTS_DDL)
    conn.execute(
        f"""
        INSERT INTO nomina_banorte_draft_events ({_DRAFT_EVENTS_COLS})
        SELECT {_DRAFT_EVENTS_COLS} FROM nomina_banorte_draft_events__old
        """
    )
    conn.execute("DROP TABLE nomina_banorte_draft_events__old")


def test_rename_first_rebuild_fails_with_self_ref_undo(tmp_path):
    path = str(tmp_path / "red.db")
    _seed_old_draft_events_with_undo(path, row_factory=None)
    conn = connect(path)
    conn.row_factory = None
    assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        _simulate_broken_rename_first_migration(conn)
    conn.rollback()
    assert not _draft_events_sql_allows_add_row(conn)
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_draft_events").fetchone()[0]) == 6
    conn.close()


def test_migration_with_self_ref_undo_succeeds_tuple_factory(tmp_path):
    path = str(tmp_path / "ok.db")
    before = _seed_old_draft_events_with_undo(path, row_factory=None)
    conn = connect(path)
    conn.row_factory = None
    assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    _migrate_draft_events_add_row(conn)
    assert _draft_events_sql_allows_add_row(conn)
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_draft_events").fetchone()[0]) == before["count"]
    ids = [int(r[0]) for r in conn.execute("SELECT id FROM nomina_banorte_draft_events ORDER BY id")]
    assert ids == before["ids"]
    targets = [
        (int(r[0]), int(r[1]) if r[1] is not None else None)
        for r in conn.execute(
            "SELECT id, target_event_id FROM nomina_banorte_draft_events ORDER BY id"
        )
    ]
    assert targets == before["targets"]
    fk_rows = conn.execute("PRAGMA foreign_key_list(nomina_banorte_draft_events)").fetchall()
    target_fk = [row for row in fk_rows if str(row[3]) == "target_event_id"]
    assert target_fk and str(target_fk[0][2]) == "nomina_banorte_draft_events"
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='nomina_banorte_draft_events'"
    ).fetchone()[0]
    assert "ADD_ROW" in sql
    assert "__old" not in sql
    assert "__new" not in sql
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    temps = {
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'nomina_banorte_draft_events%'"
        )
    }
    assert temps == {"nomina_banorte_draft_events"}
    conn.close()


def test_migration_with_self_ref_undo_succeeds_row_factory(tmp_path):
    path = str(tmp_path / "row.db")
    before = _seed_old_draft_events_with_undo(path, row_factory=sqlite3.Row)
    conn = connect(path)
    _migrate_draft_events_add_row(conn)
    assert _draft_events_sql_allows_add_row(conn)
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_draft_events").fetchone()[0]) == before["count"]
    conn.close()


def test_migration_idempotent_second_run_noop(tmp_path):
    path = str(tmp_path / "idem.db")
    _seed_old_draft_events_with_undo(path)
    conn = connect(path)
    _migrate_draft_events_add_row(conn)
    sql1 = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='nomina_banorte_draft_events'"
    ).fetchone()[0]
    count1 = int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_draft_events").fetchone()[0])
    _migrate_draft_events_add_row(conn)
    sql2 = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='nomina_banorte_draft_events'"
    ).fetchone()[0]
    count2 = int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_draft_events").fetchone()[0])
    assert sql1 == sql2
    assert count1 == count2
    conn.close()


def test_migration_noop_when_ddl_already_has_add_row(tmp_path):
    path = str(tmp_path / "fresh.db")
    conn = connect(path)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    assert _draft_events_sql_allows_add_row(conn)
    sql_before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='nomina_banorte_draft_events'"
    ).fetchone()[0]
    _migrate_draft_events_add_row(conn)
    sql_after = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='nomina_banorte_draft_events'"
    ).fetchone()[0]
    assert sql_before == sql_after
    conn.close()


def test_migration_aborts_if_foreign_keys_cannot_be_disabled(tmp_path, monkeypatch):
    path = str(tmp_path / "stuck.db")
    _seed_old_draft_events_with_undo(path)
    conn = connect(path)
    import modules.nomina.banorte.schema as sch

    real = sch._pragma_foreign_keys
    calls = {"n": 0}

    def wrap(c):
        calls["n"] += 1
        if calls["n"] >= 2:
            return 1
        return real(c)

    monkeypatch.setattr(sch, "_pragma_foreign_keys", wrap)
    with pytest.raises(RuntimeError, match="foreign_keys_disable_failed"):
        _migrate_draft_events_add_row(conn)
    assert not _draft_events_sql_allows_add_row(conn)
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_draft_events").fetchone()[0]) == 6
    conn.close()


def test_migration_rolls_back_on_copy_failure(tmp_path, monkeypatch):
    path = str(tmp_path / "failcopy.db")
    before = _seed_old_draft_events_with_undo(path)
    conn = connect(path)
    import modules.nomina.banorte.schema as sch

    def boom(name: str) -> None:
        if name == "before_copy":
            raise sqlite3.OperationalError("injected_copy_failure")

    monkeypatch.setattr(sch, "_draft_events_migration_failpoint", boom)
    with pytest.raises(sqlite3.OperationalError, match="injected_copy_failure"):
        _migrate_draft_events_add_row(conn)
    conn.close()
    conn = connect(path)
    assert not _draft_events_sql_allows_add_row(conn)
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_draft_events").fetchone()[0]) == before["count"]
    assert (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE name='nomina_banorte_draft_events__new'"
        ).fetchone()
        is None
    )
    conn.close()


def test_migration_rolls_back_before_drop(tmp_path, monkeypatch):
    path = str(tmp_path / "faildrop.db")
    before = _seed_old_draft_events_with_undo(path)
    conn = connect(path)
    import modules.nomina.banorte.schema as sch

    def boom(name: str) -> None:
        if name == "before_drop":
            raise RuntimeError("injected_before_drop")

    monkeypatch.setattr(sch, "_draft_events_migration_failpoint", boom)
    with pytest.raises(RuntimeError, match="injected_before_drop"):
        _migrate_draft_events_add_row(conn)
    conn.close()
    conn = connect(path)
    assert not _draft_events_sql_allows_add_row(conn)
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_draft_events").fetchone()[0]) == before["count"]
    assert (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE name='nomina_banorte_draft_events__new'"
        ).fetchone()
        is None
    )
    conn.close()


def test_stale_temp_table_is_cleared_safely(tmp_path):
    path = str(tmp_path / "stale.db")
    _seed_old_draft_events_with_undo(path)
    conn = connect(path)
    conn.execute("CREATE TABLE nomina_banorte_draft_events__new (id INTEGER PRIMARY KEY, junk TEXT)")
    conn.commit()
    _migrate_draft_events_add_row(conn)
    assert _draft_events_sql_allows_add_row(conn)
    assert (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE name='nomina_banorte_draft_events__new'"
        ).fetchone()
        is None
    )
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_draft_events").fetchone()[0]) == 6
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_orphan_old_table_aborts_without_destructive_drop(tmp_path):
    path = str(tmp_path / "orphan.db")
    _seed_old_draft_events_with_undo(path)
    conn = connect(path)
    conn.execute("ALTER TABLE nomina_banorte_draft_events RENAME TO nomina_banorte_draft_events__old")
    conn.commit()
    with pytest.raises(RuntimeError, match="draft_events_migration_orphan_old"):
        _migrate_draft_events_add_row(conn)
    assert (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE name='nomina_banorte_draft_events__old'"
        ).fetchone()
        is not None
    )
    conn.close()


def test_undo_unique_index_preserved(tmp_path):
    path = str(tmp_path / "uniq.db")
    _seed_old_draft_events_with_undo(path)
    conn = connect(path)
    _migrate_draft_events_add_row(conn)
    idx = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='uq_banorte_draft_events_undo_target'"
    ).fetchone()[0]
    assert "UNDO" in idx
    assert "target_event_id" in idx
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO nomina_banorte_draft_events (
                draft_id, row_id, action, reversible, target_event_id,
                revision_before, revision_after, created_by, created_at
            ) VALUES (1, 1, 'UNDO', 0, 2, 9, 10, 'u', 't')
            """
        )
    conn.close()


def test_ensure_banorte_tables_plain_connection_migrates_old_draft_events(tmp_path):
    path = str(tmp_path / "boot.db")
    before = _seed_old_draft_events_with_undo(path, row_factory=None)
    conn = sqlite3.connect(path)
    assert conn.row_factory is None
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate_draft_events_add_row(conn)
    assert _draft_events_sql_allows_add_row(conn)
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_draft_events").fetchone()[0]) == before["count"]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    _migrate_draft_events_add_row(conn)
    conn.close()


def test_create_app_init_db_with_old_draft_events_schema(tmp_path):
    isolated = tmp_path / "instance"
    isolated.mkdir()
    generated = tmp_path / "generated"
    generated.mkdir()
    repo = Path(__file__).resolve().parents[1]
    db_path = isolated / "proclean.db"
    old_ddl = OLD_DRAFT_EVENTS_DDL.strip()
    script = textwrap.dedent(
        f"""
        import sqlite3
        from pathlib import Path

        import app as app_module
        from modules.nomina.banorte.schema import _draft_events_sql_allows_add_row

        db_path = Path(r"{db_path}")
        assert app_module.DB_PATH.resolve() == db_path.resolve()

        app_module.init_db()
        assert db_path.exists()

        conn = sqlite3.connect(str(db_path))
        assert conn.row_factory is None
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DROP TABLE IF EXISTS nomina_banorte_draft_events")
        conn.executescript({old_ddl!r})
        conn.execute(
            "INSERT INTO nomina_banorte_export_drafts ("
            "created_by, updated_by, created_at, updated_at, origin_kind, origin_hash, status, revision"
            ") VALUES ('u','u','t','t','MANUAL_CAPTURE','hash-startup','OPEN',2)"
        )
        draft_id = int(conn.execute("SELECT id FROM nomina_banorte_export_drafts ORDER BY id DESC").fetchone()[0])
        conn.execute(
            "INSERT INTO nomina_banorte_export_draft_rows ("
            "draft_id, position, nombre_recibido, amount_original_cents, amount_final_cents, "
            "included, match_kind, row_state, warnings_json, user_decision_json"
            ") VALUES (?,1,'Startup',1000,1000,1,'NONE','OK','[]','{{}}')",
            (draft_id,),
        )
        row_id = int(conn.execute("SELECT id FROM nomina_banorte_export_draft_rows ORDER BY id DESC").fetchone()[0])
        cur = conn.execute(
            "INSERT INTO nomina_banorte_draft_events ("
            "draft_id, row_id, action, reversible, target_event_id, "
            "revision_before, revision_after, created_by, created_at"
            ") VALUES (?, ?, 'APPLY_AMOUNT', 1, NULL, 0, 1, 'u', 't1')",
            (draft_id, row_id),
        )
        apply_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO nomina_banorte_draft_events ("
            "draft_id, row_id, action, reversible, target_event_id, "
            "revision_before, revision_after, created_by, created_at"
            ") VALUES (?, ?, 'UNDO', 0, ?, 1, 2, 'u', 't2')",
            (draft_id, row_id, apply_id),
        )
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
        assert not _draft_events_sql_allows_add_row(conn)
        conn.close()

        app_module.init_db()
        flask_app = app_module.create_app()
        assert flask_app is not None
        conn = sqlite3.connect(str(db_path))
        assert conn.row_factory is None
        assert _draft_events_sql_allows_add_row(conn)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        conn.close()
        print("STARTUP_SMOKE_OK")
        """
    )
    import os
    import subprocess
    import sys

    env = os.environ.copy()
    env["PROCLEAN_INSTANCE_DIR"] = str(isolated)
    env["PROCLEAN_GENERATED_DIR"] = str(generated)
    env["SECRET_KEY"] = "test-secret-key-for-draft-events-startup"
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "STARTUP_SMOKE_OK" in proc.stdout
