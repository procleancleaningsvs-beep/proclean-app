from __future__ import annotations

import sqlite3

import pytest

from modules.nomina.banorte import repository as banorte_repo
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.db import ensure_nomina_tables


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _insert_beneficiary(
    conn: sqlite3.Connection,
    *,
    emp: str,
    account: str,
    record_status: str = "ACTIVO",
    validation_status: str = "IMPORTADO_EXITOSO",
    nombre: str = "PERSONA DEMO",
    replaces_id: int | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, employee_number_effective,
            account_number, source_kind, validation_status, record_status,
            imported_at, imported_by, created_at, updated_at, replaces_id
        ) VALUES (?, ?, ?, ?, 'ALTA_MANUAL', ?, ?, '2026-01-01T00:00:00', 'tester',
                  '2026-01-01T00:00:00', '2026-01-01T00:00:00', ?)
        """,
        (nombre, nombre, emp, account, validation_status, record_status, replaces_id),
    )
    return int(cur.lastrowid)


@pytest.fixture
def banorte_db(tmp_path):
    path = tmp_path / "banorte.db"
    conn = banorte_repo.connect(path)
    ensure_nomina_tables(conn)
    conn.commit()
    yield conn, path
    conn.close()


def test_repository_enables_foreign_keys(banorte_db):
    conn, _ = banorte_db
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_beneficiary_columns_match_spec(banorte_db):
    conn, _ = banorte_db
    cols = _cols(conn, "nomina_banorte_beneficiaries")
    assert "is_active" not in cols
    assert "replaced_by_id" not in cols
    assert "record_status" in cols
    assert "validation_status" in cols
    assert "replaces_id" in cols
    batch_cols = _cols(conn, "nomina_banorte_import_batches")
    assert "original_file_blob" not in batch_cols
    export_cols = _cols(conn, "nomina_banorte_exports")
    assert "total_decimal" not in export_cols
    assert "beneficiary_snapshot_version" not in export_cols
    assert "beneficiary_snapshot_sha256" not in export_cols
    assert "total_cents" in export_cols
    assert "duplicate_of_export_id" in export_cols
    item_cols = _cols(conn, "nomina_banorte_export_items")
    assert "amount_decimal" not in item_cols
    assert "amount_cents" in item_cols
    assert "record_status" in item_cols


def test_invalid_validation_status_rejected(banorte_db):
    conn, _ = banorte_db
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO nomina_banorte_beneficiaries (
                nombre_original, nombre_normalizado, employee_number_effective,
                account_number, source_kind, validation_status, record_status,
                imported_at, imported_by, created_at, updated_at
            ) VALUES ('A','A','0001','1001','ALTA_MANUAL','INACTIVO_REEMPLAZADO','ACTIVO',
                      't','u','t','t')
            """
        )


def test_invalid_record_status_rejected(banorte_db):
    conn, _ = banorte_db
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO nomina_banorte_beneficiaries (
                nombre_original, nombre_normalizado, employee_number_effective,
                account_number, source_kind, validation_status, record_status,
                imported_at, imported_by, created_at, updated_at
            ) VALUES ('A','A','0001','1001','ALTA_MANUAL','IMPORTADO_EXITOSO','IMPORTADO_EXITOSO',
                      't','u','t','t')
            """
        )


def test_duplicate_active_account_rejected(banorte_db):
    conn, _ = banorte_db
    _insert_beneficiary(conn, emp="0000000001", account="1320000001")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_beneficiary(conn, emp="0000000002", account="1320000001", nombre="OTRA")


def test_duplicate_active_employee_number_rejected(banorte_db):
    conn, _ = banorte_db
    _insert_beneficiary(conn, emp="0000000042", account="1320000042")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_beneficiary(conn, emp="0000000042", account="1320000099", nombre="OTRA")


def test_conflicto_critico_may_share_account(banorte_db):
    conn, _ = banorte_db
    _insert_beneficiary(
        conn, emp="0000000101", account="1320000101", record_status="CONFLICTO_CRITICO"
    )
    _insert_beneficiary(
        conn,
        emp="0000000102",
        account="1320000101",
        record_status="CONFLICTO_CRITICO",
        nombre="OTRA CONFLICTO",
    )
    n = conn.execute(
        "SELECT COUNT(*) FROM nomina_banorte_beneficiaries WHERE account_number=?",
        ("1320000101",),
    ).fetchone()[0]
    assert n == 2


def test_inactive_may_share_account_with_activo(banorte_db):
    conn, _ = banorte_db
    old_id = _insert_beneficiary(
        conn, emp="0000000201", account="1320000201", record_status="INACTIVO_REEMPLAZADO"
    )
    _insert_beneficiary(
        conn,
        emp="0000000202",
        account="1320000201",
        record_status="ACTIVO",
        replaces_id=old_id,
        nombre="NUEVA VERSION",
    )


def test_two_children_same_replaces_id_rejected(banorte_db):
    conn, _ = banorte_db
    old_id = _insert_beneficiary(
        conn, emp="0000000301", account="1320000301", record_status="INACTIVO_REEMPLAZADO"
    )
    _insert_beneficiary(
        conn,
        emp="0000000302",
        account="1320000302",
        replaces_id=old_id,
        nombre="HIJO A",
    )
    with pytest.raises(sqlite3.IntegrityError):
        _insert_beneficiary(
            conn,
            emp="0000000303",
            account="1320000303",
            replaces_id=old_id,
            nombre="HIJO B",
        )


def test_delete_beneficiary_referenced_by_alias_restricted(banorte_db):
    conn, _ = banorte_db
    ben_id = _insert_beneficiary(conn, emp="0000000401", account="1320000401")
    conn.execute(
        """
        INSERT INTO nomina_banorte_aliases (
            alias_original, alias_normalizado, beneficiary_id, is_active,
            created_by, created_at
        ) VALUES ('Pepe', 'PEPE', ?, 1, 'tester', '2026-01-01T00:00:00')
        """,
        (ben_id,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM nomina_banorte_beneficiaries WHERE id=?", (ben_id,))


def test_duplicate_active_alias_normalized_rejected(banorte_db):
    conn, _ = banorte_db
    a = _insert_beneficiary(conn, emp="0000000501", account="1320000501")
    b = _insert_beneficiary(conn, emp="0000000502", account="1320000502", nombre="OTRA")
    conn.execute(
        """
        INSERT INTO nomina_banorte_aliases (
            alias_original, alias_normalizado, beneficiary_id, is_active,
            created_by, created_at
        ) VALUES ('Pepe', 'PEPE', ?, 1, 'tester', '2026-01-01T00:00:00')
        """,
        (a,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO nomina_banorte_aliases (
                alias_original, alias_normalizado, beneficiary_id, is_active,
                created_by, created_at
            ) VALUES ('Pepe', 'PEPE', ?, 1, 'tester', '2026-01-01T00:00:00')
            """,
            (b,),
        )


def test_alias_reassign_after_explicit_deactivate(banorte_db):
    conn, _ = banorte_db
    a = _insert_beneficiary(conn, emp="0000000601", account="1320000601")
    b = _insert_beneficiary(conn, emp="0000000602", account="1320000602", nombre="OTRA")
    conn.execute(
        """
        INSERT INTO nomina_banorte_aliases (
            alias_original, alias_normalizado, beneficiary_id, is_active,
            created_by, created_at
        ) VALUES ('Pepe', 'PEPE', ?, 1, 'tester', '2026-01-01T00:00:00')
        """,
        (a,),
    )
    conn.execute(
        """
        UPDATE nomina_banorte_aliases
        SET is_active=0, deactivated_by='tester', deactivated_at='2026-01-02T00:00:00'
        WHERE alias_normalizado='PEPE' AND is_active=1
        """
    )
    conn.execute(
        """
        INSERT INTO nomina_banorte_aliases (
            alias_original, alias_normalizado, beneficiary_id, is_active,
            created_by, created_at
        ) VALUES ('Pepe', 'PEPE', ?, 1, 'tester', '2026-01-02T00:00:00')
        """,
        (b,),
    )
    active = conn.execute(
        "SELECT beneficiary_id FROM nomina_banorte_aliases WHERE alias_normalizado='PEPE' AND is_active=1"
    ).fetchone()[0]
    assert active == b


def test_ensure_banorte_tables_direct(tmp_path):
    path = tmp_path / "direct.db"
    conn = sqlite3.connect(path)
    ensure_banorte_tables(conn)
    conn.commit()
    assert (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='nomina_banorte_beneficiaries'"
        ).fetchone()
        is not None
    )
    conn.close()
