"""Fecha límite de pago del finiquito: emisión + 15 días naturales, siguiente día hábil si aplica."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from modules.finiquitos.fecha_es import fecha_emision_larga


def es_dia_habil_mexico(
    d: date,
    *,
    feriados: Optional[frozenset[date]] = None,
) -> bool:
    """Mínimo: sábado y domingo inhábiles. Opcional: feriados (configuración futura)."""
    if d.weekday() >= 5:
        return False
    if feriados is not None and d in feriados:
        return False
    return True


def siguiente_dia_habil_mexico(
    d: date,
    *,
    feriados: Optional[frozenset[date]] = None,
) -> date:
    cur = d
    while not es_dia_habil_mexico(cur, feriados=feriados):
        cur += timedelta(days=1)
    return cur


def fecha_limite_pago_finiquito(
    fecha_emision: date,
    *,
    feriados: Optional[frozenset[date]] = None,
) -> date:
    """
    Regla: fecha de emisión + 15 días naturales; si cae en inhábil, siguiente día hábil.
    No usar fecha de terminación laboral aquí.
    """
    candidata = fecha_emision + timedelta(days=15)
    return siguiente_dia_habil_mexico(candidata, feriados=feriados)


def fecha_limite_pago_finiquito_larga(
    fecha_emision: date,
    *,
    feriados: Optional[frozenset[date]] = None,
) -> str:
    """Formato largo en español, p. ej. 'miércoles 22 de abril de 2026'."""
    return fecha_emision_larga(fecha_limite_pago_finiquito(fecha_emision, feriados=feriados))
