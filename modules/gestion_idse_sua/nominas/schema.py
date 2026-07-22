from __future__ import annotations

import sqlite3

GIS_NOMINA_TABLES: tuple[str, ...] = (
    "gis_nomina_imports",
    "gis_nomina_sheets",
    "gis_nomina_periods",
    "gis_planta_cliente",
    "gis_cliente_corte",
    "gis_nomina_workers",
    "gis_nomina_matches",
    "gis_nomina_comparatives",
    "gis_nomina_results",
)

_PLANTA_CLIENTE_SEED: tuple[tuple[str, str], ...] = (
    ("FLOTADO", "VITROFLEX"),
    ("MACRO NORTE", "VITROFLEX"),
    ("MERCADO REPUESTO", "VITROFLEX"),
)


def ensure_gis_nominas_tables(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gis_nomina_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_filename TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            uploaded_by TEXT,
            uploaded_at TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN (
                    'uploaded', 'classified', 'period_confirmed',
                    'extracted', 'review', 'compared', 'completed', 'error'
                ))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gis_nomina_sheets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_id INTEGER NOT NULL,
            sheet_index INTEGER NOT NULL,
            sheet_name TEXT NOT NULL,
            is_hidden INTEGER NOT NULL DEFAULT 0,
            suggested_classification TEXT,
            confirmed_classification TEXT
                CHECK (confirmed_classification IS NULL OR confirmed_classification IN (
                    'nomina', 'auxiliar', 'ignorada'
                )),
            estimated_rows INTEGER NOT NULL DEFAULT 0,
            suggested_period_json TEXT,
            FOREIGN KEY(import_id) REFERENCES gis_nomina_imports(id) ON DELETE CASCADE,
            UNIQUE(import_id, sheet_index)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gis_nomina_periods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sheet_id INTEGER NOT NULL UNIQUE,
            fecha_inicio TEXT,
            fecha_fin TEXT,
            semana_num INTEGER,
            detection_source TEXT,
            user_confirmed INTEGER NOT NULL DEFAULT 0,
            cut_warning TEXT,
            FOREIGN KEY(sheet_id) REFERENCES gis_nomina_sheets(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gis_planta_cliente (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            planta_normalizada TEXT NOT NULL UNIQUE,
            planta_original TEXT,
            cliente TEXT NOT NULL,
            confirmed_by TEXT,
            confirmed_at TEXT,
            source TEXT NOT NULL DEFAULT 'seed'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gis_cliente_corte (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente TEXT NOT NULL UNIQUE,
            weekday_start INTEGER,
            confirmed_by TEXT,
            confirmed_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gis_nomina_workers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_id INTEGER NOT NULL,
            row_number INTEGER NOT NULL,
            num_empleado TEXT,
            nombre_original TEXT NOT NULL,
            nombre_normalizado TEXT NOT NULL,
            puesto TEXT,
            planta_original TEXT,
            planta_normalizada TEXT,
            cuenta TEXT,
            row_json TEXT,
            cliente_sugerido TEXT,
            cliente_confirmado TEXT,
            suggestion_source TEXT,
            suggestion_confidence REAL,
            discarded_reason TEXT,
            FOREIGN KEY(period_id) REFERENCES gis_nomina_periods(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gis_nomina_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker_id INTEGER NOT NULL UNIQUE,
            headcount_key TEXT,
            match_method TEXT NOT NULL,
            confidence REAL,
            status TEXT NOT NULL
                CHECK (status IN (
                    'auto', 'suggested', 'confirmed', 'manual',
                    'unmatched', 'review'
                )),
            nss TEXT,
            rfc TEXT,
            curp TEXT,
            hc_nombre TEXT,
            hc_json TEXT,
            FOREIGN KEY(worker_id) REFERENCES gis_nomina_workers(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gis_nomina_comparatives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_id INTEGER NOT NULL,
            cliente TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            generated_by TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            warnings_json TEXT,
            FOREIGN KEY(period_id) REFERENCES gis_nomina_periods(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gis_nomina_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comparative_id INTEGER NOT NULL,
            worker_id INTEGER,
            headcount_only INTEGER NOT NULL DEFAULT 0,
            hc_nombre TEXT,
            resultado TEXT NOT NULL,
            semaforo TEXT NOT NULL,
            tipo_sugerido TEXT,
            fecha_sugerida TEXT,
            decision_final TEXT,
            conversion_status TEXT NOT NULL DEFAULT 'none'
                CHECK (conversion_status IN ('none', 'pending', 'converted', 'excluded')),
            movimiento_id TEXT,
            exclusion_reason TEXT,
            observaciones TEXT,
            FOREIGN KEY(comparative_id) REFERENCES gis_nomina_comparatives(id) ON DELETE CASCADE,
            FOREIGN KEY(worker_id) REFERENCES gis_nomina_workers(id) ON DELETE SET NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gis_nomina_sheets_import ON gis_nomina_sheets(import_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gis_nomina_workers_period ON gis_nomina_workers(period_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gis_nomina_results_comp ON gis_nomina_results(comparative_id)"
    )
    _seed_planta_cliente(conn)


def _seed_planta_cliente(conn: sqlite3.Connection) -> None:
    for planta, cliente in _PLANTA_CLIENTE_SEED:
        conn.execute(
            """
            INSERT OR IGNORE INTO gis_planta_cliente
                (planta_normalizada, planta_original, cliente, source)
            VALUES (?, ?, ?, 'seed')
            """,
            (planta, planta, cliente),
        )
