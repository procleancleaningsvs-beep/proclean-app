"""Fechas Vitroflex (sin dependencias externas)."""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


_MESES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def mes_nombre(m: int) -> str:
    if 1 <= m <= 12:
        return _MESES[m - 1]
    return str(m)


MUNICIPIO_GARCIA = "Garcia, N. L."


def default_fecha_linea(*, tz: ZoneInfo | None = None) -> str:
    """Valor por defecto para {{FECHA}} en español."""
    tz = tz or ZoneInfo("America/Mexico_City")
    now = datetime.now(tz)
    dia = now.day
    mes = mes_nombre(now.month)
    anio = now.year
    return f"{MUNICIPIO_GARCIA} a {dia} de {mes} de {anio}"


def linea_fecha_documento(
    *,
    fecha_iso: str | None = None,
    municipio_modo: str | None = None,
    municipio_otro: str | None = None,
    fecha_texto_legacy: str | None = None,
) -> str:
    """
    Compone la línea de fecha para {{FECHA}}: «[municipio] a [día] de [mes] de [año]».
    Si hay fecha_iso, manda la composición; si no, conserva texto legacy o el default.
    """
    legacy = (fecha_texto_legacy or "").strip()
    d = parse_iso_date(fecha_iso) if fecha_iso else None
    if d:
        modo = (municipio_modo or "").strip().lower()
        if modo == "otro":
            muni = (municipio_otro or "").strip() or MUNICIPIO_GARCIA
        else:
            muni = MUNICIPIO_GARCIA
        return f"{muni} a {d.day} de {mes_nombre(d.month)} de {d.year}"
    return legacy or default_fecha_linea()


def add_months(d: date, months: int) -> date:
    m0 = d.month - 1 + months
    y = d.year + m0 // 12
    m = m0 % 12 + 1
    last = calendar.monthrange(y, m)[1]
    day = min(d.day, last)
    return date(y, m, day)


def permiso2_desde_permiso1(d: date) -> date:
    """PERMISO_1 + 1 mes + 17 días."""
    return add_months(d, 1) + timedelta(days=17)


def parse_iso_date(s: str | None) -> date | None:
    if not s or not str(s).strip():
        return None
    s = str(s).strip()[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


_MES_MAP = {nombre: i + 1 for i, nombre in enumerate(_MESES)}


def extraer_mes_anio_desde_texto_fecha(texto: str | None) -> tuple[str, str] | None:
    """
    Intenta obtener ('marzo', '2026') desde textos tipo '... 15 de marzo de 2026'.
    """
    if not texto:
        return None
    t = texto.strip().lower()
    m = re.search(r"de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})", t)
    if not m:
        return None
    mes_txt, anio = m.group(1), m.group(2)
    # normalizar acentos comunes
    mes_norm = mes_txt.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    for nombre in _MESES:
        if nombre == mes_norm or nombre.replace("á", "a") == mes_norm:
            return nombre, anio
    return None
