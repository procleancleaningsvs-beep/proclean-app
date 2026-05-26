"""Snapshot local persistente de Headcount para Nóminas (sin OneDrive en GET)."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from modules.nomina.parametros_match import _norm_name
from modules.nomina.vacaciones_util import resolve_status_headcount, sanitize_display_value

_SNAPSHOT_VERSION = 1
_META_ID = 1


def ensure_headcount_snapshot_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_headcount_snapshot_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_refresh_at TEXT,
            source TEXT,
            total_rows INTEGER NOT NULL DEFAULT 0,
            activos_count INTEGER NOT NULL DEFAULT 0,
            warnings_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'empty',
            error_message TEXT,
            snapshot_version INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_headcount_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nss TEXT,
            nombre_completo TEXT,
            nombre_normalizado TEXT,
            cliente TEXT,
            planta TEXT,
            puesto TEXT,
            fecha_ingreso TEXT,
            status TEXT,
            status_imss TEXT,
            activo INTEGER NOT NULL DEFAULT 0,
            sueldo REAL,
            banco TEXT,
            cuenta TEXT,
            raw_json TEXT,
            snapshot_version INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_hc_snapshot_nss ON nomina_headcount_snapshot(nss)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_hc_snapshot_activo ON nomina_headcount_snapshot(activo)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_hc_snapshot_cliente ON nomina_headcount_snapshot(cliente)"
    )
    conn.execute(
        "INSERT OR IGNORE INTO nomina_headcount_snapshot_meta (id, status) VALUES (1, 'empty')"
    )


def get_headcount_snapshot_meta(db_path: str) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_headcount_snapshot_tables(conn)
        row = conn.execute("SELECT * FROM nomina_headcount_snapshot_meta WHERE id = 1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def headcount_snapshot_available(db_path: str) -> bool:
    meta = get_headcount_snapshot_meta(db_path)
    if not meta or str(meta.get("status") or "") != "ok":
        return False
    return int(meta.get("activos_count") or 0) > 0 or int(meta.get("total_rows") or 0) > 0


def headcount_snapshot_user_message(db_path: str) -> str | None:
    meta = get_headcount_snapshot_meta(db_path)
    if meta and str(meta.get("status") or "") == "ok" and int(meta.get("total_rows") or 0) > 0:
        return None
    if meta and str(meta.get("status") or "") == "error":
        err = (meta.get("error_message") or "error desconocido").strip()
        return (
            "Headcount no actualizado. El último intento de carga falló. "
            f"Detalle: {err}. Use el botón «Actualizar Headcount»."
        )
    return "Headcount no actualizado. Da clic en «Actualizar Headcount» para cargar la base vigente."


def headcount_snapshot_dashboard_message(db_path: str) -> str:
    meta = get_headcount_snapshot_meta(db_path)
    if meta and str(meta.get("status") or "") == "ok" and meta.get("last_refresh_at"):
        activos = int(meta.get("activos_count") or 0)
        total = int(meta.get("total_rows") or 0)
        return (
            f"Headcount actualizado al: {meta['last_refresh_at']}. "
            f"Copia local: {activos} activos de {total} registros."
        )
    return (
        "Headcount no actualizado en esta sesión. "
        "Para ver empleados activos y KPIs completos, actualiza Headcount con el botón correspondiente."
    )


def _row_to_headcount_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "nombre_completo": row["nombre_completo"],
        "cliente": row["cliente"],
        "patron": row["planta"],
        "fecha_ingreso": row["fecha_ingreso"],
        "sueldo_diario": row["sueldo"],
        "puesto": row["puesto"],
        "nss": row["nss"],
        "status_operacion": row["status"] or "DESCONOCIDO",
        "status_imss": row["status_imss"] or "DESCONOCIDO",
    }


def load_headcount_snapshot_rows(db_path: str, *, activos_only: bool = False) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_headcount_snapshot_tables(conn)
        meta = conn.execute("SELECT status FROM nomina_headcount_snapshot_meta WHERE id = 1").fetchone()
        if meta is None or str(meta["status"] or "") != "ok":
            return []
        query = "SELECT * FROM nomina_headcount_snapshot"
        if activos_only:
            query += " WHERE activo = 1"
        query += " ORDER BY nombre_completo ASC"
        return [_row_to_headcount_dict(r) for r in conn.execute(query).fetchall()]
    finally:
        conn.close()


def refresh_headcount_snapshot(db_path: str, *, now_iso: str) -> dict[str, Any]:
    """Descarga Headcount remoto y persiste snapshot local (solo acción explícita)."""
    from modules.comparativo.headcount_service import actualizar_headcount
    from modules.nomina.headcount_bridge import obtener_headcount_completo

    actualizar_headcount()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_headcount_snapshot_tables(conn)
        try:
            remote_rows = obtener_headcount_completo()
        except Exception as exc:
            conn.execute(
                """
                UPDATE nomina_headcount_snapshot_meta SET
                    status = 'error',
                    error_message = ?,
                    last_refresh_at = ?
                WHERE id = 1
                """,
                (str(exc)[:2000], now_iso),
            )
            conn.commit()
            return {
                "ok": False,
                "error": str(exc),
                "total_rows": 0,
                "activos_count": 0,
            }

        conn.execute("DELETE FROM nomina_headcount_snapshot")
        activos = 0
        warnings = 0
        for item in remote_rows:
            hc_dict = dict(item)
            activo = 1 if resolve_status_headcount(hc_dict) == "ACTIVO" else 0
            if activo:
                activos += 1
            sal = hc_dict.get("sueldo_diario")
            try:
                sueldo = float(sal) if sal not in (None, "") else None
            except (TypeError, ValueError):
                sueldo = None
                warnings += 1
            conn.execute(
                """
                INSERT INTO nomina_headcount_snapshot (
                    nss, nombre_completo, nombre_normalizado, cliente, planta, puesto,
                    fecha_ingreso, status, status_imss, activo, sueldo, banco, cuenta,
                    raw_json, snapshot_version, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sanitize_display_value(hc_dict.get("nss")),
                    sanitize_display_value(hc_dict.get("nombre_completo")),
                    _norm_name(hc_dict.get("nombre_completo")),
                    sanitize_display_value(hc_dict.get("cliente")),
                    sanitize_display_value(hc_dict.get("patron")),
                    sanitize_display_value(hc_dict.get("puesto")),
                    sanitize_display_value(hc_dict.get("fecha_ingreso")),
                    sanitize_display_value(hc_dict.get("status_operacion")),
                    sanitize_display_value(hc_dict.get("status_imss")),
                    activo,
                    sueldo,
                    None,
                    None,
                    json.dumps(hc_dict, ensure_ascii=False, default=str),
                    _SNAPSHOT_VERSION,
                    now_iso,
                ),
            )
        conn.execute(
            """
            INSERT INTO nomina_headcount_snapshot_meta (
                id, last_refresh_at, source, total_rows, activos_count,
                warnings_count, status, error_message, snapshot_version
            ) VALUES (1, ?, 'onedrive', ?, ?, ?, 'ok', NULL, ?)
            ON CONFLICT(id) DO UPDATE SET
                last_refresh_at = excluded.last_refresh_at,
                source = excluded.source,
                total_rows = excluded.total_rows,
                activos_count = excluded.activos_count,
                warnings_count = excluded.warnings_count,
                status = excluded.status,
                error_message = NULL,
                snapshot_version = excluded.snapshot_version
            """,
            (now_iso, len(remote_rows), activos, warnings, _SNAPSHOT_VERSION),
        )
        conn.commit()
        return {
            "ok": True,
            "total_rows": len(remote_rows),
            "activos_count": activos,
            "warnings_count": warnings,
            "last_refresh_at": now_iso,
        }
    finally:
        conn.close()
