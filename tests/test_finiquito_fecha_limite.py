"""Fecha límite de pago (emisión + 15 naturales, siguiente hábil) y modo total gravable / Art. 174."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from modules.finiquitos.calc import calcular_finiquito
from modules.finiquitos.fecha_limite_pago import fecha_limite_pago_finiquito


class TestFechaLimitePago(unittest.TestCase):
    def test_no_repite_emision(self):
        em = date(2026, 4, 1)
        lim = fecha_limite_pago_finiquito(em)
        self.assertNotEqual(lim, em)

    def test_sabado_pasa_a_lunes(self):
        # viernes 10 abr 2026 + 15 = sábado 25 abr 2026 → lunes 27
        em = date(2026, 4, 10)
        self.assertEqual(em.weekday(), 4)
        lim = fecha_limite_pago_finiquito(em)
        self.assertEqual(lim.weekday(), 0)
        self.assertEqual(lim, date(2026, 4, 27))

    def test_domingo_pasa_a_lunes(self):
        # sáb 11 abr 2026 + 15 = domingo 26 abr 2026 → lunes 27
        em = date(2026, 4, 11)
        self.assertEqual(em.weekday(), 5)
        lim = fecha_limite_pago_finiquito(em)
        self.assertEqual(lim.weekday(), 0)
        self.assertEqual(lim, date(2026, 4, 27))


class TestPdfSumaDeducciones(unittest.TestCase):
    def test_suma_d_sin_subsidio_en_monto(self):
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
        ded = Decimal(str(r["totales"]["total_deducciones_reales"]))
        suma_s = r["pdf_filas"]["suma_d"].replace(",", "")
        suma_d = Decimal(suma_s)
        self.assertEqual(ded, suma_d)
        if Decimal(str(r["fiscal"]["subsidio_aplicado"])) > 0:
            self.assertIn("Subsidio", r["pdf_filas"]["c_imes"])


class TestTotalGravableArt174(unittest.TestCase):
    def test_isr_174_separado_de_ordinario(self):
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
        self.assertGreater(f["bucket_art174_gravado"], 0)
        self.assertGreater(f["isr_art174"], 0)
        self.assertGreater(f["isr_ordinario_antes_subsidio"], 0)


if __name__ == "__main__":
    unittest.main()
