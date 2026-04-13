"""Desglose editable: apply_desglose_manual."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from modules.finiquitos.calc import calcular_finiquito
from modules.finiquitos.edicion_libre_finiquito import apply_desglose_manual


def _base_filas_from_calc(c: dict) -> list[dict]:
    lab = c["laboral"]
    fis = c["fiscal"]
    filas = []
    for ck, concepto in (
        ("sueldo", "Sueldo"),
        ("septimo_dia", "Séptimo día"),
        ("vacaciones_a_tiempo", "Vacaciones"),
        ("prima_vacacional", "Prima vacacional"),
        ("aguinaldo", "Aguinaldo"),
        ("prima_antiguedad_monto", "Prima de antigüedad"),
        ("prima_dominical", "Prima dominical"),
        ("ptu", "PTU"),
    ):
        filas.append(
            {
                "id": f"base:P:{ck}",
                "tipo": "P",
                "concepto": concepto,
                "clave": ck,
                "monto": float(lab.get(ck) or 0),
            }
        )
    filas.append(
        {
            "id": "base:ISR:isr_41",
            "tipo": "ISR",
            "concepto": "41 I.S.R. (referencia)",
            "clave": "isr_41_ref",
            "monto": float(fis.get("isr_ordinario_antes_subsidio") or 0),
        }
    )
    filas.append(
        {
            "id": "base:ISR:isr174",
            "tipo": "ISR",
            "concepto": "43 I.S.R. Art174",
            "clave": "isr_art174",
            "monto": float(fis.get("isr_art174") or 0),
        }
    )
    filas.append(
        {
            "id": "base:ISR:isr_ord",
            "tipo": "ISR",
            "concepto": "45 I.S.R. (mes)",
            "clave": "isr_ordinario",
            "monto": float(fis.get("isr_mes_neto") or 0),
        }
    )
    filas.append(
        {
            "id": "base:ISR:isr_sep",
            "tipo": "ISR",
            "concepto": "ISR separación",
            "clave": "isr_separacion",
            "monto": float(fis.get("isr_separacion") or 0),
        }
    )
    return filas


class TestDesgloseManual(unittest.TestCase):
    def test_extra_percepcion_suma_total(self):
        c = calcular_finiquito(
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
        filas = _base_filas_from_calc(c)
        filas.append({"id": "extra-p:x1", "tipo": "P", "concepto": "Bono", "monto": 100.0})
        m = apply_desglose_manual(c, filas)
        self.assertAlmostEqual(m["totales"]["total_percepciones"], c["totales"]["total_percepciones"] + 100.0, delta=1.5)

    def test_extra_deduccion_resta_neto(self):
        c = calcular_finiquito(
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
        filas = _base_filas_from_calc(c)
        filas.append({"id": "extra-d:x1", "tipo": "D", "concepto": "Otra deducción", "monto": 50.0})
        m = apply_desglose_manual(c, filas)
        self.assertLess(m["totales"]["neto_final"], c["totales"]["neto_final"])


if __name__ == "__main__":
    unittest.main()
