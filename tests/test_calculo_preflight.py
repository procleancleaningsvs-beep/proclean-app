"""Tests preflight de calculo de nomina (sin motor de calculo)."""
from __future__ import annotations

import unittest

from modules.nomina.calculo_preflight import (
    NIVEL_CRITICAL,
    NIVEL_INFO,
    NIVEL_REVIEW,
    _build_prima_vacacional_context,
    _match_vacaciones_for_asistencia,
    _resumen_observaciones,
    preflight_requires_screen,
)


class TestCalculoPreflightVacacionesMatch(unittest.TestCase):
    def test_match_por_nss_confiable(self):
        vac_row = {"nss": "123", "match_status": "MATCH_OK", "prima_2026_pagada": 0, "saldo_calculado": 2}
        by_nss = {"123": vac_row}
        row = {"nss": "123", "nombre_empleado": "JUAN PEREZ"}
        matched, method, confident = _match_vacaciones_for_asistencia(row, by_nss=by_nss, by_name={})
        self.assertIs(matched, vac_row)
        self.assertEqual(method, "nss")
        self.assertTrue(confident)

    def test_match_por_nombre_sin_match(self):
        row = {"nombre_empleado": "MARIA LOPEZ"}
        matched, method, confident = _match_vacaciones_for_asistencia(row, by_nss={}, by_name={})
        self.assertIsNone(matched)
        self.assertEqual(method, "sin_match")
        self.assertFalse(confident)


class TestCalculoPreflightPrimaVacacional(unittest.TestCase):
    def _base_vac(self):
        return {
            "nss": "999",
            "match_status": "MATCH_OK",
            "prima_2026_pagada": 0,
            "saldo_calculado": 3,
            "dias_restantes_calculado": 3,
            "cliente": "Carrier A",
            "planta_headcount": "Planta 1",
        }

    def test_prima_informativa_desde_vacaciones(self):
        vac = self._base_vac()
        by_nss = {"999": vac}
        raw = {"id": 10, "row_number": 5, "nss": "999", "nombre_empleado": "PEDRO GARCIA", "prima_vacacional": "SOLICITA", "cliente": "Carrier A", "planta": "Planta 1"}
        preview = [{
            "asistencia_row_id": 10,
            "nombre_empleado": "PEDRO GARCIA",
            "nss": "999",
            "prima_vacacional_aplicada": 1,
            "dias_prima_vacacional_pendientes": 3,
            "importe_prima_vacacional": 150.0,
            "warnings_json": [],
        }]
        observaciones: list = []
        info = _build_prima_vacacional_context(
            preview,
            {10: raw},
            by_nss=by_nss,
            by_name={},
            observaciones=observaciones,
            seen=set(),
        )
        self.assertEqual(len(info), 1)
        self.assertEqual(info[0]["origen"], "Vacaciones")
        self.assertIn("borrador", info[0]["accion"].lower())

    def test_prima_sin_match_genera_observacion_revision(self):
        raw = {"id": 11, "row_number": 6, "nss": "", "nombre_empleado": "ANA RUIZ", "prima_vacacional": "SOLICITA"}
        preview = [{
            "asistencia_row_id": 11,
            "nombre_empleado": "ANA RUIZ",
            "warnings_json": ["prima_vacacional_sin_datos_vacaciones"],
        }]
        observaciones: list = []
        info = _build_prima_vacacional_context(
            preview,
            {11: raw},
            by_nss={},
            by_name={},
            observaciones=observaciones,
            seen=set(),
        )
        self.assertEqual(info, [])
        self.assertEqual(len(observaciones), 1)
        self.assertEqual(observaciones[0]["nivel"], NIVEL_REVIEW)
        self.assertEqual(observaciones[0]["codigo"], "prima_vacacional_sin_match_confiable")

    def test_contradiccion_asistencia_solicita_vacaciones_pagada(self):
        vac = self._base_vac()
        vac["prima_2026_pagada"] = 1
        by_nss = {"999": vac}
        raw = {"id": 12, "row_number": 7, "nss": "999", "nombre_empleado": "LUIS MORALES", "prima_vacacional": "SOLICITA"}
        preview = [{
            "asistencia_row_id": 12,
            "nombre_empleado": "LUIS MORALES",
            "nss": "999",
            "warnings_json": ["prima_vacacional_ya_cubierta"],
        }]
        observaciones: list = []
        _build_prima_vacacional_context(
            preview,
            {12: raw},
            by_nss=by_nss,
            by_name={},
            observaciones=observaciones,
            seen=set(),
        )
        self.assertTrue(any("Contradiccion" in o["detalle"] for o in observaciones))


class TestCalculoPreflightScreenLogic(unittest.TestCase):
    def test_preflight_requires_screen_con_observaciones(self):
        preflight = {"observaciones": [{"nivel": NIVEL_INFO}], "preguntas_necesarias": []}
        self.assertTrue(preflight_requires_screen(preflight))

    def test_preflight_no_requiere_pantalla_limpia(self):
        preflight = {"observaciones": [], "preguntas_necesarias": [], "prima_vacacional_informativa": []}
        self.assertFalse(preflight_requires_screen(preflight))

    def test_resumen_observaciones(self):
        obs = [
            {"nivel": NIVEL_CRITICAL},
            {"nivel": NIVEL_REVIEW},
            {"nivel": NIVEL_INFO},
            {"nivel": NIVEL_INFO},
        ]
        resumen = _resumen_observaciones(obs)
        self.assertEqual(resumen[NIVEL_CRITICAL], 1)
        self.assertEqual(resumen[NIVEL_REVIEW], 1)
        self.assertEqual(resumen[NIVEL_INFO], 2)


if __name__ == "__main__":
    unittest.main()
