from __future__ import annotations

import sqlite3

BANORTE_TABLES: tuple[str, ...] = (
    "nomina_banorte_beneficiaries",
    "nomina_banorte_aliases",
    "nomina_banorte_import_batches",
    "nomina_banorte_import_rows",
    "nomina_banorte_exports",
    "nomina_banorte_export_items",
    "nomina_banorte_export_drafts",
    "nomina_banorte_export_draft_rows",
    "nomina_banorte_beneficiary_events",
)


def ensure_banorte_tables(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_banorte_beneficiaries (
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
                    'INACTIVO_MANUAL',
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
            FOREIGN KEY (replaces_id)
                REFERENCES nomina_banorte_beneficiaries(id) ON DELETE RESTRICT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banorte_ben_nombre_norm
            ON nomina_banorte_beneficiaries(nombre_normalizado)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banorte_ben_emp_eff
            ON nomina_banorte_beneficiaries(employee_number_effective)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banorte_ben_account
            ON nomina_banorte_beneficiaries(account_number)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banorte_ben_curp
            ON nomina_banorte_beneficiaries(curp)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banorte_ben_record_status
            ON nomina_banorte_beneficiaries(record_status)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banorte_ben_validation_status
            ON nomina_banorte_beneficiaries(validation_status)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_banorte_active_emp
            ON nomina_banorte_beneficiaries(employee_number_effective)
            WHERE record_status = 'ACTIVO'
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_banorte_active_account
            ON nomina_banorte_beneficiaries(account_number)
            WHERE record_status = 'ACTIVO'
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_banorte_replaces_id
            ON nomina_banorte_beneficiaries(replaces_id)
            WHERE replaces_id IS NOT NULL
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_banorte_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alias_original TEXT NOT NULL,
            alias_normalizado TEXT NOT NULL,
            beneficiary_id INTEGER NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
                CHECK (is_active IN (0, 1)),
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            deactivated_by TEXT,
            deactivated_at TEXT,
            FOREIGN KEY (beneficiary_id)
                REFERENCES nomina_banorte_beneficiaries(id) ON DELETE RESTRICT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banorte_alias_norm
            ON nomina_banorte_aliases(alias_normalizado)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_banorte_active_alias_norm
            ON nomina_banorte_aliases(alias_normalizado)
            WHERE is_active = 1
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_banorte_import_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_name TEXT NOT NULL,
            file_sha256 TEXT NOT NULL,
            file_size INTEGER NOT NULL CHECK (file_size >= 0),
            detected_type TEXT NOT NULL
                CHECK (detected_type IN (
                    'ALTAS_NOMINA_BANORTE',
                    'REPORTE_DETALLADO'
                )),
            imported_by TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            rows_processed INTEGER NOT NULL,
            count_exitosos INTEGER NOT NULL,
            count_manuales INTEGER NOT NULL,
            count_fallidos_estatus INTEGER NOT NULL,
            count_fallidos_hoja_sin_estatus INTEGER NOT NULL,
            count_excluidos_hoja_fallidos_total INTEGER NOT NULL,
            count_duplicados_reemplazados INTEGER NOT NULL,
            count_conflictos INTEGER NOT NULL,
            count_omitidos INTEGER NOT NULL,
            summary_json TEXT NOT NULL,
            reimport_confirmed INTEGER NOT NULL DEFAULT 0
                CHECK (reimport_confirmed IN (0, 1))
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banorte_import_sha
            ON nomina_banorte_import_batches(file_sha256)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_banorte_import_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            sheet_name TEXT NOT NULL,
            row_number INTEGER NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT NOT NULL,
            nombre TEXT,
            curp TEXT,
            employee_number_requested TEXT,
            employee_number_effective TEXT,
            account_number TEXT,
            estatus_raw TEXT,
            comentarios_raw TEXT,
            beneficiary_id INTEGER,
            payload_json TEXT,
            FOREIGN KEY (batch_id)
                REFERENCES nomina_banorte_import_batches(id) ON DELETE RESTRICT,
            FOREIGN KEY (beneficiary_id)
                REFERENCES nomina_banorte_beneficiaries(id) ON DELETE RESTRICT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banorte_import_rows_batch
            ON nomina_banorte_import_rows(batch_id)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_banorte_exports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            timezone TEXT NOT NULL,
            layout_date TEXT NOT NULL,
            layout_date_auto TEXT NOT NULL,
            date_override_confirmed INTEGER NOT NULL DEFAULT 0
                CHECK (date_override_confirmed IN (0, 1)),
            consecutive TEXT NOT NULL
                CHECK (
                    length(consecutive) = 2
                    AND consecutive GLOB '[0-9][0-9]'
                    AND consecutive BETWEEN '01' AND '99'
                ),
            filename TEXT NOT NULL,
            payment_count INTEGER NOT NULL CHECK (payment_count >= 0),
            total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
            capture_origin TEXT NOT NULL,
            incidents_json TEXT NOT NULL,
            manual_row_count INTEGER NOT NULL,
            aliases_used_json TEXT NOT NULL,
            recommendations_accepted_json TEXT NOT NULL,
            warnings_ignored_json TEXT NOT NULL,
            duplicate_consecutive_confirmed INTEGER NOT NULL DEFAULT 0
                CHECK (duplicate_consecutive_confirmed IN (0, 1)),
            duplicate_of_export_id INTEGER,
            file_sha256 TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            file_blob BLOB NOT NULL,
            status TEXT NOT NULL CHECK (status = 'GENERATED'),
            FOREIGN KEY (duplicate_of_export_id)
                REFERENCES nomina_banorte_exports(id) ON DELETE RESTRICT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banorte_exports_date_consec
            ON nomina_banorte_exports(layout_date, consecutive)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banorte_exports_created
            ON nomina_banorte_exports(created_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banorte_exports_filename
            ON nomina_banorte_exports(filename)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banorte_exports_sha
            ON nomina_banorte_exports(file_sha256)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_banorte_export_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            export_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            nombre_recibido TEXT NOT NULL,
            beneficiary_id INTEGER,
            employee_number_effective TEXT NOT NULL,
            account_number TEXT NOT NULL,
            curp TEXT,
            amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
            match_kind TEXT NOT NULL,
            alias_id INTEGER,
            validation_status TEXT NOT NULL,
            record_status TEXT NOT NULL,
            is_manual_beneficiary INTEGER NOT NULL
                CHECK (is_manual_beneficiary IN (0, 1)),
            warnings_json TEXT NOT NULL,
            user_decision_json TEXT NOT NULL,
            FOREIGN KEY (export_id)
                REFERENCES nomina_banorte_exports(id) ON DELETE RESTRICT,
            FOREIGN KEY (beneficiary_id)
                REFERENCES nomina_banorte_beneficiaries(id) ON DELETE RESTRICT,
            FOREIGN KEY (alias_id)
                REFERENCES nomina_banorte_aliases(id) ON DELETE RESTRICT,
            UNIQUE (export_id, position)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banorte_export_items_export
            ON nomina_banorte_export_items(export_id)
        """
    )

    _migrate_banorte_schema(conn)


def _table_cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl_type: str) -> None:
    if column not in _table_cols(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")


def _migrate_banorte_schema(conn: sqlite3.Connection) -> None:
    """Additive migrations only; never DROP Banorte tables."""
    _add_column_if_missing(conn, "nomina_banorte_exports", "calculo_id", "INTEGER")
    _add_column_if_missing(conn, "nomina_banorte_exports", "draft_id", "INTEGER")
    # capture_origin already exists NOT NULL on Fase 1 CREATE; if somehow missing:
    if "capture_origin" not in _table_cols(conn, "nomina_banorte_exports"):
        conn.execute("ALTER TABLE nomina_banorte_exports ADD COLUMN capture_origin TEXT")

    _add_column_if_missing(conn, "nomina_banorte_export_items", "calculo_row_id", "INTEGER")

    _add_column_if_missing(conn, "nomina_banorte_beneficiaries", "replace_reason", "TEXT")
    _add_column_if_missing(conn, "nomina_banorte_beneficiaries", "replaced_by", "TEXT")
    _add_column_if_missing(conn, "nomina_banorte_beneficiaries", "replaced_at", "TEXT")
    _add_column_if_missing(
        conn,
        "nomina_banorte_beneficiaries",
        "manual_effective_from_account",
        "INTEGER NOT NULL DEFAULT 0",
    )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_banorte_exports_calculo ON nomina_banorte_exports(calculo_id)"
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_banorte_exports_draft_id
            ON nomina_banorte_exports(draft_id)
            WHERE draft_id IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banorte_export_items_calculo_row
            ON nomina_banorte_export_items(calculo_row_id)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_banorte_export_drafts (
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
            revision INTEGER NOT NULL DEFAULT 1
                CHECK (revision >= 1),
            consecutive_pref TEXT,
            layout_date_pref TEXT,
            CHECK (
                (origin_kind = 'CALCULO_RUN' AND calculo_id IS NOT NULL)
                OR (origin_kind = 'MANUAL_CAPTURE' AND calculo_id IS NULL)
            ),
            CHECK (length(origin_hash) > 0)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_banorte_drafts_calculo ON nomina_banorte_export_drafts(calculo_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_banorte_drafts_status ON nomina_banorte_export_drafts(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_banorte_drafts_user ON nomina_banorte_export_drafts(created_by)"
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_banorte_draft_open_user_calculo
            ON nomina_banorte_export_drafts(created_by, calculo_id)
            WHERE status = 'OPEN'
              AND origin_kind = 'CALCULO_RUN'
              AND calculo_id IS NOT NULL
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_banorte_export_draft_rows (
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
            amount_original_cents INTEGER NOT NULL
                CHECK (amount_original_cents >= 0),
            amount_final_cents INTEGER NOT NULL
                CHECK (amount_final_cents >= 0),
            included INTEGER NOT NULL
                CHECK (included IN (0, 1)),
            match_kind TEXT NOT NULL,
            alias_id INTEGER,
            row_state TEXT NOT NULL
                CHECK (row_state IN ('OK', 'NEEDS_REVIEW', 'BLOCKED', 'EXCLUDED')),
            warnings_json TEXT NOT NULL,
            user_decision_json TEXT NOT NULL,
            CHECK (included = 0 OR amount_final_cents > 0),
            FOREIGN KEY (draft_id)
                REFERENCES nomina_banorte_export_drafts(id) ON DELETE CASCADE,
            FOREIGN KEY (beneficiary_id)
                REFERENCES nomina_banorte_beneficiaries(id) ON DELETE RESTRICT,
            FOREIGN KEY (alias_id)
                REFERENCES nomina_banorte_aliases(id) ON DELETE RESTRICT,
            UNIQUE (draft_id, position)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banorte_draft_rows_draft
            ON nomina_banorte_export_draft_rows(draft_id)
        """
    )

    _add_column_if_missing(conn, "nomina_banorte_export_draft_rows", "excluded_at", "TEXT")
    _add_column_if_missing(conn, "nomina_banorte_export_draft_rows", "excluded_by", "TEXT")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banorte_draft_rows_excluded
            ON nomina_banorte_export_draft_rows(draft_id, excluded_at)
        """
    )

    _migrate_drafts_excel_nomina(conn)
    _migrate_beneficiaries_inactivo_manual(conn)
    _ensure_beneficiary_events(conn)


def _beneficiaries_sql_allows_inactivo_manual(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='nomina_banorte_beneficiaries'"
    ).fetchone()
    if row is None or not row[0]:
        return False
    return "INACTIVO_MANUAL" in str(row[0])


def _migrate_beneficiaries_inactivo_manual(conn: sqlite3.Connection) -> None:
    """Rebuild beneficiaries CHECK to include INACTIVO_MANUAL (idempotent)."""
    if not _table_cols(conn, "nomina_banorte_beneficiaries"):
        return
    if _beneficiaries_sql_allows_inactivo_manual(conn):
        return
    before = int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_beneficiaries").fetchone()[0])
    cols = _table_cols(conn, "nomina_banorte_beneficiaries")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            CREATE TABLE nomina_banorte_beneficiaries__new (
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
                        'INACTIVO_MANUAL',
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
        )
        select_cols = [
            "id",
            "nombre_original",
            "nombre_normalizado",
            "curp",
            "employee_number_requested",
            "employee_number_effective",
            "account_number",
            "source_kind",
            "validation_status",
            "record_status",
            "banorte_employee_substituted",
            "banorte_comment",
            "source_filename",
            "source_sheet",
            "source_row",
            "report_date",
            "imported_at",
            "imported_by",
            "replaces_id",
            "created_at",
            "updated_at",
        ]
        optional = [
            ("replace_reason", "NULL"),
            ("replaced_by", "NULL"),
            ("replaced_at", "NULL"),
            ("manual_effective_from_account", "0"),
        ]
        insert_cols = list(select_cols)
        select_expr = list(select_cols)
        for col, default in optional:
            insert_cols.append(col)
            if col in cols:
                select_expr.append(col)
            else:
                select_expr.append(default)
        conn.execute(
            f"""
            INSERT INTO nomina_banorte_beneficiaries__new ({", ".join(insert_cols)})
            SELECT {", ".join(select_expr)}
            FROM nomina_banorte_beneficiaries
            """
        )
        after = int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_beneficiaries__new").fetchone()[0])
        if after != before:
            raise RuntimeError("beneficiary_migration_count_mismatch")
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DROP TABLE nomina_banorte_beneficiaries")
        conn.execute(
            "ALTER TABLE nomina_banorte_beneficiaries__new RENAME TO nomina_banorte_beneficiaries"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_banorte_ben_nombre_norm ON nomina_banorte_beneficiaries(nombre_normalizado)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_banorte_ben_emp_eff ON nomina_banorte_beneficiaries(employee_number_effective)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_banorte_ben_account ON nomina_banorte_beneficiaries(account_number)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_banorte_ben_curp ON nomina_banorte_beneficiaries(curp)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_banorte_ben_record_status ON nomina_banorte_beneficiaries(record_status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_banorte_ben_validation_status ON nomina_banorte_beneficiaries(validation_status)"
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_banorte_active_emp
                ON nomina_banorte_beneficiaries(employee_number_effective)
                WHERE record_status = 'ACTIVO'
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_banorte_active_account
                ON nomina_banorte_beneficiaries(account_number)
                WHERE record_status = 'ACTIVO'
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_banorte_replaces_id
                ON nomina_banorte_beneficiaries(replaces_id)
                WHERE replaces_id IS NOT NULL
            """
        )
        bad = conn.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise RuntimeError(f"beneficiary_migration_fk_check:{bad}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
    except Exception:
        conn.rollback()
        conn.execute("PRAGMA foreign_keys = ON")
        raise


def _ensure_beneficiary_events(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_banorte_beneficiary_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            beneficiary_id INTEGER NOT NULL,
            action TEXT NOT NULL
                CHECK (action IN (
                    'mark_usable_manual',
                    'keep_pending',
                    'deactivate',
                    'replace',
                    'resolve_duplicate'
                )),
            reason TEXT NOT NULL
                CHECK (length(trim(reason)) > 0),
            previous_validation_status TEXT,
            new_validation_status TEXT,
            previous_record_status TEXT,
            new_record_status TEXT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            replacement_beneficiary_id INTEGER,
            FOREIGN KEY (beneficiary_id)
                REFERENCES nomina_banorte_beneficiaries(id) ON DELETE RESTRICT,
            FOREIGN KEY (replacement_beneficiary_id)
                REFERENCES nomina_banorte_beneficiaries(id) ON DELETE RESTRICT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banorte_ben_events_ben_created
            ON nomina_banorte_beneficiary_events(beneficiary_id, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_banorte_ben_events_created
            ON nomina_banorte_beneficiary_events(created_at)
        """
    )


def _drafts_sql_allows_excel_nomina(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='nomina_banorte_export_drafts'"
    ).fetchone()
    if row is None or not row[0]:
        return False
    return "EXCEL_NOMINA" in str(row[0])


def _migrate_drafts_excel_nomina(conn: sqlite3.Connection) -> None:
    """Rebuild drafts table when CHECK lacks EXCEL_NOMINA; add source_* columns."""
    if not _table_cols(conn, "nomina_banorte_export_drafts"):
        return
    _add_column_if_missing(conn, "nomina_banorte_export_drafts", "source_filename", "TEXT")
    _add_column_if_missing(conn, "nomina_banorte_export_drafts", "source_sha256", "TEXT")
    _add_column_if_missing(conn, "nomina_banorte_export_drafts", "source_sheet", "TEXT")
    _add_column_if_missing(conn, "nomina_banorte_export_drafts", "source_file_size", "INTEGER")
    if _drafts_sql_allows_excel_nomina(conn):
        return
    before = int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_export_drafts").fetchone()[0])
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            CREATE TABLE nomina_banorte_export_drafts__new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_by TEXT NOT NULL,
                updated_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                origin_kind TEXT NOT NULL
                    CHECK (origin_kind IN ('CALCULO_RUN', 'MANUAL_CAPTURE', 'EXCEL_NOMINA')),
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
                    OR (origin_kind = 'EXCEL_NOMINA' AND calculo_id IS NULL)
                ),
                CHECK (length(origin_hash) > 0)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO nomina_banorte_export_drafts__new (
                id, created_by, updated_by, created_at, updated_at, origin_kind, calculo_id,
                origin_updated_at, origin_hash, status, revision, consecutive_pref, layout_date_pref,
                source_filename, source_sha256, source_sheet, source_file_size
            )
            SELECT
                id, created_by, updated_by, created_at, updated_at, origin_kind, calculo_id,
                origin_updated_at, origin_hash, status, revision, consecutive_pref, layout_date_pref,
                source_filename, source_sha256, source_sheet, source_file_size
            FROM nomina_banorte_export_drafts
            """
        )
        after = int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_export_drafts__new").fetchone()[0])
        if after != before:
            raise RuntimeError("draft_migration_count_mismatch")
        conn.execute("DROP TABLE nomina_banorte_export_drafts")
        conn.execute("ALTER TABLE nomina_banorte_export_drafts__new RENAME TO nomina_banorte_export_drafts")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_banorte_drafts_calculo ON nomina_banorte_export_drafts(calculo_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_banorte_drafts_status ON nomina_banorte_export_drafts(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_banorte_drafts_user ON nomina_banorte_export_drafts(created_by)"
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_banorte_draft_open_user_calculo
                ON nomina_banorte_export_drafts(created_by, calculo_id)
                WHERE status = 'OPEN'
                  AND origin_kind = 'CALCULO_RUN'
                  AND calculo_id IS NOT NULL
            """
        )
        fk_issues = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_issues:
            raise RuntimeError("draft_migration_fk_check_failed")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
