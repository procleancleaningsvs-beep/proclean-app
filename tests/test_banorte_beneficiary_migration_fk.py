"""Hotfix: beneficiaries CHECK rebuild must survive FK enforcement (prod incident)."""

from __future__ import annotations

import sqlite3

import pytest

from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import (
    _beneficiaries_sql_allows_inactivo_manual,
    _migrate_beneficiaries_inactivo_manual,
    ensure_banorte_tables,
)
from modules.nomina.db import ensure_nomina_tables

OLD_BENEFICIARIES_DDL = """
CREATE TABLE nomina_banorte_beneficiaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre_original TEXT NOT NULL,
    nombre_normalizado TEXT NOT NULL,
    curp TEXT,
    employee_number_requested TEXT,
    employee_number_effective TEXT NOT NULL,
    account_number TEXT NOT NULL,
    source_kind TEXT NOT NULL
        CHECK (source_kind IN (
            'ALTAS_NOMINA_BANORTE',
            'REPORTE_DETALLADO',
            'ALTA_MANUAL'
        )),
    validation_status TEXT NOT NULL
        CHECK (validation_status IN (
            'IMPORTADO_EXITOSO',
            'MANUAL_PENDIENTE_VALIDACION'
        )),
    record_status TEXT NOT NULL
        CHECK (record_status IN (
            'ACTIVO',
            'INACTIVO_REEMPLAZADO',
            'CONFLICTO_CRITICO'
        )),
    banorte_employee_substituted INTEGER NOT NULL DEFAULT 0
        CHECK (banorte_employee_substituted IN (0, 1)),
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
"""


def _seed_old_schema_with_children(path: str) -> dict[str, int]:
    """Simulate prod: old beneficiaries CHECK + child FK rows, foreign_keys ON."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    conn.executescript(OLD_BENEFICIARIES_DDL)
    # Minimal child tables matching real FK targets
    conn.execute(
        """
        CREATE TABLE nomina_banorte_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alias_original TEXT NOT NULL,
            alias_normalizado TEXT NOT NULL,
            beneficiary_id INTEGER NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (beneficiary_id)
                REFERENCES nomina_banorte_beneficiaries(id) ON DELETE RESTRICT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE nomina_banorte_export_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            export_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            nombre_recibido TEXT NOT NULL,
            beneficiary_id INTEGER,
            employee_number_effective TEXT NOT NULL,
            account_number TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            match_kind TEXT NOT NULL,
            validation_status TEXT NOT NULL,
            record_status TEXT NOT NULL,
            is_manual_beneficiary INTEGER NOT NULL DEFAULT 0,
            warnings_json TEXT NOT NULL DEFAULT '[]',
            user_decision_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (beneficiary_id)
                REFERENCES nomina_banorte_beneficiaries(id) ON DELETE RESTRICT
        )
        """
    )
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
            status TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            consecutive_pref TEXT,
            layout_date_pref TEXT,
            source_filename TEXT,
            source_sha256 TEXT,
            source_sheet TEXT,
            source_file_size INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE nomina_banorte_export_draft_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            nombre_recibido TEXT NOT NULL,
            beneficiary_id INTEGER,
            amount_original_cents INTEGER NOT NULL DEFAULT 0,
            amount_final_cents INTEGER NOT NULL DEFAULT 100,
            included INTEGER NOT NULL DEFAULT 1,
            match_kind TEXT NOT NULL DEFAULT 'NONE',
            row_state TEXT NOT NULL DEFAULT 'OK',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            user_decision_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (draft_id) REFERENCES nomina_banorte_export_drafts(id),
            FOREIGN KEY (beneficiary_id)
                REFERENCES nomina_banorte_beneficiaries(id) ON DELETE RESTRICT
        )
        """
    )
    rows = [
        ("ACTIVO", "IMPORTADO_EXITOSO", "1", "111"),
        ("INACTIVO_REEMPLAZADO", "IMPORTADO_EXITOSO", "2", "222"),
        ("CONFLICTO_CRITICO", "MANUAL_PENDIENTE_VALIDACION", "3", "333"),
        ("ACTIVO", "MANUAL_PENDIENTE_VALIDACION", "4", "444"),
    ]
    ids = []
    for status, val, emp, acct in rows:
        cur = conn.execute(
            """
            INSERT INTO nomina_banorte_beneficiaries (
                nombre_original, nombre_normalizado, employee_number_effective, account_number,
                source_kind, validation_status, record_status,
                imported_at, imported_by, created_at, updated_at
            ) VALUES (?,?,?,?,'ALTA_MANUAL',?,?, 't','u','t','t')
            """,
            (f"N{emp}", f"N{emp}", emp, acct, val, status),
        )
        ids.append(int(cur.lastrowid))
    conn.execute(
        """
        INSERT INTO nomina_banorte_aliases (
            alias_original, alias_normalizado, beneficiary_id, created_by, created_at
        ) VALUES ('Alias','ALIAS',?, 'u','t')
        """,
        (ids[0],),
    )
    conn.execute(
        """
        INSERT INTO nomina_banorte_export_items (
            export_id, position, nombre_recibido, beneficiary_id, employee_number_effective,
            account_number, amount_cents, match_kind, validation_status, record_status
        ) VALUES (1,1,'X',?,'1','111',100,'EXACT','IMPORTADO_EXITOSO','ACTIVO')
        """,
        (ids[0],),
    )
    conn.execute(
        """
        INSERT INTO nomina_banorte_export_drafts (
            created_by, updated_by, created_at, updated_at, origin_kind, origin_hash, status
        ) VALUES ('u','u','t','t','MANUAL_CAPTURE','abc','OPEN')
        """
    )
    draft_id = int(conn.execute("SELECT id FROM nomina_banorte_export_drafts").fetchone()[0])
    conn.execute(
        """
        INSERT INTO nomina_banorte_export_draft_rows (
            draft_id, position, nombre_recibido, beneficiary_id
        ) VALUES (?,1,'Y',?)
        """,
        (draft_id, ids[1]),
    )
    conn.commit()
    assert not _beneficiaries_sql_allows_inactivo_manual(conn)
    counts = {
        "beneficiaries": int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_beneficiaries").fetchone()[0]),
        "aliases": int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_aliases").fetchone()[0]),
        "items": int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_export_items").fetchone()[0]),
        "draft_rows": int(
            conn.execute("SELECT COUNT(*) FROM nomina_banorte_export_draft_rows").fetchone()[0]
        ),
        "ids": ids,
        "by_record": {
            r["record_status"]: r["c"]
            for r in conn.execute(
                "SELECT record_status, COUNT(*) AS c FROM nomina_banorte_beneficiaries GROUP BY record_status"
            )
        },
        "by_validation": {
            r["validation_status"]: r["c"]
            for r in conn.execute(
                "SELECT validation_status, COUNT(*) AS c FROM nomina_banorte_beneficiaries GROUP BY validation_status"
            )
        },
    }
    conn.close()
    return counts


def test_migration_with_fk_on_and_child_rows_succeeds(tmp_path):
    path = str(tmp_path / "fk.db")
    before = _seed_old_schema_with_children(path)
    conn = connect(path)
    assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    _migrate_beneficiaries_inactivo_manual(conn)
    assert _beneficiaries_sql_allows_inactivo_manual(conn)
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_beneficiaries").fetchone()[0]) == before[
        "beneficiaries"
    ]
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_aliases").fetchone()[0]) == before["aliases"]
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_export_items").fetchone()[0]) == before[
        "items"
    ]
    assert int(
        conn.execute("SELECT COUNT(*) FROM nomina_banorte_export_draft_rows").fetchone()[0]
    ) == before["draft_rows"]
    ids = [int(r[0]) for r in conn.execute("SELECT id FROM nomina_banorte_beneficiaries ORDER BY id")]
    assert ids == before["ids"]
    by_rec = {
        r["record_status"]: r["c"]
        for r in conn.execute(
            "SELECT record_status, COUNT(*) AS c FROM nomina_banorte_beneficiaries GROUP BY record_status"
        )
    }
    assert by_rec == before["by_record"]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    # INACTIVO_MANUAL insertable
    conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, employee_number_effective, account_number,
            source_kind, validation_status, record_status,
            imported_at, imported_by, created_at, updated_at
        ) VALUES ('Z','Z','99','999','ALTA_MANUAL','MANUAL_PENDIENTE_VALIDACION','INACTIVO_MANUAL',
                  't','u','t','t')
        """
    )
    conn.commit()
    conn.close()


def test_migration_idempotent_second_run_noop(tmp_path):
    path = str(tmp_path / "idem.db")
    _seed_old_schema_with_children(path)
    conn = connect(path)
    _migrate_beneficiaries_inactivo_manual(conn)
    sql1 = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='nomina_banorte_beneficiaries'"
    ).fetchone()[0]
    count1 = int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_beneficiaries").fetchone()[0])
    _migrate_beneficiaries_inactivo_manual(conn)
    sql2 = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='nomina_banorte_beneficiaries'"
    ).fetchone()[0]
    count2 = int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_beneficiaries").fetchone()[0])
    assert sql1 == sql2
    assert count1 == count2
    temps = conn.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE 'nomina_banorte_beneficiaries%'"
    ).fetchall()
    names = {r[0] for r in temps}
    assert names == {"nomina_banorte_beneficiaries"}
    assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    conn.close()


def test_migration_noop_when_ddl_already_has_inactivo_manual(tmp_path):
    path = str(tmp_path / "fresh.db")
    conn = connect(path)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    assert _beneficiaries_sql_allows_inactivo_manual(conn)
    sql_before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='nomina_banorte_beneficiaries'"
    ).fetchone()[0]
    _migrate_beneficiaries_inactivo_manual(conn)
    sql_after = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='nomina_banorte_beneficiaries'"
    ).fetchone()[0]
    assert sql_before == sql_after
    conn.close()


def test_migration_aborts_if_foreign_keys_cannot_be_disabled(tmp_path, monkeypatch):
    path = str(tmp_path / "stuck.db")
    _seed_old_schema_with_children(path)
    conn = connect(path)
    import modules.nomina.banorte.schema as sch

    real = sch._pragma_foreign_keys
    calls = {"n": 0}

    def wrap(c):
        calls["n"] += 1
        # 1st: original_fk; 2nd: verify after OFF — force still ON
        if calls["n"] >= 2:
            return 1
        return real(c)

    monkeypatch.setattr(sch, "_pragma_foreign_keys", wrap)
    with pytest.raises(RuntimeError, match="foreign_keys_disable_failed"):
        _migrate_beneficiaries_inactivo_manual(conn)
    assert not _beneficiaries_sql_allows_inactivo_manual(conn)
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_beneficiaries").fetchone()[0]) == 4
    assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    conn.close()


def test_migration_rolls_back_on_copy_failure(tmp_path, monkeypatch):
    path = str(tmp_path / "failcopy.db")
    before = _seed_old_schema_with_children(path)
    conn = connect(path)
    import modules.nomina.banorte.schema as sch

    def boom(name: str) -> None:
        if name == "before_copy":
            raise sqlite3.OperationalError("injected_copy_failure")

    monkeypatch.setattr(sch, "_beneficiary_migration_failpoint", boom)
    with pytest.raises(sqlite3.OperationalError, match="injected_copy_failure"):
        _migrate_beneficiaries_inactivo_manual(conn)
    conn.close()
    conn = connect(path)
    assert not _beneficiaries_sql_allows_inactivo_manual(conn)
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_beneficiaries").fetchone()[0]) == before[
        "beneficiaries"
    ]
    leftover = conn.execute(
        "SELECT name FROM sqlite_master WHERE name='nomina_banorte_beneficiaries__new'"
    ).fetchone()
    assert leftover is None
    assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    conn.close()


def test_migration_rolls_back_before_drop(tmp_path, monkeypatch):
    path = str(tmp_path / "faildrop.db")
    before = _seed_old_schema_with_children(path)
    conn = connect(path)
    import modules.nomina.banorte.schema as sch

    def boom(name: str) -> None:
        if name == "before_drop":
            raise RuntimeError("injected_before_drop")

    monkeypatch.setattr(sch, "_beneficiary_migration_failpoint", boom)
    with pytest.raises(RuntimeError, match="injected_before_drop"):
        _migrate_beneficiaries_inactivo_manual(conn)
    conn.close()
    conn = connect(path)
    assert not _beneficiaries_sql_allows_inactivo_manual(conn)
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_beneficiaries").fetchone()[0]) == before[
        "beneficiaries"
    ]
    assert (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE name='nomina_banorte_beneficiaries__new'"
        ).fetchone()
        is None
    )
    assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    conn.close()


def test_stale_temp_table_is_cleared_safely(tmp_path):
    path = str(tmp_path / "stale.db")
    _seed_old_schema_with_children(path)
    conn = connect(path)
    conn.execute(
        "CREATE TABLE nomina_banorte_beneficiaries__new (id INTEGER PRIMARY KEY, junk TEXT)"
    )
    conn.commit()
    _migrate_beneficiaries_inactivo_manual(conn)
    assert _beneficiaries_sql_allows_inactivo_manual(conn)
    assert (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE name='nomina_banorte_beneficiaries__new'"
        ).fetchone()
        is None
    )
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_beneficiaries").fetchone()[0]) == 4
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
