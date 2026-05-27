"""API unificada de Headcount snapshot para toda la app."""
from __future__ import annotations

import json
import os
from typing import Any

from modules.comparativo.headcount_service import _format_fecha, _normalize_name, _normalize_spaces
from modules.headcount.matching import normalize_text
from modules.nomina.headcount_snapshot import (
    get_headcount_snapshot,
    get_headcount_snapshot_meta,
    get_headcount_snapshot_meta_fast,
    headcount_snapshot_available,
    headcount_snapshot_ui_message,
    is_headcount_snapshot_refreshing,
    is_headcount_snapshot_stale,
    load_headcount_snapshot_rows,
    refresh_headcount_snapshot,
)
from services.perf_logging import perf_headcount_log

# Re-export refresh for jobs/endpoints
__all__ = [
    "get_headcount_snapshot_meta_fast",
    "get_headcount_rows_from_snapshot",
    "get_headcount_active_rows_from_snapshot",
    "search_headcount_snapshot",
    "refresh_headcount_snapshot",
    "get_last_valid_headcount_snapshot",
    "get_headcount_clientes_from_snapshot",
    "resolve_headcount_db_path",
    "headcount_snapshot_page_context",
]


def resolve_headcount_db_path(db_path: str | None = None) -> str:
    if db_path:
        return db_path
    try:
        from flask import current_app, has_request_context

        if has_request_context():
            return str(current_app.config["DATABASE"])
    except RuntimeError:
        pass
    from app import DB_PATH

    return str(DB_PATH)


def get_last_valid_headcount_snapshot(db_path: str, *, now_iso: str | None = None) -> dict[str, Any]:
    return get_headcount_snapshot(db_path, now_iso=now_iso)


def headcount_snapshot_page_context(db_path: str, *, now_iso: str | None = None) -> dict[str, Any]:
    snap = get_last_valid_headcount_snapshot(db_path, now_iso=now_iso)
    meta = snap.get("meta") or {}
    return {
        "snapshot": snap,
        "meta": meta,
        "message": snap.get("message") or headcount_snapshot_ui_message(db_path, now_iso=now_iso),
        "has_data": bool(snap.get("has_data")),
        "snapshot_exists": bool(snap.get("snapshot_exists")),
        "stale": bool(snap.get("stale")),
        "refreshing": bool(snap.get("refreshing")),
        "status": snap.get("status") or "missing",
        "activos_count": int(meta.get("activos_count") or 0),
        "total_rows": int(meta.get("total_rows") or 0),
        "last_refresh_at": meta.get("last_refresh_at"),
    }


def _sanitize_field(value: Any) -> str:
    if value is None:
        return ""
    s = _normalize_spaces(str(value).strip())
    if s.upper() in {"", "NAN", "NONE", "NULL", "NAT", "<NA>"}:
        return ""
    return s


def _snapshot_row_to_record(row: dict[str, Any], seq: int) -> dict[str, Any]:
    """Convierte fila snapshot (columnas + raw_json) al formato del módulo Headcount."""
    raw: dict[str, Any] = {}
    raw_json = row.get("raw_json")
    if isinstance(raw_json, str) and raw_json.strip():
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict):
                raw = parsed
        except json.JSONDecodeError:
            pass
    elif isinstance(raw_json, dict):
        raw = raw_json

    status_op = _sanitize_field(raw.get("status_operacion") or row.get("status_operacion") or row.get("status"))
    status_imss = _sanitize_field(raw.get("status_imss") or row.get("status_imss")) or "SIN ESTATUS"
    if not status_op:
        status_op = "SIN ESTATUS"
    nombre_completo = _normalize_name(
        raw.get("nombre_completo") or row.get("nombre_completo") or ""
    )
    sueldo_diario = raw.get("sueldo_diario")
    if sueldo_diario is None:
        sueldo_diario = row.get("sueldo_diario")
    sueldo_semanal = raw.get("sueldo_semanal")

    return {
        "headcount_id": f"hc_{seq}",
        "cliente": _sanitize_field(raw.get("cliente") or row.get("cliente")),
        "ubicacion": _sanitize_field(
            raw.get("ubicacion") or raw.get("planta") or row.get("ubicacion") or row.get("planta")
        ),
        "puesto": _sanitize_field(raw.get("puesto") or row.get("puesto")),
        "sueldo_diario": sueldo_diario if sueldo_diario not in ("", None) else None,
        "sueldo_semanal": sueldo_semanal if sueldo_semanal not in ("", None) else None,
        "patron": _sanitize_field(raw.get("patron") or row.get("patron")),
        "fecha_ingreso": _format_fecha(raw.get("fecha_ingreso") or row.get("fecha_ingreso")),
        "status_operacion": status_op,
        "status_imss": status_imss,
        "rfc_homoclave": _sanitize_field(raw.get("rfc_homoclave")),
        "cp_fiscal": _sanitize_field(raw.get("cp_fiscal")),
        "curp": _sanitize_field(raw.get("curp")).upper(),
        "nss": _sanitize_field(raw.get("nss") or row.get("nss")),
        "apellido_paterno": _sanitize_field(raw.get("apellido_paterno")),
        "apellido_materno": _sanitize_field(raw.get("apellido_materno")),
        "nombre": _sanitize_field(raw.get("nombre")),
        "nombre_completo": nombre_completo,
        "genero": _sanitize_field(raw.get("genero")),
        "fecha_nacimiento": _format_fecha(raw.get("fecha_nacimiento")),
        "lugar_nacimiento": _sanitize_field(raw.get("lugar_nacimiento")),
    }


def _load_snapshot_db_rows(db_path: str, *, activos_only: bool = False) -> list[dict[str, Any]]:
    import sqlite3

    from modules.nomina.headcount_snapshot import get_snapshot_conn, validate_snapshot_state

    if not os.path.exists(db_path):
        return []
    state = validate_snapshot_state(db_path)
    if not state.get("snapshot_valid"):
        return []
    try:
        conn = get_snapshot_conn(db_path, readonly=True)
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='nomina_headcount_snapshot' LIMIT 1"
            ).fetchone()
            if not exists:
                return []
            count = int(conn.execute("SELECT COUNT(*) FROM nomina_headcount_snapshot").fetchone()[0] or 0)
            if count == 0:
                return []
            query = "SELECT * FROM nomina_headcount_snapshot"
            if activos_only:
                query += " WHERE activo = 1"
            query += " ORDER BY nombre_completo ASC"
            return [dict(r) for r in conn.execute(query).fetchall()]
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return []


def get_headcount_rows_from_snapshot(
    db_path: str,
    *,
    activos_only: bool = False,
    page: int = 1,
    per_page: int | None = None,
) -> dict[str, Any]:
    db_path = resolve_headcount_db_path(db_path)
    ctx = headcount_snapshot_page_context(db_path)
    raw_rows = _load_snapshot_db_rows(db_path, activos_only=activos_only)
    records = [_snapshot_row_to_record(r, i + 1) for i, r in enumerate(raw_rows)]
    total = len(records)
    perf_headcount_log(
        "snapshot_query",
        rows=len(records) if per_page is None else min(per_page, max(0, total - (page - 1) * (per_page or total))),
        total=total,
    )
    if per_page is not None and per_page > 0:
        page = max(1, int(page))
        start = (page - 1) * per_page
        records = records[start : start + per_page]
    return {
        "records": records,
        "total_rows": total,
        "page": page,
        "per_page": per_page,
        "available": bool(ctx["has_data"]) or bool(ctx["snapshot_exists"]),
        "has_data": bool(ctx["has_data"]),
        "snapshot_exists": bool(ctx["snapshot_exists"]),
        "message": ctx["message"],
        "meta": ctx["meta"],
        "stale": ctx["stale"],
        "refreshing": ctx["refreshing"],
        "status": ctx["status"],
    }


def get_headcount_active_rows_from_snapshot(db_path: str) -> list[dict[str, Any]]:
    return get_headcount_rows_from_snapshot(db_path, activos_only=True)["records"]


def search_headcount_snapshot(
    db_path: str,
    query: str,
    *,
    activos_only: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    q = normalize_text(query)
    q_digits = "".join(c for c in (query or "") if c.isdigit())
    if not q and not q_digits:
        return []
    out: list[dict[str, Any]] = []
    for rec in get_headcount_rows_from_snapshot(db_path, activos_only=activos_only)["records"]:
        if q and (
            q in normalize_text(rec.get("nombre_completo"))
            or q in normalize_text(rec.get("cliente"))
            or q in normalize_text(rec.get("ubicacion"))
            or q in normalize_text(rec.get("patron"))
        ):
            out.append(rec)
        elif q_digits and q_digits in str(rec.get("nss") or ""):
            out.append(rec)
        if len(out) >= limit:
            break
    return out


def get_headcount_clientes_from_snapshot(db_path: str) -> list[str]:
    db_path = resolve_headcount_db_path(db_path)
    if not headcount_snapshot_available(db_path):
        return []
    rows = load_headcount_snapshot_rows(db_path, activos_only=True)
    return sorted(
        {str(r.get("cliente") or "").strip() for r in rows if str(r.get("cliente") or "").strip()},
        key=lambda x: x.casefold(),
    )


def headcount_auto_refresh_enabled() -> bool:
    return (os.environ.get("HEADCOUNT_AUTO_REFRESH_ENABLED") or "0").strip() in {"1", "true", "yes", "on"}


def headcount_auto_refresh_cron_hint() -> str:
    return (os.environ.get("HEADCOUNT_AUTO_REFRESH_CRON_HINT") or "0 */2 * * *").strip()
