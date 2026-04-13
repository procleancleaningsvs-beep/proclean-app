"""Partición ISR (mes) vs Art. 174 en modo total gravable."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from modules.finiquitos.calc import calc_isr_mes_semanal_mensualizado
from modules.finiquitos.isr_partition import particionar_bases_total_gravable


class TestParticionTodoGravable(unittest.TestCase):
    def test_caso_usuario_sin_excedente_uma(self):
        """Vacaciones + PV + aguinaldo bajo topes 30/15 UMA → base Art. 174 = 0; todo a ISR mes."""
        p = particionar_bases_total_gravable(
            total_percepciones=Decimal("3213.68"),
            aguinaldo=Decimal("1309.78"),
            prima_vacacional=Decimal("380.78"),
            prima_dominical=Decimal("0"),
            ptu=Decimal("0"),
            fecha_referencia=date(2026, 4, 7),
        )
        self.assertEqual(p.base_art174, Decimal("0.00"))
        self.assertEqual(p.base_isr_mes, Decimal("3213.68"))
        self.assertEqual(p.excedente_aguinaldo, Decimal("0.00"))
        self.assertEqual(p.excedente_prima_vacacional, Decimal("0.00"))

    def test_caso_referencia_2673_tarifa_isr_mes(self):
        """Percepciones 2,673.57 sin excedentes UMA: ISR mes fila 3; ISR periodo sin subsidio."""
        p = particionar_bases_total_gravable(
            total_percepciones=Decimal("2673.57"),
            aguinaldo=Decimal("1336.78"),
            prima_vacacional=Decimal("267.36"),
            prima_dominical=Decimal("0"),
            ptu=Decimal("0"),
            fecha_referencia=date(2026, 4, 7),
        )
        self.assertEqual(p.base_art174, Decimal("0.00"))
        self.assertEqual(p.base_isr_mes, Decimal("2673.57"))
        ing_m = Decimal("315.04") * Decimal("30.4")
        pkg = calc_isr_mes_semanal_mensualizado(
            p.base_isr_mes, date(2026, 4, 7), ing_m
        )
        t = pkg["tarifa_mensual_art96"]
        self.assertEqual(t["fila_numero"], 3)
        self.assertEqual(Decimal(str(t["limite_inferior"])), Decimal("7168.52"))
        self.assertEqual(Decimal(str(t["cuota_fija"])), Decimal("420.95"))
        self.assertEqual(Decimal(str(t["porcentaje_marginal"])), Decimal("10.88"))
        self.assertEqual(Decimal(str(pkg["isr_mes_neto"])), Decimal("208.22"))


if __name__ == "__main__":
    unittest.main()
