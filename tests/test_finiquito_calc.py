"""Pruebas mínimas de cálculo finiquito (ejemplos 1 y 2 del requerimiento)."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from modules.finiquitos.calc import calcular_finiquito
from modules.finiquitos.graph_excel import _normalize_name


class TestFiniquitoEjemplos(unittest.TestCase):
    def test_ejemplo_1_correcto_fiscal(self):
        r = calcular_finiquito(
            ingreso=date(2024, 10, 15),
            baja=date(2026, 3, 26),
            fecha_emision=date(2026, 3, 26),
            salario_diario=Decimal("315.04"),
            zona="general",
            periodicidad_isr="quincenal",
            modo="correcto_fiscal",
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
        self.assertAlmostEqual(r["totales"]["neto_final"], 5755.0, places=1)
        self.assertAlmostEqual(r["fiscal"]["isr_ordinario_antes_subsidio"], 277.14, places=1)
        self.assertAlmostEqual(r["fiscal"]["subsidio_aplicado"], 264.30, places=1)

    def test_ejemplo_2_aguinaldo_gravable(self):
        r = calcular_finiquito(
            ingreso=date(2024, 10, 15),
            baja=date(2026, 3, 26),
            fecha_emision=date(2026, 3, 26),
            salario_diario=Decimal("315.04"),
            zona="general",
            periodicidad_isr="quincenal",
            modo="aguinaldo_todo_gravable",
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
        self.assertAlmostEqual(r["fiscal"]["isr_art174"], 119.73, places=1)
        self.assertAlmostEqual(r["totales"]["neto_final"], 5635.20, places=1)

    def test_normaliza_nombre(self):
        self.assertEqual(_normalize_name("José  Álvarez"), _normalize_name("Jose Alvarez"))


if __name__ == "__main__":
    unittest.main()
