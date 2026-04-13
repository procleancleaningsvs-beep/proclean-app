"""Edición libre: merge de importes manuales sobre resultado calculado."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from modules.finiquitos.calc import calcular_finiquito
from modules.finiquitos.edicion_libre_finiquito import merge_finiquito_calc_with_manual


class TestEdicionLibreMerge(unittest.TestCase):
    def test_override_aguinaldo_recomputes_neto(self):
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
        base_ag = r["laboral"]["aguinaldo"]
        m = merge_finiquito_calc_with_manual(r, {"aguinaldo": float(base_ag) + 500.0})
        self.assertAlmostEqual(m["laboral"]["aguinaldo"], base_ag + 500.0, places=1)
        self.assertGreater(m["totales"]["total_percepciones"], r["totales"]["total_percepciones"])


if __name__ == "__main__":
    unittest.main()
