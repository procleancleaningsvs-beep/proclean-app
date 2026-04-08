"""Límites exentos en UMA (aguinaldo, prima vacacional) según ejercicio/fecha de pago."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from modules.finiquitos.config import UMA_DIARIA_2026

D0 = Decimal("0")


def uma_diaria_vigente(fecha_referencia: date) -> Decimal:
    """
    UMA diaria vigente para el ejercicio de la fecha.
    Centralizar aquí para ampliar por año sin hardcodear en el render.
    """
    if fecha_referencia.year >= 2026:
        return UMA_DIARIA_2026
    # Ejercicios anteriores: reutilizar 2026 como fallback hasta ampliar tablas.
    return UMA_DIARIA_2026


def limite_exento_aguinaldo_uma(fecha_referencia: date) -> Decimal:
    """Tope exento aguinaldo: 30 días de UMA (expresado en pesos)."""
    return Decimal("30") * uma_diaria_vigente(fecha_referencia)


def limite_exento_prima_vacacional_uma(fecha_referencia: date) -> Decimal:
    """Tope exento prima vacacional: 15 días de UMA (expresado en pesos)."""
    return Decimal("15") * uma_diaria_vigente(fecha_referencia)
