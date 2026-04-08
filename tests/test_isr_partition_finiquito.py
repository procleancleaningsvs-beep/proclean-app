"""Partición ISR (mes) vs Art. 174 en modo total gravable."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

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
            modo="total_gravable",
        )
        self.assertEqual(p.base_art174, Decimal("0.00"))
        self.assertEqual(p.base_isr_mes, Decimal("3213.68"))
        self.assertEqual(p.excedente_aguinaldo, Decimal("0.00"))
        self.assertEqual(p.excedente_prima_vacacional, Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()
