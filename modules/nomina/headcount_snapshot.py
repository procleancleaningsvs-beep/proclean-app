"""Snapshot local persistente de Headcount para Nóminas (sin OneDrive en GET)."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from modules.nomina.parametros_match import _norm_name
from modules.nomina.vacaciones_util import resolve_status_headcount, sanitize_display_value
from services.perf_logging import perf_headcount_log

_SNAPSHOT_VERSION = 1
_META_ID = 1
_SNAPSHOT_TTL_ENV = "HEADCOUNT_SNAPSHOT_TTL_SECONDS"
_REFRESH_LOCK_TIMEOUT_SEC = 900
_SQLITE_TIMEOUT_SEC = 30
_MX = ZoneInfo("America/Mexico_City")
_wal_init_lock = threading.Lock()
_wal_ready: set[str] = set()
_logger = logging.getLogger(__name__)

_INSERT_SNAPSHOT_SQL = """
    INSERT INTO {table} (
        nss, nombre_completo, nombre_normalizado, cliente, planta, puesto,
        fecha_ingreso, status, status_imss, activo, sueldo, banco, cuenta,
        raw_json, snapshot_version, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


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


def _ensure_wal(db_path: str) -> None:
    if db_path in _wal_ready or not os.path.exists(db_path):
        return
    with _wal_init_lock:
        if db_path in _wal_ready:
            return
        try:
            conn = sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT_SEC)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=30000")
                conn.commit()
            finally:
                conn.close()
            _wal_ready.add(db_path)
        except sqlite3.Error as exc:
            _logger.debug("headcount snapshot WAL init skipped: %s", exc)


def get_snapshot_conn(db_path: str, *, readonly: bool = False) -> sqlite3.Connection:
    """Conexión SQLite lock-safe para snapshot (preferir readonly en GET)."""
    _ensure_wal(db_path)
    if readonly and os.path.exists(db_path):
        try:
            conn = sqlite3.connect(
                f"file:{os.path.abspath(db_path)}?mode=ro",
                uri=True,
                timeout=_SQLITE_TIMEOUT_SEC,
            )
        except sqlite3.Error:
            conn = sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT_SEC)
    else:
        conn = sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT_SEC)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _snapshot_tables_exist(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='nomina_headcount_snapshot_meta' LIMIT 1"
    ).fetchone()
    return row is not None


def ensure_headcount_snapshot_tables(conn: sqlite3.Connection) -> None:
    """DDL de snapshot — solo arranque/migración o inicio de refresh (nunca en GET)."""
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
            refresh_finished_at TEXT,
            total_rows_source INTEGER NOT NULL DEFAULT 0,
            skipped_empty_rows INTEGER NOT NULL DEFAULT 0,
            parse_guardrail_triggered INTEGER NOT NULL DEFAULT 0
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
        """
        CREATE TABLE IF NOT EXISTS nomina_headcount_snapshot_staging (
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
        ("total_rows_source", "INTEGER NOT NULL DEFAULT 0"),
        ("skipped_empty_rows", "INTEGER NOT NULL DEFAULT 0"),
        ("parse_guardrail_triggered", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE nomina_headcount_snapshot_meta ADD COLUMN {name} {typ}")


def get_headcount_snapshot_meta(db_path: str) -> dict[str, Any] | None:
    if not os.path.exists(db_path):
        return None
    try:
        conn = get_snapshot_conn(db_path, readonly=True)
        try:
            if not _snapshot_tables_exist(conn):
                return None
            row = conn.execute(
                "SELECT * FROM nomina_headcount_snapshot_meta WHERE id = 1"
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            perf_headcount_log("snapshot_read_locked", fallback="meta")
        else:
            _logger.warning("headcount snapshot meta read failed: %s", exc)
        return None


def get_headcount_snapshot_meta_fast(db_path: str) -> dict[str, Any]:
    """Solo metadata — para dashboard (sin cargar empleados)."""
    meta = get_headcount_snapshot_meta(db_path) or {}
    if meta:
        saved = int(meta.get("total_rows") or 0)
        activos = int(meta.get("activos_count") or 0)
        perf_headcount_log("meta_read_ok", activos=activos, total_saved=saved)
    return meta


def _snapshot_row_count(conn: sqlite3.Connection, *, table: str = "nomina_headcount_snapshot") -> int:
    if not _snapshot_tables_exist(conn):
        return 0
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row[0] if row else 0)


def _snapshot_saved_count(db_path: str) -> int:
    if not os.path.exists(db_path):
        return 0
    try:
        conn = get_snapshot_conn(db_path, readonly=True)
        try:
            return _snapshot_row_count(conn)
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return 0


def headcount_snapshot_available(db_path: str) -> bool:
    return _snapshot_saved_count(db_path) > 0


def is_headcount_refresh_running(db_path: str, *, now_iso: str | None = None) -> bool:
    return is_headcount_snapshot_refreshing(db_path, now_iso=now_iso)


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


def _build_ui_message(
    *,
    has_data: bool,
    meta: dict[str, Any] | None,
    refreshing: bool,
    read_locked: bool,
    stale: bool,
) -> str:
    meta = meta or {}
    last = (meta.get("last_refresh_at") or "").strip()
    activos = int(meta.get("activos_count") or 0)

    if refreshing or read_locked:
        if has_data:
            return "Headcount se está actualizando. Se muestran los últimos datos disponibles."
        return "Headcount se está actualizando. Intenta de nuevo en unos segundos."

    if not has_data:
        return (
            "Headcount pendiente de actualización. "
            "La página está en modo limitado hasta generar la primera copia local."
        )

    base = (
        f"Headcount actualizado: {last}. Activos: {activos}."
        if last
        else f"Headcount disponible. Activos: {activos}."
    )
    if stale:
        return f"{base} Puedes actualizarlo para traer la base más reciente."
    if str(meta.get("status") or "") == "error" and meta.get("error_message"):
        return "No se pudo actualizar Headcount. Se conserva la última copia válida."
    return base


def headcount_snapshot_ui_message(db_path: str, *, now_iso: str | None = None) -> str:
    meta = get_headcount_snapshot_meta(db_path)
    has_data = _snapshot_saved_count(db_path) > 0
    now = now_iso or _now_iso()
    return _build_ui_message(
        has_data=has_data,
        meta=meta,
        refreshing=is_headcount_snapshot_refreshing(db_path, now_iso=now),
        read_locked=False,
        stale=is_headcount_snapshot_stale(db_path, now_iso=now) if has_data else True,
    )


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
    if not os.path.exists(db_path):
        return []
    try:
        conn = get_snapshot_conn(db_path, readonly=True)
        try:
            if not _snapshot_tables_exist(conn) or _snapshot_row_count(conn) == 0:
                return []
            query = "SELECT * FROM nomina_headcount_snapshot"
            if activos_only:
                query += " WHERE activo = 1"
            query += " ORDER BY nombre_completo ASC"
            return [_row_to_headcount_dict(r) for r in conn.execute(query).fetchall()]
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            perf_headcount_log("snapshot_read_locked", fallback="rows")
        else:
            _logger.warning("headcount snapshot rows read failed: %s", exc)
        return []


def get_headcount_snapshot(db_path: str, *, now_iso: str | None = None) -> dict[str, Any]:
    now = now_iso or _now_iso()
    try:
        return _get_headcount_snapshot_inner(db_path, now_iso=now)
    except sqlite3.OperationalError as exc:
        perf_headcount_log("snapshot_read_locked", fallback="limited")
        _logger.warning("headcount snapshot read fallback: %s", exc)
        rows: list[dict[str, Any]] = []
        try:
            rows = load_headcount_snapshot_rows(db_path)
        except sqlite3.OperationalError:
            rows = []
        has_data = len(rows) > 0
        msg = (
            "Headcount se está actualizando. Se muestran los últimos datos disponibles."
            if has_data
            else "Headcount se está actualizando. Intenta de nuevo en unos segundos."
        )
        return {
            "rows": rows,
            "meta": None,
            "has_data": has_data,
            "snapshot_exists": has_data,
            "stale": True,
            "refreshing": True,
            "read_locked": True,
            "status": "locked",
            "message": msg,
        }


def _get_headcount_snapshot_inner(db_path: str, *, now_iso: str) -> dict[str, Any]:
    read_locked = False
    meta = get_headcount_snapshot_meta(db_path)
    rows = load_headcount_snapshot_rows(db_path)
    if meta is None and os.path.exists(db_path):
        try:
            conn = get_snapshot_conn(db_path, readonly=True)
            try:
                if _snapshot_tables_exist(conn) and _snapshot_row_count(conn) == 0:
                    pass
            finally:
                conn.close()
        except sqlite3.OperationalError:
            read_locked = True

    has_data = len(rows) > 0
    snapshot_exists = meta is not None or has_data
    refreshing = is_headcount_snapshot_refreshing(db_path, now_iso=now_iso)
    if meta and str(meta.get("status") or "") == "refreshing":
        refreshing = True
    stale = is_headcount_snapshot_stale(db_path, now_iso=now_iso) if snapshot_exists else True

    if not snapshot_exists:
        status = "missing"
    elif read_locked or refreshing:
        status = "refreshing" if refreshing else "locked"
    elif has_data:
        status = "ok"
    else:
        status = "limited"

    message = _build_ui_message(
        has_data=has_data,
        meta=meta,
        refreshing=refreshing,
        read_locked=read_locked,
        stale=stale and not refreshing and not read_locked,
    )
    if read_locked and has_data:
        perf_headcount_log("snapshot_read_ok", fallback="partial", rows=_snapshot_saved_count(db_path))
    elif has_data and not read_locked:
        perf_headcount_log("snapshot_read_ok", rows=_snapshot_saved_count(db_path))

    return {
        "rows": rows,
        "meta": meta,
        "has_data": has_data,
        "snapshot_exists": snapshot_exists,
        "stale": stale,
        "refreshing": refreshing,
        "read_locked": read_locked,
        "status": status,
        "message": message,
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
    try:
        conn = get_snapshot_conn(db_path, readonly=False)
        try:
            ensure_headcount_snapshot_tables(conn)
            conn.commit()
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
    except sqlite3.OperationalError as exc:
        perf_headcount_log("refresh_skipped", reason="lock_active", detail=str(exc)[:80])
        return False


def release_headcount_refresh_lock(db_path: str, *, now_iso: str, final_status: str) -> None:
    try:
        conn = get_snapshot_conn(db_path, readonly=False)
        try:
            if not _snapshot_tables_exist(conn):
                return
            has_rows = _snapshot_row_count(conn) > 0
            status = (
                final_status
                if final_status in {"ok", "error", "empty"}
                else ("ok" if has_rows else "empty")
            )
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
    except sqlite3.OperationalError as exc:
        _logger.warning("headcount refresh lock release failed: %s", exc)


def _prepare_snapshot_tuples(
    remote_rows: list[dict[str, Any]],
    *,
    now_iso: str,
) -> tuple[list[tuple[Any, ...]], int, int, int]:
    tuples: list[tuple[Any, ...]] = []
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
        tuples.append(
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
            )
        )
    return tuples, len(remote_rows), activos, warnings


def _atomic_write_snapshot(
    db_path: str,
    prepared: list[tuple[Any, ...]],
    *,
    now_iso: str,
    total_rows: int,
    activos: int,
    warnings: int,
    total_rows_source: int = 0,
    skipped_empty_rows: int = 0,
    parse_guardrail_triggered: int = 0,
) -> None:
    conn = get_snapshot_conn(db_path, readonly=False)
    try:
        ensure_headcount_snapshot_tables(conn)
        conn.commit()
        insert_sql = _INSERT_SNAPSHOT_SQL.format(table="nomina_headcount_snapshot_staging")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM nomina_headcount_snapshot_staging")
        if prepared:
            conn.executemany(insert_sql, prepared)
        conn.execute("DELETE FROM nomina_headcount_snapshot")
        conn.execute(
            """
            INSERT INTO nomina_headcount_snapshot (
                nss, nombre_completo, nombre_normalizado, cliente, planta, puesto,
                fecha_ingreso, status, status_imss, activo, sueldo, banco, cuenta,
                raw_json, snapshot_version, updated_at
            )
            SELECT
                nss, nombre_completo, nombre_normalizado, cliente, planta, puesto,
                fecha_ingreso, status, status_imss, activo, sueldo, banco, cuenta,
                raw_json, snapshot_version, updated_at
            FROM nomina_headcount_snapshot_staging
            """
        )
        conn.execute(
            """
            INSERT INTO nomina_headcount_snapshot_meta (
                id, last_refresh_at, source, total_rows, activos_count,
                warnings_count, status, error_message, snapshot_version,
                refresh_started_at, refresh_finished_at,
                total_rows_source, skipped_empty_rows, parse_guardrail_triggered
            ) VALUES (1, ?, 'onedrive', ?, ?, ?, 'ok', NULL, ?, NULL, ?, ?, ?, ?)
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
                refresh_finished_at = excluded.refresh_finished_at,
                total_rows_source = excluded.total_rows_source,
                skipped_empty_rows = excluded.skipped_empty_rows,
                parse_guardrail_triggered = excluded.parse_guardrail_triggered
            """,
            (
                now_iso,
                total_rows,
                activos,
                warnings,
                _SNAPSHOT_VERSION,
                now_iso,
                total_rows_source,
                skipped_empty_rows,
                parse_guardrail_triggered,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _write_refresh_error(db_path: str, *, now_iso: str, error: str) -> bool:
    try:
        conn = get_snapshot_conn(db_path, readonly=False)
        try:
            if not _snapshot_tables_exist(conn):
                ensure_headcount_snapshot_tables(conn)
                conn.commit()
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
            conn.commit()
            return has_rows
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        _logger.warning("headcount refresh error metadata write failed: %s", exc)
        return headcount_snapshot_available(db_path)


def refresh_headcount_snapshot(
    db_path: str,
    *,
    now_iso: str | None = None,
    skip_lock_acquire: bool = False,
) -> dict[str, Any]:
    """Descarga Headcount remoto y persiste snapshot local (job/manual/CLI; no GET)."""
    from modules.comparativo.headcount_service import actualizar_headcount
    from modules.nomina.headcount_bridge import fetch_and_parse_headcount

    now = now_iso or _now_iso()
    if not skip_lock_acquire and not acquire_headcount_refresh_lock(db_path, now_iso=now):
        perf_headcount_log("refresh_skipped", reason="lock_active")
        return {"ok": False, "error": "refresh_already_running", "skipped": True}

    perf_headcount_log("refresh_started")
    try:
        actualizar_headcount()
        parse_result = fetch_and_parse_headcount()
    except Exception as exc:
        preserved = _write_refresh_error(db_path, now_iso=now, error=str(exc))
        perf_headcount_log("refresh_failed", error=str(exc)[:120])
        _logger.warning("[headcount_snapshot] refresh failed: %s", exc)
        return {"ok": False, "error": str(exc), "preserved": preserved}

    if parse_result.guardrail_triggered:
        err = parse_result.guardrail_reason or "Headcount inválido."
        preserved = _write_refresh_error(db_path, now_iso=now, error=err)
        perf_headcount_log(
            "refresh_aborted",
            reason="row_explosion",
            source_rows=parse_result.source_rows_scanned,
        )
        return {"ok": False, "error": err, "preserved": preserved, "guardrail": True}

    remote_rows = parse_result.rows
    perf_headcount_log(
        "parse_finished",
        source_rows=parse_result.source_rows_scanned,
        saved_rows=parse_result.saved_rows,
        activos=0,
        skipped_empty=parse_result.skipped_empty_rows,
    )
    prepared, total_rows, activos, warnings = _prepare_snapshot_tuples(remote_rows, now_iso=now)
    perf_headcount_log("parse_finished", saved_rows=total_rows, activos=activos)

    perf_headcount_log("refresh_db_write_started", rows=total_rows)
    t0 = time.perf_counter()
    try:
        _atomic_write_snapshot(
            db_path,
            prepared,
            now_iso=now,
            total_rows=total_rows,
            activos=activos,
            warnings=warnings,
            total_rows_source=parse_result.source_rows_scanned,
            skipped_empty_rows=parse_result.skipped_empty_rows,
            parse_guardrail_triggered=0,
        )
    except Exception as exc:
        preserved = _write_refresh_error(db_path, now_iso=now, error=str(exc))
        perf_headcount_log("refresh_failed", error=str(exc)[:120])
        return {"ok": False, "error": str(exc), "preserved": preserved}

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    perf_headcount_log("refresh_db_write_finished", duration_ms=elapsed_ms, rows=total_rows)
    perf_headcount_log(
        "refresh_finished",
        status="ok",
        saved_rows=total_rows,
        activos=activos,
    )
    _logger.info("[headcount_snapshot] refresh ok rows=%s activos=%s", total_rows, activos)
    return {
        "ok": True,
        "total_rows": total_rows,
        "total_rows_source": parse_result.source_rows_scanned,
        "skipped_empty_rows": parse_result.skipped_empty_rows,
        "activos_count": activos,
        "warnings_count": warnings,
        "last_refresh_at": now,
    }


def trigger_headcount_refresh_if_needed(db_path: str, *, now_iso: str | None = None) -> dict[str, Any]:
    """No inicia refresh en web worker — usar cron/CLI/botón manual."""
    now = now_iso or _now_iso()
    if is_headcount_snapshot_stale(db_path, now_iso=now):
        perf_headcount_log("refresh_skipped", reason="web_worker_disabled")
        return {"triggered": False, "reason": "web_worker_disabled"}
    return {"triggered": False, "reason": "fresh"}
