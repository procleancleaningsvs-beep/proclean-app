"""
Subsidio al empleo: parámetros ley vigente (config 2026) y prorrateo por días de periodo.
Solo se aplica contra ISR del periodo (art. 96); no contra Art. 174.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from modules.finiquitos.config import (
    SUBSIDIO_LIMITE_MENSUAL_2026,
    SUBSIDIO_PCT_2026,
    SUBSIDIO_PCT_ENERO_2026,
    UMA_DIARIA_2026,
    UMA_MENSUAL_2025,
    UMA_MENSUAL_2026,
)

D2 = Decimal("0.01")
D0 = Decimal("0")


def _q(x: Decimal) -> Decimal:
    return x.quantize(D2, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class SubsidioEmpleoDetalle:
    ingreso_mensual_base: Decimal
    limite_ingresos_subsidio: Decimal
    es_elegible: bool
    motivo_elegibilidad: str
    es_enero_2026: bool
    uma_mensual_referencia: Decimal
    uma_diaria_para_subsidio: Decimal
    porcentaje_sobre_uma: Decimal  # fracción (ej. 0.1502); en auditoría se muestra también como %
    dias_factor_mes: Decimal
    subsidio_mensual_maximo: Decimal
    dias_periodo: Decimal
    formula_prorrateo: str
    subsidio_periodo_maximo: Decimal
    isr_antes_subsidio_mensual: Decimal
    subsidio_topeado_mensual: Decimal
    isr_antes_subsidio_periodo: Decimal
    subsidio_aplicado: Decimal

    def to_auditoria(self) -> dict[str, Any]:
        return {
            "ingreso_mensual_base": float(self.ingreso_mensual_base),
            "limite_ingresos_subsidio": float(self.limite_ingresos_subsidio),
            "es_elegible": self.es_elegible,
            "motivo_elegibilidad": self.motivo_elegibilidad,
            "es_enero_2026": self.es_enero_2026,
            "uma_mensual_utilizada": float(self.uma_mensual_referencia),
            "uma_diaria_para_subsidio": float(self.uma_diaria_para_subsidio),
            "porcentaje_sobre_uma_fraccion": float(self.porcentaje_sobre_uma),
            "porcentaje_sobre_uma_pct": float(self.porcentaje_sobre_uma * Decimal("100")),
            "dias_factor_mes": float(self.dias_factor_mes),
            "subsidio_mensual_maximo": float(self.subsidio_mensual_maximo),
            "dias_periodo": float(self.dias_periodo),
            "formula_prorrateo": self.formula_prorrateo,
            "subsidio_periodo_maximo": float(self.subsidio_periodo_maximo),
            "isr_antes_subsidio_mensual": float(self.isr_antes_subsidio_mensual),
            "subsidio_topeado_por_isr_mensual": float(self.subsidio_topeado_mensual),
            "isr_antes_subsidio_periodo": float(self.isr_antes_subsidio_periodo),
            "subsidio_aplicado": float(self.subsidio_aplicado),
        }


def calcular_subsidio_empleo_periodo(
    fecha_pago: date,
    dias_periodo: Decimal,
    *,
    ingreso_mensual_equiv: Decimal,
    isr_antes_subsidio_mensual: Decimal,
    factor_mensual_dias: Decimal = Decimal("30.4"),
) -> SubsidioEmpleoDetalle:
    """
    Regla implementada (coherente con LISR / tablas en config):
    - Elegibilidad: 0 < ingreso_mensual_equiv ≤ SUBSIDIO_LIMITE_MENSUAL_2026.
    - Subsidio mensual máximo: UMA_mensual × porcentaje (enero 2026: UMA 2025 y % enero).
    - UMA diaria implícita: UMA_mensual / 30.4 (enero: UMA 2025 / 30.4).
    - Subsidio del periodo: min(UMA_diaria × % × días, tope mensual prorrateado), y no mayor al ISR del periodo.
    - Tope adicional: subsidio mensual aplicable no puede exceder ISR mensual antes de subsidio.
    """
    lim = SUBSIDIO_LIMITE_MENSUAL_2026
    ing = _q(ingreso_mensual_equiv)
    isr_men = _q(isr_antes_subsidio_mensual)
    isr_periodo_calc = _q(isr_men / factor_mensual_dias * dias_periodo)

    if ing <= 0:
        eleg = False
        mot = "Ingreso mensual equivalente ≤ 0: no hay derecho al subsidio al empleo."
    elif ing > lim:
        eleg = False
        mot = f"Ingreso mensual equivalente {ing} supera el límite {lim} (config 2026): sin subsidio."
    else:
        eleg = True
        mot = f"Ingreso mensual equivalente {ing} está dentro del límite {lim}: procede valorar subsidio."

    es_enero = fecha_pago.year == 2026 and fecha_pago.month == 1
    if es_enero:
        uma_m = UMA_MENSUAL_2025
        pct = SUBSIDIO_PCT_ENERO_2026
    else:
        uma_m = UMA_MENSUAL_2026
        pct = SUBSIDIO_PCT_2026

    uma_d = uma_m / factor_mensual_dias
    if not es_enero:
        uma_d = UMA_DIARIA_2026

    dias_p = dias_periodo
    if not eleg:
        return SubsidioEmpleoDetalle(
            ingreso_mensual_base=ing,
            limite_ingresos_subsidio=lim,
            es_elegible=False,
            motivo_elegibilidad=mot,
            es_enero_2026=es_enero,
            uma_mensual_referencia=uma_m,
            uma_diaria_para_subsidio=_q(uma_d),
            porcentaje_sobre_uma=pct,
            dias_factor_mes=factor_mensual_dias,
            subsidio_mensual_maximo=D0,
            dias_periodo=dias_p,
            formula_prorrateo="No aplica prorrateo (sin elegibilidad).",
            subsidio_periodo_maximo=D0,
            isr_antes_subsidio_mensual=isr_men,
            subsidio_topeado_mensual=D0,
            isr_antes_subsidio_periodo=isr_periodo_calc,
            subsidio_aplicado=D0,
        )

    sub_mensual_teorico = _q(uma_m * pct)

    sub_tope_mensual = _q(min(sub_mensual_teorico, isr_men))
    isr_periodo = isr_periodo_calc
    sub_prorrateado = _q(sub_tope_mensual / factor_mensual_dias * dias_p)
    sub_ap = _q(min(sub_prorrateado, isr_periodo))

    formula = (
        f"Subsidio mensual máximo = UMA mensual ({uma_m}) × {pct * Decimal('100')}% = {sub_mensual_teorico}; "
        f"tope aplicable al ISR mensual = min({sub_mensual_teorico}, ISR mensual {isr_antes_subsidio_mensual}) = {sub_tope_mensual}; "
        f"prorrateo periodo ({dias_p} días / {factor_mensual_dias}) = {sub_tope_mensual} × {dias_p} / {factor_mensual_dias} = {sub_prorrateado}; "
        f"aplicado = min({sub_prorrateado}, ISR periodo {isr_periodo}) = {sub_ap}."
    )

    return SubsidioEmpleoDetalle(
        ingreso_mensual_base=ing,
        limite_ingresos_subsidio=lim,
        es_elegible=True,
        motivo_elegibilidad=mot,
        es_enero_2026=es_enero,
        uma_mensual_referencia=uma_m,
        uma_diaria_para_subsidio=_q(uma_d),
        porcentaje_sobre_uma=pct,
        dias_factor_mes=factor_mensual_dias,
        subsidio_mensual_maximo=sub_mensual_teorico,
        dias_periodo=dias_p,
        formula_prorrateo=formula,
        subsidio_periodo_maximo=sub_prorrateado,
        isr_antes_subsidio_mensual=isr_men,
        subsidio_topeado_mensual=sub_tope_mensual,
        isr_antes_subsidio_periodo=isr_periodo,
        subsidio_aplicado=sub_ap,
    )


def subsidio_periodo_importe(
    fecha_pago: date,
    dias_periodo: Decimal,
    *,
    ingreso_mensual_equiv: Decimal,
    isr_antes_subsidio_mensual: Decimal,
    factor_mensual_dias: Decimal = Decimal("30.4"),
) -> Decimal:
    """Solo el monto aplicado (compatibilidad con llamadas existentes)."""
    return calcular_subsidio_empleo_periodo(
        fecha_pago,
        dias_periodo,
        ingreso_mensual_equiv=ingreso_mensual_equiv,
        isr_antes_subsidio_mensual=isr_antes_subsidio_mensual,
        factor_mensual_dias=factor_mensual_dias,
    ).subsidio_aplicado
