"""Tests preflight de calculo de nomina (sin motor de calculo)."""
from __future__ import annotations

import unittest

from modules.nomina.calculo_preflight import (
    NIVEL_CRITICAL,
    NIVEL_INFO,
    NIVEL_REVIEW,
    WARN_BLOCK_CALC_MISSING_SALARY,
    WARN_BLOCK_CALC_MISSING_VALOR_HE,
    _build_observaciones_from_payload,
    _build_prima_vacacional_context,
    _build_trabajador_datos,
    _catalog_observacion,
    _match_vacaciones_for_asistencia,
    _resumen_observaciones,
    _should_skip_preflight_warning,
    _split_observaciones,
    build_calculo_preflight,
    preflight_requires_screen,
    save_parametro_from_preflight,
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

    def test_prima_pagada_no_va_a_informativa(self):
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
        info = _build_prima_vacacional_context(
            preview,
            {12: raw},
            by_nss=by_nss,
            by_name={},
            observaciones=observaciones,
            seen=set(),
        )
        self.assertEqual(info, [])
        self.assertTrue(any("duplicidad" in o["detalle"].lower() for o in observaciones))

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


class TestCalculoPreflightObservaciones(unittest.TestCase):
    def test_falta_salario_muestra_mensaje_operativo(self):
        obs = _catalog_observacion(
            WARN_BLOCK_CALC_MISSING_SALARY,
            trabajador="JUAN PEREZ",
            fila=5,
        )
        self.assertEqual(obs["nivel"], NIVEL_CRITICAL)
        self.assertIn("Parametros", obs["detalle"])
        self.assertNotIn("block_calc_missing_salary_operativo", obs["detalle"])
        self.assertIn("salario operativo", obs["accion_sugerida"].lower())

    def test_infonavit_sin_registro_se_omite(self):
        self.assertTrue(_should_skip_preflight_warning("infonavit_sin_registro_para_nss"))

    def test_infonavit_sin_registro_no_genera_observacion(self):
        payload = {
            "raw_json": {"run_warnings": []},
            "rows": [{
                "asistencia_row_id": 1,
                "nombre_empleado": "PEDRO",
                "nss": "111",
                "warnings_json": ["infonavit_sin_registro_para_nss"],
                "blocks_json": [],
            }],
        }
        rows_by_id = {1: {"id": 1, "row_number": 2, "nombre_empleado": "PEDRO", "nss": "111"}}
        observaciones, _ = _build_observaciones_from_payload(payload, rows_by_id, by_nss={}, by_name={})
        self.assertEqual(observaciones, [])

    def test_infonavit_conflicto_real_genera_revision(self):
        payload = {
            "raw_json": {"run_warnings": []},
            "rows": [{
                "asistencia_row_id": 2,
                "nombre_empleado": "MARIA",
                "nss": "222",
                "warnings_json": ["infonavit_no_aplicado_automaticamente:sin_monto_aplicable"],
                "blocks_json": [],
            }],
        }
        rows_by_id = {2: {"id": 2, "row_number": 3, "nombre_empleado": "MARIA", "nss": "222"}}
        observaciones, _ = _build_observaciones_from_payload(payload, rows_by_id, by_nss={}, by_name={})
        self.assertEqual(len(observaciones), 1)
        self.assertEqual(observaciones[0]["nivel"], NIVEL_REVIEW)
        self.assertNotIn("infonavit_sin_registro", observaciones[0]["detalle"])

    def test_informativas_separadas_y_no_bloquean(self):
        obs = [
            {"nivel": NIVEL_INFO, "detalle": "NI", "trabajador": "A"},
            {"nivel": NIVEL_REVIEW, "detalle": "Revision", "trabajador": "B"},
            {"nivel": NIVEL_CRITICAL, "detalle": "Critica", "trabajador": "C"},
        ]
        accionables, informativas = _split_observaciones(obs)
        self.assertEqual(len(informativas), 1)
        self.assertEqual(len(accionables), 2)
        self.assertEqual(accionables[0]["nivel"], NIVEL_CRITICAL)
        resumen = _resumen_observaciones(obs)
        self.assertEqual(resumen[NIVEL_INFO], 1)
        self.assertFalse(resumen[NIVEL_INFO] > 0 and resumen[NIVEL_CRITICAL] == 0 and resumen[NIVEL_REVIEW] == 0)

    def test_salario_critico_incluye_accion_parametros(self):
        payload = {
            "raw_json": {"run_warnings": []},
            "rows": [{
                "asistencia_row_id": 3,
                "nombre_empleado": "CARLOS",
                "nss": "333",
                "parametro_empleado_id": None,
                "warnings_json": [],
                "blocks_json": [WARN_BLOCK_CALC_MISSING_SALARY],
            }],
        }
        rows_by_id = {3: {"id": 3, "row_number": 8, "nombre_empleado": "CARLOS", "nss": "333", "cliente": "Carrier"}}
        observaciones, _ = _build_observaciones_from_payload(payload, rows_by_id, by_nss={}, by_name={})
        self.assertEqual(observaciones[0]["nivel"], NIVEL_CRITICAL)
        self.assertTrue(observaciones[0]["accion_parametros"]["needs_salario"])

    def test_valor_he_critico_incluye_accion_parametros(self):
        payload = {
            "raw_json": {"run_warnings": []},
            "rows": [{
                "asistencia_row_id": 4,
                "nombre_empleado": "DIANA",
                "nss": "444",
                "warnings_json": [],
                "blocks_json": [WARN_BLOCK_CALC_MISSING_VALOR_HE],
            }],
        }
        rows_by_id = {4: {"id": 4, "row_number": 9, "nombre_empleado": "DIANA", "nss": "444"}}
        observaciones, _ = _build_observaciones_from_payload(payload, rows_by_id, by_nss={}, by_name={})
        self.assertTrue(observaciones[0]["accion_parametros"]["needs_valor_he"])

    def test_observacion_incluye_datos_trabajador(self):
        raw = {
            "id": 5,
            "row_number": 91,
            "nombre_empleado": "PEDRO GARCIA",
            "cliente": "CARRIER",
            "planta": "F",
            "puesto": "OPERADOR",
            "nss": "99988877766",
        }
        payload = {
            "asistencia_row_id": 5,
            "nombre_empleado": "PEDRO GARCIA",
            "blocks_json": [WARN_BLOCK_CALC_MISSING_SALARY],
        }
        observaciones, _ = _build_observaciones_from_payload(
            {"raw_json": {"run_warnings": []}, "rows": [payload]},
            {5: raw},
            by_nss={},
            by_name={},
        )
        datos = observaciones[0]["trabajador_datos"]
        self.assertEqual(datos["nombre"], "PEDRO GARCIA")
        self.assertEqual(datos["cliente"], "CARRIER")
        self.assertEqual(datos["planta"], "F")
        self.assertEqual(datos["puesto"], "OPERADOR")
        self.assertEqual(datos["fila"], 91)
        self.assertNotIn("NSS", observaciones[0]["trabajador"])


class TestCalculoPreflightGuardadoParametros(unittest.TestCase):
    def test_guardar_salario_elimina_critica_en_preflight(self):
        import sqlite3

        from modules.nomina.db import ensure_nomina_tables, get_asistencia_import, save_asistencia_import

        db = self._mk_db()
        iso = "2026-06-09 12:00:00"
        import_id = save_asistencia_import(
            db,
            {
                "semana": "S24",
                "fecha_inicio": "2026-06-02",
                "fecha_fin": "2026-06-08",
                "cliente": "CARRIER",
                "clientes": ["CARRIER"],
                "total_rows": 1,
                "rows": [{
                    "row_number": 91,
                    "nombre_empleado": "MARIA LOPEZ",
                    "cliente": "CARRIER",
                    "planta": "F",
                    "puesto": "AUXILIAR LIMPIEZA",
                    "nss": "12345678901",
                    "dia_1_value": "A",
                    "dia_2_value": "A",
                    "dia_3_value": "A",
                    "dia_4_value": "A",
                    "dia_5_value": "A",
                    "dia_6_value": "D",
                    "dia_7_value": "D",
                }],
            },
            created_by=None,
            now_iso=iso,
        )
        imp = get_asistencia_import(db, import_id)
        row_id = int(imp["rows"][0]["id"])

        pre1 = build_calculo_preflight(db, import_id=import_id)
        criticas_antes = [
            o for o in (pre1.get("observaciones_accionables") or [])
            if o.get("codigo") == WARN_BLOCK_CALC_MISSING_SALARY
        ]
        self.assertGreaterEqual(len(criticas_antes), 1)

        ok, msg = save_parametro_from_preflight(
            db,
            asistencia_import_id=import_id,
            asistencia_row_id=row_id,
            salario_operativo=1500.0,
            updated_by=1,
            now_iso=iso,
        )
        self.assertTrue(ok, msg)
        self.assertIn("Preflight actualizado", msg)

        pre2 = build_calculo_preflight(db, import_id=import_id)
        criticas_despues = [
            o for o in (pre2.get("observaciones_accionables") or [])
            if o.get("codigo") == WARN_BLOCK_CALC_MISSING_SALARY
            and (o.get("trabajador_datos") or {}).get("nombre") == "MARIA LOPEZ"
        ]
        self.assertEqual(criticas_despues, [])

    def test_guardar_por_nss_es_legible_por_calc_service(self):
        import sqlite3

        from modules.nomina.calc_service import _match_parametro, _param_index
        from modules.nomina.db import ensure_nomina_tables, get_asistencia_import, list_empleado_parametros, save_asistencia_import

        db = self._mk_db()
        iso = "2026-06-09 12:00:00"
        import_id = save_asistencia_import(
            db,
            {
                "fecha_inicio": "2026-06-02",
                "fecha_fin": "2026-06-08",
                "cliente": "CARRIER",
                "clientes": ["CARRIER"],
                "total_rows": 1,
                "rows": [{
                    "row_number": 10,
                    "nombre_empleado": "JUAN PEREZ",
                    "cliente": "CARRIER",
                    "nss": "55544433322",
                    "dia_1_value": "A",
                    "dia_2_value": "A",
                    "dia_3_value": "A",
                    "dia_4_value": "A",
                    "dia_5_value": "A",
                    "dia_6_value": "D",
                    "dia_7_value": "D",
                }],
            },
            created_by=None,
            now_iso=iso,
        )
        row = get_asistencia_import(db, import_id)["rows"][0]
        row_id = int(row["id"])
        ok, _ = save_parametro_from_preflight(
            db,
            asistencia_import_id=import_id,
            asistencia_row_id=row_id,
            salario_operativo=980.0,
            updated_by=1,
            now_iso=iso,
        )
        self.assertTrue(ok)
        by_nss, by_name = _param_index(list_empleado_parametros(db, limit=100))
        matched = _match_parametro(row, by_nss, by_name)
        self.assertIsNotNone(matched)
        self.assertGreater(float(matched.get("salario_operativo") or 0), 0)

    @staticmethod
    def _mk_db() -> str:
        import sqlite3
        import tempfile

        from modules.nomina.db import ensure_nomina_tables

        fd, path = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        conn = sqlite3.connect(path)
        ensure_nomina_tables(conn)
        conn.commit()
        conn.close()
        return path


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
