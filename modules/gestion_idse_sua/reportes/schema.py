from __future__ import annotations

import sqlite3

GIS_MONTHLY_TABLES: tuple[str, ...] = (
    "gis_monthly_reports",
    "gis_monthly_report_weeks",
    "gis_monthly_report_persons",
    "gis_monthly_report_events",
)

REPORT_STATUSES = frozenset({"borrador", "generado", "en_revision", "cerrado", "cancelado"})
EVENT_STATUSES = frozenset({"propuesto", "confirmado", "descartado", "incompleto", "convertido"})


def ensure_gis_monthly_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gis_monthly_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente TEXT NOT NULL,
            mes INTEGER NOT NULL CHECK (mes BETWEEN 1 AND 12),
            anio INTEGER NOT NULL,
            estado TEXT NOT NULL DEFAULT 'borrador'
                CHECK (estado IN ('borrador', 'generado', 'en_revision', 'cerrado', 'cancelado')),
            created_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            warnings_json TEXT,
            snapshot_json TEXT,
            version TEXT NOT NULL DEFAULT '1.0'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gis_monthly_report_weeks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            period_id INTEGER NOT NULL,
            sort_order INTEGER NOT NULL,
            included_at TEXT NOT NULL,
            origin_ref TEXT,
            FOREIGN KEY(report_id) REFERENCES gis_monthly_reports(id) ON DELETE CASCADE,
            FOREIGN KEY(period_id) REFERENCES gis_nomina_periods(id),
            UNIQUE(report_id, period_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gis_monthly_report_persons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            identity_key TEXT NOT NULL,
            worker_ids_json TEXT,
            num_empleado TEXT,
            nombre_nomina TEXT,
            nombre_hc TEXT,
            match_method TEXT,
            match_status TEXT,
            nss TEXT,
            rfc TEXT,
            curp TEXT,
            sbc TEXT,
            clientes_json TEXT,
            plantas_json TEXT,
            semanas_json TEXT,
            estado_mensual TEXT,
            totals_json TEXT,
            primera_a TEXT,
            ultima_a TEXT,
            afiliatorios_json TEXT,
            warnings_json TEXT,
            daily_json TEXT,
            trajectory_json TEXT,
            FOREIGN KEY(report_id) REFERENCES gis_monthly_reports(id) ON DELETE CASCADE,
            UNIQUE(report_id, identity_key)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gis_monthly_report_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            person_id INTEGER NOT NULL,
            period_id INTEGER,
            event_type_suggested TEXT,
            event_type_confirmed TEXT,
            fecha_suggested TEXT,
            fecha_confirmed TEXT,
            estado TEXT NOT NULL DEFAULT 'propuesto'
                CHECK (estado IN ('propuesto', 'confirmado', 'descartado', 'incompleto', 'convertido')),
            motivo TEXT,
            movimiento_id TEXT,
            decided_by TEXT,
            decided_at TEXT,
            observaciones TEXT,
            segment_json TEXT,
            FOREIGN KEY(report_id) REFERENCES gis_monthly_reports(id) ON DELETE CASCADE,
            FOREIGN KEY(person_id) REFERENCES gis_monthly_report_persons(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gis_monthly_reports_cliente ON gis_monthly_reports(cliente, anio, mes)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gis_monthly_report_weeks_report ON gis_monthly_report_weeks(report_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gis_monthly_report_persons_report ON gis_monthly_report_persons(report_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gis_monthly_report_events_report ON gis_monthly_report_events(report_id)"
    )
