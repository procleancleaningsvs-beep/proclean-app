"""Comprobaciones rápidas del módulo finiquito (ejecutar desde la raíz del repo)."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from datetime import date
from decimal import Decimal

from modules.finiquitos.calc import calcular_finiquito
from modules.finiquitos.export_docx import build_finiquito_placeholders
from modules.finiquitos.fecha_limite_pago import fecha_limite_pago_finiquito, fecha_limite_pago_finiquito_larga


def main() -> None:
    nombre_largo = (
        "María Fernanda de los Ángeles López Hernández de la Garza y Villarreal"
    )
    emision = date(2026, 4, 11)
    baja = date(2026, 3, 26)
    calc = calcular_finiquito(
        ingreso=date(2024, 10, 15),
        baja=baja,
        fecha_emision=emision,
        salario_diario=Decimal("315.04"),
        zona="general",
        periodicidad_isr="semanal_mensualizada",
        modo="total_gravable",
        dias_sueldo_pendientes=Decimal("6"),
        septimos_pendientes=Decimal("1"),
        dias_aguinaldo_politica=Decimal("15"),
        prima_vacacional_pct=Decimal("25"),
        vacaciones_ya_usadas=Decimal("0"),
        aguinaldo_ya_pagado=Decimal("0"),
        prima_vac_ya_pagada=Decimal("0"),
        incluir_prima_antiguedad=False,
        motivo_baja="despido",
    )
    lim = fecha_limite_pago_finiquito(emision)
    m = build_finiquito_placeholders(
        lugar_emision="Monterrey",
        estado_emision="N. L.",
        fecha_emision=emision,
        fecha_baja=baja,
        empleado_nombre=nombre_largo,
        calc=calc,
        incluir_prima_antig=False,
    )
    print("1) Nombre firma (mayúsculas, sin truncar):", m["{empleado_nombre_completo}"] == nombre_largo.upper())
    print("2) Fecha límite != emisión:", fecha_limite_pago_finiquito(emision) != emision)
    print("3) Fecha límite larga:", fecha_limite_pago_finiquito_larga(emision))
    print("4) Sab/dom a habil:", lim.weekday() < 5)
    ded = Decimal(str(calc["totales"]["total_deducciones_reales"]))
    suma_d = Decimal(calc["pdf_filas"]["suma_d"].replace(",", ""))
    aj = Decimal(str(calc["totales"]["ajuste_neto"]))
    extra99 = abs(aj) if aj > 0 else Decimal("0")
    print("5) suma_d == ded + linea 99 (si ajuste > 0 deducción):", suma_d == ded + extra99)
    pdf = calc["pdf_filas"]
    print("6) ISR mes label:", pdf["c_isa"])
    print("7) ISR 174 label:", pdf["c_i174"] or "(vacío)")
    print("8) Fila 45 concepto:", pdf["c_imes"] or "(vacío)")
    print("9) Fila extra sep:", pdf.get("c_sep") or "(vacío)")


if __name__ == "__main__":
    main()
