from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from modules.gestion_idse_sua.nominas.text_utils import normalize_planta, normalize_upper


def get_planta_cliente(conn: sqlite3.Connection, planta: str) -> dict[str, Any] | None:
    key = normalize_planta(planta)
    if not key:
        return None
    row = conn.execute(
        "SELECT * FROM gis_planta_cliente WHERE planta_normalizada = ?",
        (key,),
    ).fetchone()
    return dict(row) if row else None


def confirm_planta_cliente(
    conn: sqlite3.Connection,
    *,
    planta: str,
    cliente: str,
    confirmed_by: str | None,
    source: str = "manual",
) -> dict[str, Any]:
    key = normalize_planta(planta)
    cliente_norm = normalize_upper(cliente)
    if not key or not cliente_norm:
        raise ValueError("Planta y cliente son obligatorios.")
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO gis_planta_cliente
            (planta_normalizada, planta_original, cliente, confirmed_by, confirmed_at, source)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(planta_normalizada) DO UPDATE SET
            cliente = excluded.cliente,
            confirmed_by = excluded.confirmed_by,
            confirmed_at = excluded.confirmed_at,
            source = excluded.source
        """,
        (key, planta.strip(), cliente_norm, confirmed_by, now, source),
    )
    return get_planta_cliente(conn, key) or {}


def headcount_client_trend(planta: str, headcount_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    key = normalize_planta(planta)
    if not key:
        return None
    counts: dict[str, int] = {}
    for row in headcount_rows:
        row_planta = normalize_planta(row.get("planta") or row.get("ubicacion") or "")
        if row_planta != key:
            continue
        cliente = normalize_upper(row.get("cliente"))
        if cliente:
            counts[cliente] = counts.get(cliente, 0) + 1
    if not counts:
        return None
    cliente, total = max(counts.items(), key=lambda item: item[1])
    return {
        "cliente": cliente,
        "count": total,
        "source": "headcount_trend",
        "confidence": min(0.95, 0.5 + (total / max(sum(counts.values()), 1)) * 0.45),
    }


def suggest_cliente_for_planta(
    conn: sqlite3.Connection,
    planta: str,
    headcount_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    known = get_planta_cliente(conn, planta)
    if known:
        return {
            "cliente": known["cliente"],
            "source": "catalog",
            "confidence": 1.0,
            "requires_confirmation": False,
        }
    trend = headcount_client_trend(planta, headcount_rows)
    if trend:
        return {
            "cliente": trend["cliente"],
            "source": trend["source"],
            "confidence": trend["confidence"],
            "requires_confirmation": True,
        }
    return {
        "cliente": "",
        "source": "unknown",
        "confidence": 0.0,
        "requires_confirmation": True,
    }


def detect_planta_cliente_conflict(
    *,
    planta_cliente: str,
    headcount_cliente: str,
) -> bool:
    p = normalize_upper(planta_cliente)
    h = normalize_upper(headcount_cliente)
    if not p or not h:
        return False
    return p != h


def expected_cut_warning(
    conn: sqlite3.Connection,
    cliente: str | None,
    fecha_inicio: str,
    fecha_fin: str,
) -> str | None:
    if not cliente:
        return None
    row = conn.execute(
        "SELECT weekday_start FROM gis_cliente_corte WHERE cliente = ?",
        (normalize_upper(cliente),),
    ).fetchone()
    if row is None or row["weekday_start"] is None:
        return None

    from modules.gestion_idse_sua.nominas.period_parser import parse_manual_period

    try:
        period = parse_manual_period(fecha_inicio, fecha_fin)
    except ValueError:
        return None
    from datetime import datetime

    start = datetime.strptime(period["fecha_inicio"], "%d/%m/%Y").date()
    expected = int(row["weekday_start"])
    if start.weekday() != expected:
        names = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        return f"El inicio del periodo no coincide con el corte esperado ({names[expected]})."
    return None
