"""Registro mínimo de actividad para auditoría y dashboard admin (multi-módulo)."""

from __future__ import annotations

import logging
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def app_timezone() -> ZoneInfo:
    name = (os.environ.get("APP_TIMEZONE") or "America/Mexico_City").strip() or "America/Mexico_City"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("America/Mexico_City")


def activity_now_iso() -> str:
    return datetime.now(app_timezone()).strftime("%Y-%m-%d %H:%M:%S")


def ensure_app_activity_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            user_id INTEGER,
            module TEXT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            ref TEXT,
            detail TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_app_activity_created ON app_activity_log (created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_app_activity_user_mod ON app_activity_log (user_id, module, created_at DESC)")


def log_app_activity(
    db_path: str,
    *,
    user_id: int | None,
    module: str,
    action: str,
    status: str,
    ref: str | None = None,
    detail: str | None = None,
) -> None:
    try:
        conn = sqlite3.connect(db_path)
        try:
            ensure_app_activity_schema(conn)
            conn.execute(
                """
                INSERT INTO app_activity_log (created_at, user_id, module, action, status, ref, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (activity_now_iso(), user_id, module, action, status, ref, detail),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("log_app_activity failed module=%s action=%s", module, action)


def list_activity_feed(
    db_path: str,
    *,
    limit: int = 100,
    since: str | None = None,
    until: str | None = None,
    module: str | None = None,
    username: str | None = None,
) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_app_activity_schema(conn)
        clauses: list[str] = []
        params: list[Any] = []
        if since:
            clauses.append("a.created_at >= ?")
            params.append(since)
        if until:
            clauses.append("a.created_at <= ?")
            params.append(until)
        if module and module.strip():
            clauses.append("a.module = ?")
            params.append(module.strip())
        if username and username.strip():
            clauses.append("LOWER(u.username) = LOWER(?)")
            params.append(username.strip())
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        rows = conn.execute(
            f"""
            SELECT a.id, a.created_at, a.module, a.action, a.status, a.ref, a.detail, u.username
            FROM app_activity_log a
            LEFT JOIN users u ON u.id = a.user_id
            {where}
            ORDER BY a.created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _count_since(conn: sqlite3.Connection, table: str, since: str) -> int:
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS c FROM {table} WHERE created_at >= ?",
            (since,),
        ).fetchone()
        return int(row["c"]) if row else 0
    except sqlite3.OperationalError:
        return 0


def _merge_user_counts(conn: sqlite3.Connection, since: str) -> dict[int, int]:
    out: dict[int, int] = defaultdict(int)
    specs: list[tuple[str, str]] = [
        ("format_history", "user_id"),
        ("checkid_query_log", "user_id"),
        ("historial_finiquitos", "user_id"),
        ("historial_liquidaciones", "user_id"),
        ("carrier_curso_export_log", "user_id"),
    ]
    for table, col in specs:
        try:
            for uid, n in conn.execute(
                f"SELECT {col}, COUNT(*) FROM {table} WHERE created_at >= ? GROUP BY {col}",
                (since,),
            ).fetchall():
                out[int(uid)] += int(n)
        except sqlite3.OperationalError:
            continue
    return dict(out)


def build_admin_dashboard(db_path: str) -> dict[str, Any]:
    from services.finiquitos_history import ensure_finiquitos_tables
    from modules.carrier.db import ensure_carrier_tables

    ensure_finiquitos_tables(db_path)
    tz = app_timezone()
    now = datetime.now(tz)
    today_start = now.strftime("%Y-%m-%d 00:00:00")
    since_7 = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    since_30 = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_app_activity_schema(conn)
        ensure_carrier_tables(conn)

        docs_today = (
            _count_since(conn, "format_history", today_start)
            + _count_since(conn, "checkid_query_log", today_start)
            + _count_since(conn, "historial_finiquitos", today_start)
            + _count_since(conn, "historial_liquidaciones", today_start)
            + _count_since(conn, "carrier_curso_export_log", today_start)
        )
        docs_7 = (
            _count_since(conn, "format_history", since_7)
            + _count_since(conn, "checkid_query_log", since_7)
            + _count_since(conn, "historial_finiquitos", since_7)
            + _count_since(conn, "historial_liquidaciones", since_7)
            + _count_since(conn, "carrier_curso_export_log", since_7)
        )
        docs_30 = (
            _count_since(conn, "format_history", since_30)
            + _count_since(conn, "checkid_query_log", since_30)
            + _count_since(conn, "historial_finiquitos", since_30)
            + _count_since(conn, "historial_liquidaciones", since_30)
            + _count_since(conn, "carrier_curso_export_log", since_30)
        )

        merged_7 = _merge_user_counts(conn, since_7)
        active_users_7d = len(merged_7)

        users_total = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])

        vit_total = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM app_activity_log
                WHERE module IN ('vitroflex_memo', 'vitroflex_cr') AND action = 'generar_documento'
                """
            ).fetchone()[0]
        )
        vit_30 = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM app_activity_log
                WHERE created_at >= ? AND module IN ('vitroflex_memo', 'vitroflex_cr')
                AND action = 'generar_documento' AND status = 'ok'
                """,
                (since_30,),
            ).fetchone()[0]
        )

        module_summary = [
            {
                "key": "movimientos_imss",
                "label": "Movimientos IMSS",
                "description": "Constancias generadas (historial)",
                "total": int(conn.execute("SELECT COUNT(*) FROM format_history").fetchone()[0]),
                "last30": _count_since(conn, "format_history", since_30),
            },
            {
                "key": "checkid",
                "label": "CheckID",
                "description": "Consultas registradas",
                "total": int(conn.execute("SELECT COUNT(*) FROM checkid_query_log").fetchone()[0]),
                "last30": _count_since(conn, "checkid_query_log", since_30),
            },
            {
                "key": "finiquitos",
                "label": "Finiquitos",
                "description": "Snapshots en historial",
                "total": _count_since(conn, "historial_finiquitos", "1970-01-01 00:00:00"),
                "last30": _count_since(conn, "historial_finiquitos", since_30),
            },
            {
                "key": "finiquitos_liquidaciones",
                "label": "Finiquitos (liquidaciones)",
                "description": "Registros de liquidación",
                "total": _count_since(conn, "historial_liquidaciones", "1970-01-01 00:00:00"),
                "last30": _count_since(conn, "historial_liquidaciones", since_30),
            },
            {
                "key": "carrier_cursos",
                "label": "Carrier — Cursos",
                "description": "PDF exportados desde expedientes",
                "total": _count_since(conn, "carrier_curso_export_log", "1970-01-01 00:00:00"),
                "last30": _count_since(conn, "carrier_curso_export_log", since_30),
            },
            {
                "key": "vitroflex",
                "label": "Vitroflex (MEMO / CR)",
                "description": "Generaciones PDF/DOCX registradas en actividad",
                "total": vit_total,
                "last30": vit_30,
            },
        ]

        doc_usage_30d = [
            ("movimientos_imss", "Movimientos IMSS", _count_since(conn, "format_history", since_30)),
            ("checkid", "CheckID", _count_since(conn, "checkid_query_log", since_30)),
            ("finiquitos", "Finiquitos", _count_since(conn, "historial_finiquitos", since_30)),
            ("finiquitos_liquidaciones", "Liquidaciones", _count_since(conn, "historial_liquidaciones", since_30)),
            ("carrier_cursos", "Carrier — Cursos", _count_since(conn, "carrier_curso_export_log", since_30)),
            ("vitroflex", "Vitroflex", vit_30),
        ]
        most_used_documents = max(doc_usage_30d, key=lambda x: x[2])

        checkid_fail_7 = 0
        try:
            checkid_fail_7 = int(
                conn.execute(
                    "SELECT COUNT(*) FROM checkid_query_log WHERE created_at >= ? AND ok = 0",
                    (since_7,),
                ).fetchone()[0]
            )
        except sqlite3.OperationalError:
            pass

        activity_fail_7 = int(
            conn.execute(
                "SELECT COUNT(*) FROM app_activity_log WHERE created_at >= ? AND status IN ('error', 'fail')",
                (since_7,),
            ).fetchone()[0]
        )

        mod_usage_rows = conn.execute(
            """
            SELECT module, COUNT(*) AS c
            FROM app_activity_log
            WHERE created_at >= ? AND module NOT IN ('auth', 'admin')
            GROUP BY module
            ORDER BY c DESC
            LIMIT 12
            """,
            (since_30,),
        ).fetchall()

        top_users_rows = conn.execute(
            """
            SELECT u.username, COUNT(*) AS c
            FROM app_activity_log a
            JOIN users u ON u.id = a.user_id
            WHERE a.created_at >= ? AND a.module NOT IN ('auth')
            GROUP BY u.username
            ORDER BY c DESC
            LIMIT 8
            """,
            (since_30,),
        ).fetchall()

        merged_counts = merged_7
        top_legacy: list[tuple[str, int]] = []
        if merged_counts:
            ids = sorted(merged_counts.keys(), key=lambda i: -merged_counts[i])[:8]
            placeholders = ",".join("?" * len(ids))
            names = {
                int(r["id"]): str(r["username"])
                for r in conn.execute(f"SELECT id, username FROM users WHERE id IN ({placeholders})", ids).fetchall()
            }
            top_legacy = [(names.get(i, str(i)), merged_counts[i]) for i in ids]

        recent_failures = conn.execute(
            """
            SELECT a.created_at, a.module, a.action, a.status, a.ref, a.detail, u.username
            FROM app_activity_log a
            LEFT JOIN users u ON u.id = a.user_id
            WHERE a.status IN ('error', 'fail')
            ORDER BY a.created_at DESC
            LIMIT 20
            """
        ).fetchall()

        recent_logins = conn.execute(
            """
            SELECT a.created_at, u.username, a.ref
            FROM app_activity_log a
            LEFT JOIN users u ON u.id = a.user_id
            WHERE a.module = 'auth' AND a.action = 'login' AND a.status = 'ok'
            ORDER BY a.created_at DESC
            LIMIT 25
            """
        ).fetchall()

        activity_by_day_rows = conn.execute(
            """
            SELECT substr(created_at, 1, 10) AS d, COUNT(*) AS c
            FROM app_activity_log
            WHERE created_at >= ?
            GROUP BY substr(created_at, 1, 10)
            ORDER BY d ASC
            """,
            (since_30,),
        ).fetchall()

        most_used_module = None
        if mod_usage_rows:
            most_used_module = {"module": str(mod_usage_rows[0]["module"]), "count": int(mod_usage_rows[0]["c"])}

        most_used_documents_30d = {
            "key": most_used_documents[0],
            "label": most_used_documents[1],
            "count": int(most_used_documents[2]),
        }

        return {
            "range": {"today_start": today_start, "since_7": since_7, "since_30": since_30, "now": activity_now_iso()},
            "kpi": {
                "documents_today": docs_today,
                "documents_7d": docs_7,
                "documents_30d": docs_30,
                "active_users_7d": active_users_7d,
                "users_total": users_total,
                "failures_7d": activity_fail_7,
                "checkid_failures_7d": checkid_fail_7,
                "activity_failures_7d": activity_fail_7,
            },
            "module_summary": module_summary,
            "module_usage_30d": [{"module": str(r["module"]), "count": int(r["c"])} for r in mod_usage_rows],
            "top_users_activity_30d": [{"username": str(r["username"]), "count": int(r["c"])} for r in top_users_rows],
            "top_users_legacy_7d": [{"username": u, "count": c} for u, c in top_legacy],
            "most_used_module": most_used_module,
            "most_used_documents_30d": most_used_documents_30d,
            "recent_failures": [dict(r) for r in recent_failures],
            "recent_logins": [dict(r) for r in recent_logins],
            "activity_by_day": [{"date": str(r["d"]), "count": int(r["c"])} for r in activity_by_day_rows],
        }
    finally:
        conn.close()
