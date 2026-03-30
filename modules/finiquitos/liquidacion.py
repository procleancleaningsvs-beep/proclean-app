"""Liquidación comparativa (solo preview/historial; sin PDF)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from modules.finiquitos.calc import anios_servicio_exactos, calcular_finiquito


def calcular_liquidacion_comparativa(
    *,
    ingreso: date,
    baja: date,
    fecha_emision: date,
    salario_diario: Decimal,
    zona: str,
    periodicidad_isr: str,
    modo: str,
    dias_sueldo_pendientes: Decimal,
    septimos_pendientes: Decimal,
    dias_aguinaldo_politica: Decimal,
    prima_vacacional_pct: Decimal,
    vacaciones_ya_usadas: Decimal,
    aguinaldo_ya_pagado: Decimal,
    prima_vac_ya_pagada: Decimal,
    incluir_prima_antiguedad: bool,
    motivo_baja: str,
    salario_mensual_capturado: Decimal | None = None,
) -> dict[str, Any]:
    base = calcular_finiquito(
        ingreso=ingreso,
        baja=baja,
        fecha_emision=fecha_emision,
        salario_diario=salario_diario,
        zona=zona,  # type: ignore[arg-type]
        periodicidad_isr=periodicidad_isr,  # type: ignore[arg-type]
        modo=modo,  # type: ignore[arg-type]
        dias_sueldo_pendientes=dias_sueldo_pendientes,
        septimos_pendientes=septimos_pendientes,
        dias_aguinaldo_politica=dias_aguinaldo_politica,
        prima_vacacional_pct=prima_vacacional_pct,
        vacaciones_ya_usadas=vacaciones_ya_usadas,
        aguinaldo_ya_pagado=aguinaldo_ya_pagado,
        prima_vac_ya_pagada=prima_vac_ya_pagada,
        incluir_prima_antiguedad=incluir_prima_antiguedad,
        motivo_baja=motivo_baja,
        salario_mensual_capturado=salario_mensual_capturado,
    )

    ax = anios_servicio_exactos(ingreso, baja)
    tres_meses = salario_diario * Decimal("30.4") * Decimal("3")
    veinte_dias = salario_diario * Decimal("20") * ax

    base["liquidacion"] = {
        "indemnizacion_tres_meses": float(tres_meses),
        "indemnizacion_veinte_dias_por_anio": float(veinte_dias),
        "nota": "Comparativo interno (art. 50 y 89 LFT; salario fijo). Variable: usar promedio 30 días art. 89.",
    }
    return base
