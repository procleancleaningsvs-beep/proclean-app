from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from modules.gestion_idse_sua.nominas.text_utils import normalize_spaces, normalize_upper

_MONTHS: dict[str, int] = {
    "ENE": 1,
    "ENERO": 1,
    "FEB": 2,
    "FEBRERO": 2,
    "MAR": 3,
    "MARZO": 3,
    "ABR": 4,
    "ABRIL": 4,
    "MAY": 5,
    "MAYO": 5,
    "JUN": 6,
    "JUNIO": 6,
    "JUL": 7,
    "JULIO": 7,
    "AGO": 8,
    "AGOSTO": 8,
    "SEP": 9,
    "SEPT": 9,
    "SEPTIEMBRE": 9,
    "OCT": 10,
    "OCTUBRE": 10,
    "NOV": 11,
    "NOVIEMBRE": 11,
    "DIC": 12,
    "DICIEMBRE": 12,
}

_PERIOD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?P<d1>\d{1,2})\s*(?:DE\s+)?(?P<m1>[A-ZÁÉÍÓÚÑ]+)"
        r"\s+AL\s+"
        r"(?P<d2>\d{1,2})\s*(?:DE\s+)?(?P<m2>[A-ZÁÉÍÓÚÑ]+)",
        re.I,
    ),
    re.compile(
        r"(?P<d1>\d{1,2})\s+AL\s+(?P<d2>\d{1,2})\s+(?:DE\s+)?(?P<m2>[A-ZÁÉÍÓÚÑ]+)",
        re.I,
    ),
    re.compile(
        r"(?P<d1>\d{1,2})\s+AL\s+(?P<d2>\d{1,2})\s+DE\s+(?P<m2>[A-ZÁÉÍÓÚÑ]+)",
        re.I,
    ),
    re.compile(
        r"PERIODO\s+DEL\s+D[IÍ]A\s+(?P<d1>\d{1,2})\s+AL\s+(?P<d2>\d{1,2})\s+DE\s+(?P<m2>[A-ZÁÉÍÓÚÑ]+)",
        re.I,
    ),
    re.compile(
        r"NOMINA\s+DEL\s+(?P<d1>\d{1,2})\s+AL\s+(?P<d2>\d{1,2})\s+(?P<m2>[A-ZÁÉÍÓÚÑ]+)",
        re.I,
    ),
)


def _month_num(token: str) -> int | None:
    key = normalize_upper(token).replace("Á", "A").replace("É", "E").replace("Í", "I").replace("Ó", "O").replace("Ú", "U")
    return _MONTHS.get(key)


def _resolve_year(day: int, month: int, ref: date, explicit_year: int | None) -> int:
    if explicit_year is not None:
        return explicit_year
    candidate = date(ref.year, month, day)
    if (ref - candidate).days > 400:
        return ref.year + 1
    if (candidate - ref).days > 400:
        return ref.year - 1
    return ref.year


def _build_date(day: int, month_token: str, ref: date, explicit_year: int | None) -> date | None:
    month = _month_num(month_token)
    if month is None:
        return None
    year = _resolve_year(day, month, ref, explicit_year)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def detect_period(text: str, *, reference: date | None = None, explicit_year: int | None = None) -> dict[str, Any]:
    ref = reference or date.today()
    raw = normalize_spaces(str(text or ""))
    if not raw:
        return {"detected": False, "source": "empty"}

    upper = normalize_upper(raw)
    year_in_text = None
    year_match = re.search(r"\b(20\d{2})\b", upper)
    if year_match:
        year_in_text = int(year_match.group(1))

    for pattern in _PERIOD_PATTERNS:
        match = pattern.search(upper)
        if not match:
            continue
        groups = match.groupdict()
        d1 = int(groups["d1"])
        d2 = int(groups["d2"])
        m2_token = groups["m2"]
        m1_token = groups.get("m1") or m2_token
        if groups.get("m1") is None and d1 > d2:
            end_month = _month_num(m2_token)
            if end_month is not None and end_month > 1:
                prev_month_tokens = [k for k, v in _MONTHS.items() if v == end_month - 1 and len(k) <= 3]
                if prev_month_tokens:
                    m1_token = prev_month_tokens[0]

        start = _build_date(d1, m1_token, ref, year_in_text or explicit_year)
        end = _build_date(d2, m2_token, ref, year_in_text or explicit_year)
        if start is None or end is None:
            continue
        if end < start:
            end_month = _month_num(m2_token)
            if end_month is not None and start.month > end_month:
                end = date(end.year + 1, end.month, end.day)
            elif end.month <= start.month:
                end = date(start.year + 1, end.month, end.day)

        days = (end - start).days + 1
        iso_week = start.isocalendar()[1]
        warning = None if days == 7 else f"El periodo abarca {days} días (se esperaban 7)."
        return {
            "detected": True,
            "fecha_inicio": start.strftime("%d/%m/%Y"),
            "fecha_fin": end.strftime("%d/%m/%Y"),
            "semana_num": iso_week,
            "source": "text",
            "raw_text": raw,
            "days": days,
            "cut_warning": warning,
        }

    return {"detected": False, "source": "unparsed", "raw_text": raw}


def parse_manual_period(fecha_inicio: str, fecha_fin: str, *, reference: date | None = None) -> dict[str, Any]:
    ref = reference or date.today()

    def _parse(value: str) -> date | None:
        s = normalize_spaces(value)
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(s[:10], fmt).date()
            except ValueError:
                continue
        return None

    start = _parse(fecha_inicio)
    end = _parse(fecha_fin)
    if start is None or end is None:
        raise ValueError("Fechas de periodo inválidas.")
    if end < start:
        raise ValueError("La fecha final debe ser posterior o igual a la inicial.")
    days = (end - start).days + 1
    warning = None if days == 7 else f"El periodo abarca {days} días (se esperaban 7)."
    return {
        "detected": True,
        "fecha_inicio": start.strftime("%d/%m/%Y"),
        "fecha_fin": end.strftime("%d/%m/%Y"),
        "semana_num": start.isocalendar()[1],
        "source": "manual",
        "days": days,
        "cut_warning": warning,
    }


def merge_cut_warning(period: dict[str, Any], cliente: str | None, conn) -> dict[str, Any]:
    from modules.gestion_idse_sua.nominas.planta_cliente_service import expected_cut_warning

    extra = expected_cut_warning(conn, cliente, period.get("fecha_inicio", ""), period.get("fecha_fin", ""))
    warnings = [w for w in [period.get("cut_warning"), extra] if w]
    period = dict(period)
    period["cut_warning"] = " | ".join(warnings) if warnings else None
    return period
