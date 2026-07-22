from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Iterator


def parse_period_date(value: str) -> date:
    text = str(value or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Fecha de periodo no reconocida: {value}")


def iter_period_dates(fecha_inicio: str, fecha_fin: str) -> Iterator[date]:
    start = parse_period_date(fecha_inicio)
    end = parse_period_date(fecha_fin)
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def period_intersects_month(fecha_inicio: str, fecha_fin: str, *, mes: int, anio: int) -> bool:
    for day in iter_period_dates(fecha_inicio, fecha_fin):
        if day.month == mes and day.year == anio:
            return True
    return False


def days_in_calendar_month(*, mes: int, anio: int) -> list[date]:
    last = monthrange(anio, mes)[1]
    return [date(anio, mes, day) for day in range(1, last + 1)]


def clip_iso_dates_to_month(records: list[dict], *, mes: int, anio: int) -> list[dict]:
    clipped: list[dict] = []
    for record in records:
        fecha = str(record.get("fecha_iso") or "")
        if not fecha:
            continue
        try:
            day = datetime.strptime(fecha, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day.month == mes and day.year == anio:
            clipped.append(record)
    return clipped


def month_label(mes: int, anio: int) -> str:
    return f"{mes:02d}/{anio}"
