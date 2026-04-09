"""Pruebas mínimas de cálculo finiquito (ejemplos 1 y 2 del requerimiento)."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from modules.finiquitos.calc import calcular_dias_vacaciones_devengados, calcular_finiquito
from modules.finiquitos.graph_excel import _normalize_name


class TestFiniquitoEjemplos(unittest.TestCase):
    def test_ejemplo_1_correcto_fiscal(self):
        r = calcular_finiquito(
            ingreso=date(2024, 10, 15),
            baja=date(2026, 3, 26),
            fecha_emision=date(2026, 3, 26),
            salario_diario=Decimal("315.04"),
            zona="general",
            periodicidad_isr="semanal_mensualizada",
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
        self.assertEqual(r["fiscal"]["criterio_isr_ordinario"], "semanal_mensualizada_tipo_contpaq")
        self.assertGreater(r["fiscal"]["base_ordinaria_mensualizada"], r["fiscal"]["bucket_ordinario_gravado"])
        self.assertGreaterEqual(r["fiscal"]["isr_ordinario_antes_subsidio"], 0)
        self.assertGreaterEqual(r["totales"]["neto_final"], 0)

    def test_ejemplo_2_aguinaldo_gravable(self):
        r = calcular_finiquito(
            ingreso=date(2024, 10, 15),
            baja=date(2026, 3, 26),
            fecha_emision=date(2026, 3, 26),
            salario_diario=Decimal("315.04"),
            zona="general",
            periodicidad_isr="semanal_mensualizada",
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
        self.assertGreaterEqual(r["fiscal"]["isr_art174"], 0)
        self.assertGreaterEqual(r["totales"]["neto_final"], 0)

    def test_normaliza_nombre(self):
        self.assertEqual(_normalize_name("José  Álvarez"), _normalize_name("Jose Alvarez"))

    def test_concepto_41_igual_45_sin_subsidio(self):
        r = calcular_finiquito(
            ingreso=date(2024, 10, 15),
            baja=date(2026, 3, 26),
            fecha_emision=date(2026, 3, 26),
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
        f = r["fiscal"]
        self.assertEqual(f["isr_ordinario_antes_subsidio"], f["isr_ordinario_neto"])
        self.assertEqual(f["subsidio_aplicado"], 0.0)
        pf = r["pdf_filas"]
        self.assertTrue(pf["t8"])
        self.assertEqual(pf["t8"], pf["t10"])

    def test_tope_vacaciones_ya_usadas_si_ingreso_año_actual(self):
        ing = date(2026, 1, 1)
        baja = date(2026, 4, 7)
        cap = calcular_dias_vacaciones_devengados(ing, baja)["dias_vac_total_dev"]
        r = calcular_finiquito(
            ingreso=ing,
            baja=baja,
            fecha_emision=date(2026, 4, 7),
            salario_diario=Decimal("315.04"),
            zona="general",
            periodicidad_isr="semanal_mensualizada",
            modo="total_gravable",
            dias_sueldo_pendientes=Decimal("0"),
            septimos_pendientes=Decimal("0"),
            dias_aguinaldo_politica=Decimal("15"),
            prima_vacacional_pct=Decimal("25"),
            vacaciones_ya_usadas=Decimal("100"),
            aguinaldo_ya_pagado=Decimal("0"),
            prima_vac_ya_pagada=Decimal("0"),
            incluir_prima_antiguedad=False,
            motivo_baja="despido",
            año_calendario_actual=2026,
        )
        l = r["laboral"]
        self.assertTrue(l["vacaciones_ya_usadas_tope_por_ingreso_año_actual"])
        self.assertTrue(l["vacaciones_ya_usadas_se_recorto"])
        self.assertAlmostEqual(l["vacaciones_ya_usadas_efectivas"], float(cap), places=6)
        self.assertAlmostEqual(l["vacaciones_ya_usadas_max_permitido"], float(cap), places=6)


if __name__ == "__main__":
    unittest.main()
