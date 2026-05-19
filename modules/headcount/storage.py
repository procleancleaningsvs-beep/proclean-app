"""Auditorías SUA en SQLite vía ``app.config['DATABASE']`` (persistente en Railway: /app/data/instance)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def ensure_headcount_tables(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS headcount_sua_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                fecha_corte_sua TEXT NOT NULL,
                archivo_original_nombre TEXT NOT NULL,
                registro_patronal_sua TEXT,
                razon_social_sua TEXT,
                rfc_patronal_sua TEXT,
                periodo_proceso_sua TEXT,
                fecha_proceso_sua TEXT,
                total_cotizantes INTEGER,
                trabajadores_extraidos INTEGER,
                total_matches INTEGER,
                total_sin_match INTEGER,
                total_warnings INTEGER,
                resumen_json TEXT NOT NULL,
                detalle_json TEXT NOT NULL,
                hash_archivo TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_headcount_sua_fecha_corte ON headcount_sua_audits(fecha_corte_sua)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_headcount_sua_registro ON headcount_sua_audits(registro_patronal_sua)"
        )
        conn.commit()
    finally:
        conn.close()


def find_duplicate_audit(
    db_path: str,
    *,
    fecha_corte_sua: str,
    registro_patronal: str,
) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT audit_id, created_at, archivo_original_nombre, user_id
            FROM headcount_sua_audits
            WHERE fecha_corte_sua = ? AND registro_patronal_sua = ?
            ORDER BY created_at DESC
            LIMIT 5
            """,
            (fecha_corte_sua, registro_patronal),
        ).fetchall()
    finally:
        conn.close()


def insert_sua_audit(
    db_path: str,
    *,
    audit_id: str,
    user_id: int,
    created_at: str,
    fecha_corte_sua: str,
    archivo_original_nombre: str,
    registro_patronal_sua: str,
    razon_social_sua: str,
    rfc_patronal_sua: str,
    periodo_proceso_sua: str,
    fecha_proceso_sua: str,
    total_cotizantes: int,
    trabajadores_extraidos: int,
    total_matches: int,
    total_sin_match: int,
    total_warnings: int,
    resumen: dict[str, Any],
    payload: dict[str, Any],
    hash_archivo: str,
) -> str:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO headcount_sua_audits (
                audit_id, user_id, created_at, fecha_corte_sua, archivo_original_nombre,
                registro_patronal_sua, razon_social_sua, rfc_patronal_sua, periodo_proceso_sua,
                fecha_proceso_sua, total_cotizantes, trabajadores_extraidos, total_matches,
                total_sin_match, total_warnings, resumen_json, detalle_json, hash_archivo
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                user_id,
                created_at,
                fecha_corte_sua,
                archivo_original_nombre,
                registro_patronal_sua,
                razon_social_sua,
                rfc_patronal_sua,
                periodo_proceso_sua,
                fecha_proceso_sua,
                total_cotizantes,
                trabajadores_extraidos,
                total_matches,
                total_sin_match,
                total_warnings,
                json.dumps(resumen, ensure_ascii=False, default=str),
                json.dumps(payload, ensure_ascii=False, default=str),
                hash_archivo,
            ),
        )
        conn.commit()
        return audit_id
    finally:
        conn.close()


def get_sua_audit(db_path: str, audit_id: str) -> sqlite3.Row | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT a.*, u.username
            FROM headcount_sua_audits a
            JOIN users u ON u.id = a.user_id
            WHERE a.audit_id = ?
            """,
            (audit_id,),
        ).fetchone()
    finally:
        conn.close()


def list_sua_audits(
    db_path: str,
    *,
    fecha_corte: str | None = None,
    limit: int = 200,
) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if fecha_corte:
            return conn.execute(
                """
                SELECT a.*, u.username
                FROM headcount_sua_audits a
                JOIN users u ON u.id = a.user_id
                WHERE a.fecha_corte_sua = ?
                ORDER BY a.created_at DESC
                LIMIT ?
                """,
                (fecha_corte, limit),
            ).fetchall()
        return conn.execute(
            """
            SELECT a.*, u.username
            FROM headcount_sua_audits a
            JOIN users u ON u.id = a.user_id
            ORDER BY a.fecha_corte_sua DESC, a.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()


def delete_sua_audit(db_path: str, audit_id: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("DELETE FROM headcount_sua_audits WHERE audit_id = ?", (audit_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
