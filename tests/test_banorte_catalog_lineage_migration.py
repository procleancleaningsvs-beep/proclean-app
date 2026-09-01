from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from modules.nomina.banorte import schema as schema_module
from modules.nomina.banorte.schema import ensure_banorte_tables


RECONCILIATION_TABLE = "nomina_banorte_catalog_reconciliations"
LINEAGE_COLUMNS = {
    "lineage_status",
    "lineage_predecessor_person_id",
    "lineage_predecessor_beneficiary_id",
    "lineage_evidence_json",
    "lineage_evidence_sha256",
}
OLD_STATUSES = {
    "UNMATCHED",
    "AUTO_MATCHED",
    "MANUAL_MATCHED",
    "MULTIPLE_CANDIDATES",
    "ACCOUNT_MISMATCH",
    "EMPLOYEE_MISMATCH",
    "IDENTITY_CONFLICT",
    "LEGACY_NOT_USABLE",
    "STALE_RECONCILIATION",
}
OLD_METHODS = {
    "NONE",
    "EXACT_EMPLOYEE_ACCOUNT_RAW_NAME",
    "EXACT_EMPLOYEE_ACCOUNT_CANONICAL_NAME",
    "EXACT_EMPLOYEE_ACCOUNT_CONTROLLED_MA",
    "MANUAL_SELECTION",
}
NEW_METHODS = {
    "PREVIOUS_ACTIVE_RFC_BIRTH_RAW_NAME",
    "PREVIOUS_ACTIVE_RFC_BIRTH_CANONICAL_NAME",
    "PREVIOUS_ACTIVE_RFC_BIRTH_CONTROLLED_MA",
    "MANUAL_CONTINUITY_CONFIRMED",
}


OLD_RECONCILIATION_DDL = """
CREATE TABLE nomina_banorte_catalog_reconciliations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id INTEGER NOT NULL,
    person_id INTEGER NOT NULL,
    beneficiary_id INTEGER,
    reconciliation_status TEXT NOT NULL CHECK (reconciliation_status IN (
        'UNMATCHED','AUTO_MATCHED','MANUAL_MATCHED','MULTIPLE_CANDIDATES','ACCOUNT_MISMATCH',
        'EMPLOYEE_MISMATCH','IDENTITY_CONFLICT','LEGACY_NOT_USABLE','STALE_RECONCILIATION')),
    match_method TEXT NOT NULL CHECK (match_method IN (
        'NONE','EXACT_EMPLOYEE_ACCOUNT_RAW_NAME','EXACT_EMPLOYEE_ACCOUNT_CANONICAL_NAME',
        'EXACT_EMPLOYEE_ACCOUNT_CONTROLLED_MA','MANUAL_SELECTION')),
    candidate_count INTEGER NOT NULL DEFAULT 0 CHECK (candidate_count >= 0),
    reason_code TEXT,
    beneficiary_material_fingerprint_version TEXT,
    beneficiary_material_state_json TEXT,
    beneficiary_material_fingerprint TEXT,
    beneficiary_updated_at_seen TEXT,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0,1)),
    supersedes_reconciliation_id INTEGER,
    manual_reason TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    superseded_by TEXT,
    superseded_at TEXT,
    FOREIGN KEY (version_id) REFERENCES nomina_banorte_catalog_versions(id) ON DELETE RESTRICT,
    FOREIGN KEY (person_id) REFERENCES nomina_banorte_catalog_persons(id) ON DELETE RESTRICT,
    FOREIGN KEY (beneficiary_id) REFERENCES nomina_banorte_beneficiaries(id) ON DELETE RESTRICT,
    FOREIGN KEY (supersedes_reconciliation_id)
        REFERENCES nomina_banorte_catalog_reconciliations(id) ON DELETE RESTRICT,
    CHECK (reconciliation_status NOT IN ('AUTO_MATCHED','MANUAL_MATCHED') OR
        (beneficiary_id IS NOT NULL AND beneficiary_material_fingerprint_version IS NOT NULL AND
         beneficiary_material_state_json IS NOT NULL AND beneficiary_material_fingerprint IS NOT NULL)),
    CHECK (match_method<>'MANUAL_SELECTION' OR length(trim(COALESCE(manual_reason,''))) > 0)
)
"""


OLD_COLUMNS = (
    "id",
    "version_id",
    "person_id",
    "beneficiary_id",
    "reconciliation_status",
    "match_method",
    "candidate_count",
    "reason_code",
    "beneficiary_material_fingerprint_version",
    "beneficiary_material_state_json",
    "beneficiary_material_fingerprint",
    "beneficiary_updated_at_seen",
    "is_current",
    "supersedes_reconciliation_id",
    "manual_reason",
    "created_by",
    "created_at",
    "superseded_by",
    "superseded_at",
)


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_sql(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    assert row is not None and row[0]
    return str(row[0])


def _index_sql(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {
        str(row[0]): str(row[1])
        for row in conn.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
            (table,),
        )
    }


def _insert_version(conn: sqlite3.Connection, version_id: int, status: str = "STAGED") -> None:
    conn.execute(
        """
        INSERT INTO nomina_banorte_catalog_versions (
            id,status,source_filename,file_sha256,file_size_bytes,encoding,delimiter,
            report_date,issuer_original,issuer_normalized,source_line_count,
            data_row_count,created_by,created_at
        ) VALUES (?,?,?,?,1,'UTF-8','|','2026-01-01','07200','07200',4,0,'fixture','t')
        """,
        (version_id, status, f"v{version_id}.txt", f"{version_id % 16:x}" * 64),
    )


def _insert_person(conn: sqlite3.Connection, person_id: int, version_id: int) -> None:
    conn.execute(
        """
        INSERT INTO nomina_banorte_catalog_persons (
            id,version_id,issuer_normalized,rfc_normalized,birth_date_iso,
            name_normalized,name_controlled_key,person_status,
            observation_codes_json,created_at
        ) VALUES (?,?,'07200',?,'1990-01-01',?,?,'NO_ELIGIBLE_ROW','[]','t')
        """,
        (person_id, version_id, f"RFC{person_id}", f"P{person_id}", f"P{person_id}"),
    )


def _insert_beneficiary(conn: sqlite3.Connection, beneficiary_id: int) -> None:
    conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            id,nombre_original,nombre_normalizado,employee_number_effective,account_number,
            source_kind,validation_status,record_status,imported_at,imported_by,
            created_at,updated_at
        ) VALUES (?,?,?, ?,?,'ALTA_MANUAL','IMPORTADO_EXITOSO','ACTIVO','t','fixture','t','t')
        """,
        (
            beneficiary_id,
            f"B{beneficiary_id}",
            f"B{beneficiary_id}",
            f"{beneficiary_id:010d}",
            f"{beneficiary_id:010d}",
        ),
    )


def _downgrade_reconciliations_to_old_schema(conn: sqlite3.Connection) -> None:
    ensure_banorte_tables(conn)
    conn.commit()
    assert conn.execute(f"SELECT COUNT(*) FROM {RECONCILIATION_TABLE}").fetchone()[0] == 0
    conn.execute(f"DROP TABLE {RECONCILIATION_TABLE}")
    conn.execute(OLD_RECONCILIATION_DDL)
    conn.execute(
        "CREATE UNIQUE INDEX uq_banorte_catalog_reconciliation_current "
        f"ON {RECONCILIATION_TABLE}(person_id) WHERE is_current=1"
    )
    conn.execute(
        "CREATE INDEX idx_banorte_catalog_reconciliations_version_status "
        f"ON {RECONCILIATION_TABLE}(version_id,reconciliation_status)"
    )
    conn.commit()
    assert LINEAGE_COLUMNS.isdisjoint(_table_columns(conn, RECONCILIATION_TABLE))


def _insert_old_reconciliation(
    conn: sqlite3.Connection,
    *,
    reconciliation_id: int,
    version_id: int,
    person_id: int,
    beneficiary_id: int | None,
    status: str,
    method: str,
    is_current: int = 1,
    supersedes_id: int | None = None,
) -> None:
    matched = status in {"AUTO_MATCHED", "MANUAL_MATCHED"}
    conn.execute(
        f"""
        INSERT INTO {RECONCILIATION_TABLE} (
            id,version_id,person_id,beneficiary_id,reconciliation_status,match_method,
            candidate_count,reason_code,beneficiary_material_fingerprint_version,
            beneficiary_material_state_json,beneficiary_material_fingerprint,
            beneficiary_updated_at_seen,is_current,supersedes_reconciliation_id,
            manual_reason,created_by,created_at,superseded_by,superseded_at
        ) VALUES (?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            reconciliation_id,
            version_id,
            person_id,
            beneficiary_id,
            status,
            method,
            f"REASON_{reconciliation_id}",
            "v1" if matched else None,
            "{}" if matched else None,
            "a" * 64 if matched else None,
            "t" if matched else None,
            is_current,
            supersedes_id,
            "fixture reason" if method == "MANUAL_SELECTION" else None,
            "fixture",
            "t",
            "fixture" if not is_current else None,
            "t" if not is_current else None,
        ),
    )


def _seed_referenced_children(conn: sqlite3.Connection, reconciliation_id: int, person_id: int) -> None:
    conn.execute(
        """
        INSERT INTO nomina_banorte_catalog_events (
            id,version_id,person_id,reconciliation_id,event_type,metadata_json,actor,created_at
        ) VALUES (8101,101,?,?,'RECONCILIATION_CREATED','{}','fixture','t')
        """,
        (person_id, reconciliation_id),
    )
    conn.execute(
        """
        INSERT INTO nomina_banorte_export_drafts (
            id,created_by,updated_by,created_at,updated_at,origin_kind,
            origin_hash,status,revision
        ) VALUES (8201,'fixture','fixture','t','t','MANUAL_CAPTURE','hash','OPEN',1)
        """
    )
    conn.execute(
        """
        INSERT INTO nomina_banorte_export_draft_rows (
            id,draft_id,position,nombre_recibido,amount_original_cents,
            amount_final_cents,included,match_kind,row_state,warnings_json,
            user_decision_json,catalog_person_id,catalog_reconciliation_id
        ) VALUES (8301,8201,1,'SYNTHETIC',100,100,1,'NONE','OK','[]','{}',?,?)
        """,
        (person_id, reconciliation_id),
    )
    conn.execute(
        """
        INSERT INTO nomina_banorte_exports (
            id,created_by,created_at,timezone,layout_date,layout_date_auto,
            consecutive,filename,payment_count,total_cents,capture_origin,
            incidents_json,manual_row_count,aliases_used_json,
            recommendations_accepted_json,warnings_ignored_json,file_sha256,
            file_size,file_blob,status
        ) VALUES (8401,'fixture','t','UTC','2026-01-01','2026-01-01','01',
                  'fixture.pag',1,100,'MANUAL_CAPTURE','[]',1,'[]','[]','[]',
                  ?,1,X'00','GENERATED')
        """,
        ("b" * 64,),
    )
    conn.execute(
        """
        INSERT INTO nomina_banorte_export_items (
            id,export_id,position,nombre_recibido,employee_number_effective,
            account_number,amount_cents,match_kind,validation_status,record_status,
            is_manual_beneficiary,warnings_json,user_decision_json,
            catalog_person_id,catalog_reconciliation_id
        ) VALUES (8501,8401,1,'SYNTHETIC','0000000001','0000000001',100,
                  'NONE','IMPORTADO_EXITOSO','ACTIVO',0,'[]','{}',?,?)
        """,
        (person_id, reconciliation_id),
    )


def _snapshot(conn: sqlite3.Connection) -> dict[str, object]:
    rows = [
        tuple(row[column] for column in OLD_COLUMNS)
        for row in conn.execute(
            f"SELECT {','.join(OLD_COLUMNS)} FROM {RECONCILIATION_TABLE} ORDER BY id"
        )
    ]
    status_counts = Counter(
        {str(row[0]): int(row[1]) for row in conn.execute(
            f"SELECT reconciliation_status,COUNT(*) FROM {RECONCILIATION_TABLE} GROUP BY reconciliation_status"
        )}
    )
    method_counts = Counter(
        {str(row[0]): int(row[1]) for row in conn.execute(
            f"SELECT match_method,COUNT(*) FROM {RECONCILIATION_TABLE} GROUP BY match_method"
        )}
    )
    sequence = conn.execute(
        "SELECT seq FROM sqlite_sequence WHERE name=?", (RECONCILIATION_TABLE,)
    ).fetchone()
    child_refs = (
        conn.execute("SELECT reconciliation_id FROM nomina_banorte_catalog_events WHERE id=8101").fetchone()[0],
        conn.execute("SELECT catalog_reconciliation_id FROM nomina_banorte_export_draft_rows WHERE id=8301").fetchone()[0],
        conn.execute("SELECT catalog_reconciliation_id FROM nomina_banorte_export_items WHERE id=8501").fetchone()[0],
    )
    return {
        "rows": rows,
        "count": len(rows),
        "status_counts": status_counts,
        "method_counts": method_counts,
        "current_count": sum(1 for row in rows if row[12] == 1),
        "min_id": min(row[0] for row in rows),
        "max_id": max(row[0] for row in rows),
        "sequence": int(sequence[0]) if sequence is not None else None,
        "child_refs": child_refs,
        "fk": {tuple(row) for row in conn.execute("PRAGMA foreign_key_check")},
        "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
    }


def _seed_old_database(path: str | Path) -> dict[str, object]:
    conn = _connect(path)
    _downgrade_reconciliations_to_old_schema(conn)
    _insert_version(conn, 101, "SUPERSEDED")
    _insert_version(conn, 202, "ACTIVE")
    _insert_beneficiary(conn, 501)
    pairs = (
        (7001, 1001, "UNMATCHED", "NONE", 0, None),
        (7002, 1001, "AUTO_MATCHED", "EXACT_EMPLOYEE_ACCOUNT_RAW_NAME", 1, 7001),
        (7003, 1003, "MANUAL_MATCHED", "MANUAL_SELECTION", 1, None),
        (7004, 1004, "MULTIPLE_CANDIDATES", "EXACT_EMPLOYEE_ACCOUNT_CANONICAL_NAME", 1, None),
        (7005, 1005, "ACCOUNT_MISMATCH", "EXACT_EMPLOYEE_ACCOUNT_CONTROLLED_MA", 1, None),
        (7006, 1006, "EMPLOYEE_MISMATCH", "NONE", 1, None),
        (7007, 1007, "IDENTITY_CONFLICT", "NONE", 1, None),
        (7008, 1008, "LEGACY_NOT_USABLE", "NONE", 1, None),
        (7009, 1009, "STALE_RECONCILIATION", "NONE", 1, None),
    )
    for _, person_id, *_ in pairs:
        if conn.execute(
            "SELECT 1 FROM nomina_banorte_catalog_persons WHERE id=?", (person_id,)
        ).fetchone() is None:
            _insert_person(conn, person_id, 101)
    for reconciliation_id, person_id, status, method, is_current, supersedes in pairs:
        _insert_old_reconciliation(
            conn,
            reconciliation_id=reconciliation_id,
            version_id=101,
            person_id=person_id,
            beneficiary_id=501 if status in {"AUTO_MATCHED", "MANUAL_MATCHED"} else None,
            status=status,
            method=method,
            is_current=is_current,
            supersedes_id=supersedes,
        )
    _seed_referenced_children(conn, 7002, 1001)
    conn.commit()
    snapshot = _snapshot(conn)
    assert set(snapshot["status_counts"]) == OLD_STATUSES
    assert set(snapshot["method_counts"]) == OLD_METHODS
    assert snapshot["fk"] == set()
    assert snapshot["integrity"] == "ok"
    conn.close()
    return snapshot


def test_fresh_schema_installs_c1_columns_checks_fks_indexes_and_integrity(tmp_path):
    conn = _connect(tmp_path / "fresh.db")
    ensure_banorte_tables(conn)
    ensure_banorte_tables(conn)

    columns = _table_columns(conn, RECONCILIATION_TABLE)
    assert LINEAGE_COLUMNS <= columns
    sql = _table_sql(conn, RECONCILIATION_TABLE)
    for token in OLD_STATUSES | OLD_METHODS | NEW_METHODS | {
        "CATALOG_BOUND",
        "CONFIRMED",
        "UNCONFIRMED",
    }:
        assert f"'{token}'" in sql
    assert "lineage_evidence_sha256" in sql
    assert "GLOB '*[^0-9a-f]*'" in sql

    fks = {
        (str(row[3]), str(row[2]), str(row[4]), str(row[6]))
        for row in conn.execute(f"PRAGMA foreign_key_list({RECONCILIATION_TABLE})")
    }
    assert (
        "lineage_predecessor_person_id",
        "nomina_banorte_catalog_persons",
        "id",
        "RESTRICT",
    ) in fks
    assert (
        "lineage_predecessor_beneficiary_id",
        "nomina_banorte_beneficiaries",
        "id",
        "RESTRICT",
    ) in fks

    indexes = _index_sql(conn, RECONCILIATION_TABLE)
    assert "uq_banorte_catalog_reconciliation_current" in indexes
    assert "idx_banorte_catalog_reconciliations_version_status" in indexes
    for name, column in (
        ("uq_banorte_catalog_lineage_predecessor_person_current", "lineage_predecessor_person_id"),
        (
            "uq_banorte_catalog_lineage_predecessor_beneficiary_current",
            "lineage_predecessor_beneficiary_id",
        ),
    ):
        normalized = " ".join(indexes[name].split())
        assert f"(version_id,{column})" in normalized.replace(" ", "")
        assert "is_current = 1" in normalized
        assert "lineage_status = 'CONFIRMED'" in normalized
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_old_schema_migration_preserves_rows_history_sequences_and_child_fks(tmp_path):
    path = tmp_path / "old.db"
    before = _seed_old_database(path)
    conn = _connect(path)
    ensure_banorte_tables(conn)
    after = _snapshot(conn)

    assert after == before
    assert LINEAGE_COLUMNS <= _table_columns(conn, RECONCILIATION_TABLE)
    null_counts = conn.execute(
        f"""
        SELECT
            SUM(lineage_status IS NULL),
            SUM(lineage_predecessor_person_id IS NULL),
            SUM(lineage_predecessor_beneficiary_id IS NULL),
            SUM(lineage_evidence_json IS NULL),
            SUM(lineage_evidence_sha256 IS NULL)
        FROM {RECONCILIATION_TABLE}
        """
    ).fetchone()
    assert tuple(null_counts) == (before["count"],) * 5
    incoming_targets = {
        str(row[2])
        for table in (
            "nomina_banorte_catalog_events",
            "nomina_banorte_export_draft_rows",
            "nomina_banorte_export_items",
        )
        for row in conn.execute(f"PRAGMA foreign_key_list({table})")
        if str(row[3]) in {"reconciliation_id", "catalog_reconciliation_id"}
    }
    assert incoming_targets == {RECONCILIATION_TABLE}
    conn.close()


def _seed_contract_context(conn: sqlite3.Connection) -> dict[str, int]:
    ensure_banorte_tables(conn)
    for version_id in (1, 2, 3):
        _insert_version(conn, version_id)
    for person_id, version_id in (
        (10, 1),
        (20, 2),
        (21, 2),
        (22, 2),
        (30, 3),
        (31, 3),
    ):
        _insert_person(conn, person_id, version_id)
    for beneficiary_id in (10, 20, 21, 22, 30):
        _insert_beneficiary(conn, beneficiary_id)
    conn.commit()
    return {
        "predecessor_person": 10,
        "predecessor_beneficiary": 10,
    }


def _insert_c1_reconciliation(
    conn: sqlite3.Connection,
    *,
    reconciliation_id: int,
    version_id: int,
    person_id: int,
    status: str = "UNMATCHED",
    method: str = "NONE",
    beneficiary_id: int | None = None,
    lineage_status: str | None = None,
    predecessor_person_id: int | None = None,
    predecessor_beneficiary_id: int | None = None,
    evidence_json: str | None = None,
    evidence_sha: str | None = None,
    manual_reason: str | None = None,
    is_current: int = 1,
    include_fingerprint: bool | None = None,
) -> None:
    fingerprinted = (
        beneficiary_id is not None
        if include_fingerprint is None
        else include_fingerprint
    )
    conn.execute(
        f"""
        INSERT INTO {RECONCILIATION_TABLE} (
            id,version_id,person_id,beneficiary_id,reconciliation_status,match_method,
            candidate_count,beneficiary_material_fingerprint_version,
            beneficiary_material_state_json,beneficiary_material_fingerprint,
            is_current,manual_reason,created_by,created_at,lineage_status,
            lineage_predecessor_person_id,lineage_predecessor_beneficiary_id,
            lineage_evidence_json,lineage_evidence_sha256
        ) VALUES (?,?,?,?,?,?,0,?,?,?,?,?,'fixture','t',?,?,?,?,?)
        """,
        (
            reconciliation_id,
            version_id,
            person_id,
            beneficiary_id,
            status,
            method,
            "v1" if fingerprinted else None,
            "{}" if fingerprinted else None,
            "c" * 64 if fingerprinted else None,
            is_current,
            manual_reason,
            lineage_status,
            predecessor_person_id,
            predecessor_beneficiary_id,
            evidence_json,
            evidence_sha,
        ),
    )


def _assert_integrity_error(conn: sqlite3.Connection, **kwargs) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _insert_c1_reconciliation(conn, **kwargs)


def test_c1_constraint_matrix_and_version_scoped_predecessor_uniqueness():
    conn = _connect(":memory:")
    _seed_contract_context(conn)

    _insert_c1_reconciliation(
        conn,
        reconciliation_id=100,
        version_id=2,
        person_id=20,
        status="CATALOG_BOUND",
        beneficiary_id=20,
        lineage_status="UNCONFIRMED",
    )
    _insert_c1_reconciliation(
        conn,
        reconciliation_id=101,
        version_id=2,
        person_id=21,
        status="CATALOG_BOUND",
        method="PREVIOUS_ACTIVE_RFC_BIRTH_RAW_NAME",
        beneficiary_id=21,
        lineage_status="CONFIRMED",
        predecessor_person_id=10,
        evidence_json="{}",
        evidence_sha="d" * 64,
    )
    _insert_c1_reconciliation(
        conn,
        reconciliation_id=102,
        version_id=2,
        person_id=22,
        lineage_status="UNCONFIRMED",
    )
    _insert_c1_reconciliation(
        conn,
        reconciliation_id=103,
        version_id=3,
        person_id=30,
        status="CATALOG_BOUND",
        method="MANUAL_CONTINUITY_CONFIRMED",
        beneficiary_id=30,
        lineage_status="CONFIRMED",
        predecessor_beneficiary_id=10,
        evidence_json="{}",
        evidence_sha="e" * 64,
        manual_reason="confirmed by fixture",
    )

    invalid_base = dict(reconciliation_id=200, version_id=3, person_id=31)
    _assert_integrity_error(
        conn,
        **invalid_base,
        status="CATALOG_BOUND",
        lineage_status="UNCONFIRMED",
    )
    _assert_integrity_error(
        conn,
        **invalid_base,
        status="CATALOG_BOUND",
        beneficiary_id=30,
    )
    _assert_integrity_error(
        conn,
        **invalid_base,
        status="CATALOG_BOUND",
        beneficiary_id=30,
        lineage_status="UNCONFIRMED",
        include_fingerprint=False,
    )
    for overrides in (
        {},
        {"predecessor_person_id": 10},
        {"predecessor_person_id": 10, "evidence_json": "{}"},
    ):
        _assert_integrity_error(
            conn,
            **invalid_base,
            method="PREVIOUS_ACTIVE_RFC_BIRTH_RAW_NAME",
            lineage_status="CONFIRMED",
            **overrides,
        )
    _assert_integrity_error(
        conn,
        **invalid_base,
        method="NONE",
        lineage_status="CONFIRMED",
        predecessor_person_id=10,
        evidence_json="{}",
        evidence_sha="f" * 64,
    )
    _assert_integrity_error(
        conn,
        **invalid_base,
        method="PREVIOUS_ACTIVE_RFC_BIRTH_RAW_NAME",
        lineage_status="UNCONFIRMED",
    )
    _assert_integrity_error(
        conn,
        **invalid_base,
        method="MANUAL_CONTINUITY_CONFIRMED",
        lineage_status="CONFIRMED",
        predecessor_person_id=10,
        evidence_json="{}",
        evidence_sha="f" * 64,
        manual_reason="   ",
    )
    _assert_integrity_error(
        conn,
        **invalid_base,
        method="PREVIOUS_ACTIVE_RFC_BIRTH_RAW_NAME",
        lineage_status="CONFIRMED",
        predecessor_person_id=10,
        evidence_json="{}",
        evidence_sha="F" * 64,
    )
    conn.close()

    for predecessor_kind, column in (
        ("person", "predecessor_person_id"),
        ("beneficiary", "predecessor_beneficiary_id"),
    ):
        def kwargs(value: int) -> dict[str, int]:
            return {column: value}

        same = _connect(":memory:")
        ids = _seed_contract_context(same)
        predecessor = ids[f"predecessor_{predecessor_kind}"]
        for reconciliation_id, person_id in ((300, 20), (301, 21)):
            call = dict(
                reconciliation_id=reconciliation_id,
                version_id=2,
                person_id=person_id,
                method="PREVIOUS_ACTIVE_RFC_BIRTH_RAW_NAME",
                lineage_status="CONFIRMED",
                evidence_json="{}",
                evidence_sha="a" * 64,
                **kwargs(predecessor),
            )
            if reconciliation_id == 300:
                _insert_c1_reconciliation(same, **call)
            else:
                _assert_integrity_error(same, **call)
        same.close()

        different = _connect(":memory:")
        ids = _seed_contract_context(different)
        predecessor = ids[f"predecessor_{predecessor_kind}"]
        _insert_c1_reconciliation(
            different,
            reconciliation_id=310,
            version_id=2,
            person_id=20,
            method="PREVIOUS_ACTIVE_RFC_BIRTH_RAW_NAME",
            lineage_status="CONFIRMED",
            evidence_json="{}",
            evidence_sha="a" * 64,
            **kwargs(predecessor),
        )
        _insert_c1_reconciliation(
            different,
            reconciliation_id=311,
            version_id=3,
            person_id=30,
            method="PREVIOUS_ACTIVE_RFC_BIRTH_RAW_NAME",
            lineage_status="CONFIRMED",
            evidence_json="{}",
            evidence_sha="b" * 64,
            **kwargs(predecessor),
        )
        different.close()

        historical = _connect(":memory:")
        ids = _seed_contract_context(historical)
        predecessor = ids[f"predecessor_{predecessor_kind}"]
        _insert_c1_reconciliation(
            historical,
            reconciliation_id=320,
            version_id=2,
            person_id=20,
            method="PREVIOUS_ACTIVE_RFC_BIRTH_RAW_NAME",
            lineage_status="CONFIRMED",
            evidence_json="{}",
            evidence_sha="a" * 64,
            is_current=0,
            **kwargs(predecessor),
        )
        _insert_c1_reconciliation(
            historical,
            reconciliation_id=321,
            version_id=2,
            person_id=21,
            method="PREVIOUS_ACTIVE_RFC_BIRTH_RAW_NAME",
            lineage_status="CONFIRMED",
            evidence_json="{}",
            evidence_sha="b" * 64,
            **kwargs(predecessor),
        )
        historical.close()


def test_migration_failpoints_roll_back_then_retry_and_second_ensure_is_noop(tmp_path, monkeypatch):
    for failpoint in ("before_create_new", "after_copy", "before_commit"):
        path = tmp_path / f"rollback-{failpoint}.db"
        before = _seed_old_database(path)

        def inject(name: str, expected: str = failpoint) -> None:
            if name == expected:
                raise RuntimeError(f"fixture:{expected}")

        monkeypatch.setattr(
            schema_module, "_catalog_reconciliation_migration_failpoint", inject
        )
        conn = _connect(path)
        with pytest.raises(RuntimeError, match=f"fixture:{failpoint}"):
            ensure_banorte_tables(conn)
        assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
        conn.close()

        rolled_back = _connect(path)
        assert LINEAGE_COLUMNS.isdisjoint(
            _table_columns(rolled_back, RECONCILIATION_TABLE)
        )
        assert _snapshot(rolled_back) == before
        assert rolled_back.execute("PRAGMA foreign_key_check").fetchall() == []
        assert rolled_back.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert rolled_back.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'nomina_banorte_catalog_reconciliations__%'"
        ).fetchall() == []
        rolled_back.close()

        monkeypatch.setattr(
            schema_module,
            "_catalog_reconciliation_migration_failpoint",
            lambda _name: None,
        )
        retry = _connect(path)
        ensure_banorte_tables(retry)
        retry_snapshot = _snapshot(retry)
        assert retry_snapshot == before
        assert LINEAGE_COLUMNS <= _table_columns(retry, RECONCILIATION_TABLE)
        schema_before_second = _table_sql(retry, RECONCILIATION_TABLE)
        indexes_before_second = _index_sql(retry, RECONCILIATION_TABLE)
        ensure_banorte_tables(retry)
        assert _snapshot(retry) == retry_snapshot
        assert _table_sql(retry, RECONCILIATION_TABLE) == schema_before_second
        assert _index_sql(retry, RECONCILIATION_TABLE) == indexes_before_second
        assert retry.execute("PRAGMA foreign_key_check").fetchall() == []
        assert retry.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        retry.close()
