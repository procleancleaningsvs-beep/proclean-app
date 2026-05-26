"""Snapshot local persistente de Headcount para Nóminas (sin OneDrive en GET)."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from modules.nomina.parametros_match import _norm_name
from modules.nomina.vacaciones_util import resolve_status_headcount, sanitize_display_value

_SNAPSHOT_VERSION = 1
_META_ID = 1
_SNAPSHOT_TTL_ENV = "HEADCOUNT_SNAPSHOT_TTL_SECONDS"
_REFRESH_LOCK_TIMEOUT_SEC = 900
_MX = ZoneInfo("America/Mexico_City")
_process_lock = threading.Lock()
_logger = logging.getLogger(__name__)


def _snapshot_ttl_sec() -> int:
    raw = (
        os.environ.get(_SNAPSHOT_TTL_ENV)
        or os.environ.get("HEADCOUNT_CACHE_TTL_SECONDS")
        or "3600"
    ).strip()
    try:
        return max(60, int(raw))
    except ValueError:
        return 3600


def _now_iso() -> str:
    return datetime.now(_MX).strftime("%Y-%m-%d %H:%M:%S")


def _parse_iso(value: str | None) -> datetime | None:
    s = (value or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=_MX)
        except ValueError:
            continue
    return None


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
            snapshot_version INTEGER NOT NULL DEFAULT 1,
            refresh_started_at TEXT,
            refresh_finished_at TEXT
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
    cols = {row[1] for row in conn.execute("PRAGMA table_info(nomina_headcount_snapshot_meta)")}
    for name, typ in (
        ("refresh_started_at", "TEXT"),
        ("refresh_finished_at", "TEXT"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE nomina_headcount_snapshot_meta ADD COLUMN {name} {typ}")


def get_headcount_snapshot_meta(db_path: str) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_headcount_snapshot_tables(conn)
        row = conn.execute("SELECT * FROM nomina_headcount_snapshot_meta WHERE id = 1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _snapshot_row_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM nomina_headcount_snapshot").fetchone()
    return int(row[0] if row else 0)


def headcount_snapshot_available(db_path: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        ensure_headcount_snapshot_tables(conn)
        return _snapshot_row_count(conn) > 0
    finally:
        conn.close()


def is_headcount_snapshot_stale(db_path: str, *, now_iso: str | None = None) -> bool:
    meta = get_headcount_snapshot_meta(db_path)
    if not meta:
        return True
    if not headcount_snapshot_available(db_path):
        return True
    if str(meta.get("status") or "") in {"empty", "error"} and not meta.get("last_refresh_at"):
        return True
    last = _parse_iso(str(meta.get("last_refresh_at") or ""))
    if last is None:
        return True
    ref = _parse_iso(now_iso) if now_iso else datetime.now(_MX)
    if ref is None:
        ref = datetime.now(_MX)
    return ref - last >= timedelta(seconds=_snapshot_ttl_sec())


def is_headcount_snapshot_refreshing(db_path: str, *, now_iso: str | None = None) -> bool:
    meta = get_headcount_snapshot_meta(db_path)
    if not meta or str(meta.get("status") or "") != "refreshing":
        return False
    started = _parse_iso(str(meta.get("refresh_started_at") or ""))
    if started is None:
        return True
    ref = _parse_iso(now_iso) if now_iso else datetime.now(_MX)
    if ref is None:
        ref = datetime.now(_MX)
    if ref - started > timedelta(seconds=_REFRESH_LOCK_TIMEOUT_SEC):
        return False
    return True


def headcount_snapshot_ui_message(db_path: str, *, now_iso: str | None = None) -> str:
    meta = get_headcount_snapshot_meta(db_path) or {}
    has_data = headcount_snapshot_available(db_path)
    last = (meta.get("last_refresh_at") or "").strip()
    activos = int(meta.get("activos_count") or 0)

    if not has_data:
        return (
            "Headcount pendiente de actualización. "
            "Actualiza Headcount para generar la primera copia local."
        )

    base = f"Headcount actualizado: {last}. Activos: {activos}." if last else f"Headcount disponible. Activos: {activos}."

    if is_headcount_snapshot_refreshing(db_path, now_iso=now_iso):
        return f"{base} Hay una actualización en proceso."

    if is_headcount_snapshot_stale(db_path, now_iso=now_iso):
        return f"{base} Hay una actualización pendiente/en proceso."

    if str(meta.get("status") or "") == "error" and meta.get("error_message"):
        return f"{base} La última actualización automática no se completó."

    return base


def headcount_snapshot_user_message(db_path: str) -> str | None:
    if headcount_snapshot_available(db_path):
        return None
    return headcount_snapshot_ui_message(db_path)


def headcount_snapshot_dashboard_message(db_path: str) -> str:
    return headcount_snapshot_ui_message(db_path)


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
        if _snapshot_row_count(conn) == 0:
            return []
        query = "SELECT * FROM nomina_headcount_snapshot"
        if activos_only:
            query += " WHERE activo = 1"
        query += " ORDER BY nombre_completo ASC"
        return [_row_to_headcount_dict(r) for r in conn.execute(query).fetchall()]
    finally:
        conn.close()


def get_headcount_snapshot(db_path: str, *, now_iso: str | None = None) -> dict[str, Any]:
    now = now_iso or _now_iso()
    meta = get_headcount_snapshot_meta(db_path)
    rows = load_headcount_snapshot_rows(db_path)
    stale = is_headcount_snapshot_stale(db_path, now_iso=now)
    refreshing = is_headcount_snapshot_refreshing(db_path, now_iso=now)
    return {
        "rows": rows,
        "meta": meta,
        "has_data": len(rows) > 0,
        "stale": stale,
        "refreshing": refreshing,
        "message": headcount_snapshot_ui_message(db_path, now_iso=now),
    }


def _clear_stale_refresh_lock(conn: sqlite3.Connection, now_iso: str) -> None:
    row = conn.execute(
        "SELECT status, refresh_started_at FROM nomina_headcount_snapshot_meta WHERE id = 1"
    ).fetchone()
    if row is None or str(row[0] or "") != "refreshing":
        return
    started = _parse_iso(str(row[1] or ""))
    ref = _parse_iso(now_iso) or datetime.now(_MX)
    if started is None or ref - started > timedelta(seconds=_REFRESH_LOCK_TIMEOUT_SEC):
        prev_status = "ok" if _snapshot_row_count(conn) > 0 else "empty"
        conn.execute(
            """
            UPDATE nomina_headcount_snapshot_meta SET
                status = ?,
                refresh_started_at = NULL,
                refresh_finished_at = ?
            WHERE id = 1 AND status = 'refreshing'
            """,
            (prev_status, now_iso),
        )


def acquire_headcount_refresh_lock(db_path: str, *, now_iso: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        ensure_headcount_snapshot_tables(conn)
        _clear_stale_refresh_lock(conn, now_iso)
        cur = conn.execute(
            """
            UPDATE nomina_headcount_snapshot_meta SET
                status = 'refreshing',
                refresh_started_at = ?,
                refresh_finished_at = NULL,
                error_message = NULL
            WHERE id = 1 AND status != 'refreshing'
            """,
            (now_iso,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def release_headcount_refresh_lock(db_path: str, *, now_iso: str, final_status: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        ensure_headcount_snapshot_tables(conn)
        has_rows = _snapshot_row_count(conn) > 0
        status = final_status if final_status in {"ok", "error", "empty"} else ("ok" if has_rows else "empty")
        conn.execute(
            """
            UPDATE nomina_headcount_snapshot_meta SET
                status = ?,
                refresh_finished_at = ?,
                refresh_started_at = NULL
            WHERE id = 1
            """,
            (status, now_iso),
        )
        conn.commit()
    finally:
        conn.close()


def _persist_snapshot_rows(
    conn: sqlite3.Connection,
    remote_rows: list[dict[str, Any]],
    *,
    now_iso: str,
) -> tuple[int, int, int]:
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
    return len(remote_rows), activos, warnings


def _mark_refresh_error(conn: sqlite3.Connection, *, now_iso: str, error: str) -> None:
    has_rows = _snapshot_row_count(conn) > 0
    status = "ok" if has_rows else "error"
    conn.execute(
        """
        UPDATE nomina_headcount_snapshot_meta SET
            status = ?,
            error_message = ?,
            refresh_finished_at = ?,
            refresh_started_at = NULL
        WHERE id = 1
        """,
        (status, str(error)[:2000], now_iso),
    )


def refresh_headcount_snapshot(
    db_path: str,
    *,
    now_iso: str | None = None,
    skip_lock_acquire: bool = False,
) -> dict[str, Any]:
    """Descarga Headcount remoto y persiste snapshot local (job/manual; no GET)."""
    from modules.comparativo.headcount_service import actualizar_headcount
    from modules.nomina.headcount_bridge import obtener_headcount_completo

    now = now_iso or _now_iso()
    if not skip_lock_acquire and not acquire_headcount_refresh_lock(db_path, now_iso=now):
        return {"ok": False, "error": "refresh_already_running", "skipped": True}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_headcount_snapshot_tables(conn)
        try:
            actualizar_headcount()
            remote_rows = obtener_headcount_completo()
        except Exception as exc:
            _mark_refresh_error(conn, now_iso=now, error=str(exc))
            conn.commit()
            _logger.warning("[headcount_snapshot] refresh failed: %s", exc)
            return {
                "ok": False,
                "error": str(exc),
                "preserved": _snapshot_row_count(conn) > 0,
            }

        total_rows, activos, warnings = _persist_snapshot_rows(conn, remote_rows, now_iso=now)
        conn.execute(
            """
            INSERT INTO nomina_headcount_snapshot_meta (
                id, last_refresh_at, source, total_rows, activos_count,
                warnings_count, status, error_message, snapshot_version,
                refresh_started_at, refresh_finished_at
            ) VALUES (1, ?, 'onedrive', ?, ?, ?, 'ok', NULL, ?, NULL, ?)
            ON CONFLICT(id) DO UPDATE SET
                last_refresh_at = excluded.last_refresh_at,
                source = excluded.source,
                total_rows = excluded.total_rows,
                activos_count = excluded.activos_count,
                warnings_count = excluded.warnings_count,
                status = 'ok',
                error_message = NULL,
                snapshot_version = excluded.snapshot_version,
                refresh_started_at = NULL,
                refresh_finished_at = excluded.refresh_finished_at
            """,
            (now, total_rows, activos, warnings, _SNAPSHOT_VERSION, now),
        )
        conn.commit()
        _logger.info(
            "[headcount_snapshot] refresh ok rows=%s activos=%s",
            total_rows,
            activos,
        )
        return {
            "ok": True,
            "total_rows": total_rows,
            "activos_count": activos,
            "warnings_count": warnings,
            "last_refresh_at": now,
        }
    finally:
        conn.close()


def _background_refresh(db_path: str, now_iso: str) -> None:
    try:
        refresh_headcount_snapshot(db_path, now_iso=now_iso, skip_lock_acquire=True)
    except Exception as exc:  # pragma: no cover
        _logger.exception("[headcount_snapshot] background refresh crashed: %s", exc)
        release_headcount_refresh_lock(db_path, now_iso=_now_iso(), final_status="error")


def trigger_headcount_refresh_if_needed(db_path: str, *, now_iso: str | None = None) -> dict[str, Any]:
    """Inicia refresh en background si el snapshot está vencido (no bloquea GET)."""
    now = now_iso or _now_iso()
    if not is_headcount_snapshot_stale(db_path, now_iso=now):
        return {"triggered": False, "reason": "fresh"}
    if is_headcount_snapshot_refreshing(db_path, now_iso=now):
        return {"triggered": False, "reason": "already_refreshing"}

    with _process_lock:
        if is_headcount_snapshot_refreshing(db_path, now_iso=now):
            return {"triggered": False, "reason": "already_refreshing"}
        if not acquire_headcount_refresh_lock(db_path, now_iso=now):
            return {"triggered": False, "reason": "locked"}

        thread = threading.Thread(
            target=_background_refresh,
            args=(db_path, now),
            name="headcount_snapshot_refresh",
            daemon=True,
        )
        thread.start()
        _logger.info("[headcount_snapshot] background refresh triggered")
        return {"triggered": True, "reason": "stale"}
