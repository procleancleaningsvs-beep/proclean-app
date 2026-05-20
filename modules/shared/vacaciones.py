"""
Tabla y devengamiento de vacaciones (Art. 76 LFT).

Compartido entre Finiquitos y Nómina > Vacaciones.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

D0 = Decimal("0")


def add_years_safe(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, month=2, day=28)


def full_years_between(start: date, end: date) -> int:
    """Años completos de calendario entre start y end (end >= start)."""
    if end < start:
        return 0
    y = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        y -= 1
    return y


def dias_vacaciones_ley_por_anio_servicio(anio_servicio: int) -> int:
    """
    Días de vacaciones anuales según art. 76 LFT (tabla 2026 del requerimiento).
    anio_servicio: 1 = primer año, 2 = segundo, etc.
    """
    if anio_servicio <= 0:
        return 12
    if anio_servicio <= 5:
        return 10 + 2 * anio_servicio  # 12,14,16,18,20
    if anio_servicio <= 10:
        return 22
    if anio_servicio <= 15:
        return 24
    if anio_servicio <= 20:
        return 26
    if anio_servicio <= 25:
        return 28
    if anio_servicio <= 30:
        return 30
    return 30


def calcular_dias_vacaciones_devengados(ingreso: date, baja: date) -> dict[str, Any]:
    """
    Días de vacaciones devengados hasta la fecha de corte
    (ciclos completos + proporcional del ciclo en curso).
    """
    anios_completos = full_years_between(ingreso, baja)
    dias_vac_completos = D0
    for y in range(1, anios_completos + 1):
        dias_vac_completos += Decimal(dias_vacaciones_ley_por_anio_servicio(y))

    ult_ann = add_years_safe(ingreso, anios_completos) if anios_completos > 0 else ingreso
    aniversario_siguiente = add_years_safe(ult_ann, 1)
    dias_anio_ciclo = max(1, (aniversario_siguiente - ult_ann).days)
    dias_transcurridos_ciclo = max(0, (baja - ult_ann).days + 1)
    dias_vac_anuales_actual = Decimal(dias_vacaciones_ley_por_anio_servicio(anios_completos + 1))
    factor_vac = Decimal(dias_transcurridos_ciclo) / Decimal(dias_anio_ciclo)
    dias_vac_prop_actual = dias_vac_anuales_actual * factor_vac
    dias_vac_total_dev = dias_vac_completos + dias_vac_prop_actual
    return {
        "anios_completos": anios_completos,
        "dias_vac_completos": dias_vac_completos,
        "ult_ann": ult_ann,
        "aniversario_siguiente": aniversario_siguiente,
        "dias_anio_ciclo": dias_anio_ciclo,
        "dias_transcurridos_ciclo": dias_transcurridos_ciclo,
        "dias_vac_anuales_actual": dias_vac_anuales_actual,
        "factor_vac": factor_vac,
        "dias_vac_prop_actual": dias_vac_prop_actual,
        "dias_vac_total_dev": dias_vac_total_dev,
    }
