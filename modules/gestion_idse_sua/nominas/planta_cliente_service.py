from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
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


def expected_prior_week_bounds(
    *,
    weekday_start: int,
    reference: date | None = None,
) -> tuple[date, date]:
    ref = reference or date.today()
    days_since = (ref.weekday() - weekday_start) % 7
    current_week_start = ref - timedelta(days=days_since)
    prior_start = current_week_start - timedelta(days=7)
    prior_end = current_week_start - timedelta(days=1)
    return prior_start, prior_end


def _parse_period_dates(fecha_inicio: str, fecha_fin: str) -> tuple[date, date] | None:
    from modules.gestion_idse_sua.nominas.period_parser import parse_manual_period

    try:
        period = parse_manual_period(fecha_inicio, fecha_fin)
    except ValueError:
        return None
    start = datetime.strptime(period["fecha_inicio"], "%d/%m/%Y").date()
    end = datetime.strptime(period["fecha_fin"], "%d/%m/%Y").date()
    return start, end


def period_cut_warnings(
    conn: sqlite3.Connection,
    cliente: str | None,
    fecha_inicio: str,
    fecha_fin: str,
    *,
    reference: date | None = None,
) -> list[str]:
    if not cliente:
        return []
    row = conn.execute(
        "SELECT weekday_start FROM gis_cliente_corte WHERE cliente = ?",
        (normalize_upper(cliente),),
    ).fetchone()
    if row is None or row["weekday_start"] is None:
        return []

    parsed = _parse_period_dates(fecha_inicio, fecha_fin)
    if parsed is None:
        return []
    start, end = parsed
    expected = int(row["weekday_start"])
    names = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    warnings: list[str] = []
    if start.weekday() != expected:
        warnings.append(f"El inicio del periodo no coincide con el corte esperado ({names[expected]}).")

    prior_start, prior_end = expected_prior_week_bounds(weekday_start=expected, reference=reference)
    if start != prior_start or end != prior_end:
        warnings.append(
            "El periodo confirmado no corresponde a la semana anterior esperada según el corte del cliente; "
            "puede continuar importando periodos históricos."
        )
    return warnings


def expected_cut_warning(
    conn: sqlite3.Connection,
    cliente: str | None,
    fecha_inicio: str,
    fecha_fin: str,
) -> str | None:
    warnings = period_cut_warnings(conn, cliente, fecha_inicio, fecha_fin)
    return " | ".join(warnings) if warnings else None
