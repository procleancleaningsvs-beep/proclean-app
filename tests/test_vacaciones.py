"""Pruebas QA del módulo Nóminas > Vacaciones."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import date
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook

from modules.finiquitos.calc import calcular_dias_vacaciones_devengados as fin_devengados
from modules.nomina.db import (
    archive_vacaciones_import_empleados,
    ensure_nomina_tables,
    get_vacaciones_empleado,
    list_vacaciones_empleados_all,
    list_vacaciones_eventos,
    save_vacaciones_events,
    save_vacaciones_import,
    validar_vacaciones_base,
)
from modules.nomina.vacaciones_excel import _to_date_iso, _to_days_value, parse_vacaciones_historico_excel
from modules.nomina.vacaciones_logic import (
    aplicar_calculo_a_fila,
    build_migration_events_from_row,
    calcular_balance_vacaciones_trabajador,
    detect_headcount_diff_warnings,
)
from modules.shared.vacaciones import calcular_dias_vacaciones_devengados, dias_vacaciones_ley_por_anio_servicio


CARRIER_XLSX = Path(r"c:\Users\Yahir\Downloads\vacaciones actualizadas carrier.xlsx")


class TestVacacionesExcel(unittest.TestCase):
    def test_fecha_texto_dd_mm_yyyy(self):
        self.assertEqual(_to_date_iso("01/03/2024"), "2024-03-01")

    def test_fecha_serial_excel(self):
        self.assertEqual(_to_date_iso(45352), "2024-03-01")

    def test_vacaciones_laboradas_si(self):
        self.assertEqual(_to_days_value("SI"), 1.0)

    def test_vacaciones_laboradas_uno(self):
        self.assertEqual(_to_days_value(1), 1.0)

    def test_dias_pagados_vacio(self):
        self.assertEqual(_to_days_value(None), 0.0)

    def test_carrier_excel_72_rows(self):
        if not CARRIER_XLSX.exists():
            self.skipTest("Archivo Carrier no disponible en entorno")
        parsed = parse_vacaciones_historico_excel(CARRIER_XLSX.read_bytes(), CARRIER_XLSX.name)
        self.assertEqual(len(parsed.rows), 72)
        self.assertEqual(len(parsed.errors), 0)


class TestVacacionesLogic(unittest.TestCase):
    def test_tabla_lft_coincide_finiquitos(self):
        self.assertEqual(dias_vacaciones_ley_por_anio_servicio(1), 12)
        self.assertEqual(dias_vacaciones_ley_por_anio_servicio(5), 20)
        self.assertEqual(dias_vacaciones_ley_por_anio_servicio(6), 22)

    def test_devengados_misma_logica_finiquitos(self):
        ingreso = date(2024, 3, 1)
        corte = date(2026, 5, 20)
        shared = calcular_dias_vacaciones_devengados(ingreso, corte)
        finiq = fin_devengados(ingreso, corte)
        self.assertEqual(float(shared["dias_vac_total_dev"]), float(finiq["dias_vac_total_dev"]))

    def test_saldo_negativo_warning(self):
        row = {
            "fecha_ingreso_usada": "2024-03-01",
            "dias_utilizados": 20,
            "vacaciones_laboradas": 0,
            "dias_pagados": 0,
            "sueldo_usado": 300,
            "warnings": [],
        }
        calc = calcular_balance_vacaciones_trabajador(row, fecha_corte=date(2024, 6, 1))
        self.assertLess(calc["saldo_calculado"], 0)
        self.assertTrue(any("negativo" in w.lower() for w in calc["warnings"]))

    def test_comentario_reingreso_warning(self):
        row = {
            "fecha_ingreso_historica": "2020-01-01",
            "fecha_ingreso_headcount": "2024-01-01",
            "comentarios": "reingreso en enero",
        }
        warnings = detect_headcount_diff_warnings(row)
        self.assertTrue(any("reingreso" in w.lower() for w in warnings))

    def test_headcount_prevalece_en_calculo(self):
        row = {
            "fecha_ingreso_historica": "2020-01-01",
            "fecha_ingreso_headcount": "2024-03-01",
            "fecha_ingreso_usada": "2024-03-01",
            "sueldo_historico": 100,
            "sueldo_headcount": 328.57,
            "sueldo_usado": 328.57,
            "dias_utilizados": 0,
            "vacaciones_laboradas": 0,
            "dias_pagados": 0,
            "warnings": [],
        }
        enriched = aplicar_calculo_a_fila(row, fecha_corte=date(2025, 5, 20))
        self.assertEqual(enriched["sueldo_usado"], 328.57)
        self.assertIsNotNone(enriched["dias_generados"])

    def test_eventos_migracion(self):
        row = {
            "id": 1,
            "nss": "123",
            "nombre_normalizado": "TEST USER",
            "dias_utilizados": 2,
            "vacaciones_laboradas": 1,
            "dias_pagados": 3,
            "prima_2026_pagada": True,
            "fecha_pago_prima_2026": "2026-01-15",
            "comentarios": "reingreso",
            "saldo_calculado": -1,
            "fecha_ingreso_usada": "2024-01-01",
        }
        events = build_migration_events_from_row(row, import_batch_id=9, imported_from_file="test.xlsx", created_at="2026-01-01")
        types = {e["event_type"] for e in events}
        self.assertIn("vacaciones_disfrutadas", types)
        self.assertIn("vacaciones_laboradas", types)
        self.assertIn("reinicio_reingreso", types)


class TestVacacionesDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        conn = sqlite3.connect(self.db_path)
        try:
            ensure_nomina_tables(conn)
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _sample_row(self, **overrides):
        base = {
            "nss": "",
            "nombre_historico": "Juan Pérez",
            "nombre_normalizado": "JUAN PEREZ",
            "nombre_headcount": "Juan Pérez",
            "cliente": "Carrier",
            "planta_historica": "A",
            "planta_headcount": "A",
            "fecha_ingreso_historica": "2024-03-01",
            "fecha_ingreso_headcount": "2024-03-01",
            "fecha_ingreso_usada": "2024-03-01",
            "estatus_headcount": "ACTIVO",
            "sueldo_historico": 300.0,
            "sueldo_headcount": 300.0,
            "sueldo_usado": 300.0,
            "dias_vacaciones_historico": 12.0,
            "dias_generados": 12.0,
            "dias_utilizados": 0.0,
            "vacaciones_laboradas": 0.0,
            "dias_pagados": 0.0,
            "dias_restantes_historico": 12.0,
            "dias_restantes_calculado": 12.0,
            "saldo_calculado": 12.0,
            "prima_pendiente": 0.0,
            "prima_2025_pagada": False,
            "semana_pago_prima_2025": "",
            "prima_2026_pagada": False,
            "fecha_pago_prima_2026": "",
            "monto_total_historico": None,
            "monto_total_recalculado": None,
            "comentarios": "",
            "match_status": "match_name",
            "match_score": 0.92,
            "headcount_source": "headcount",
            "headcount_raw_status": "ALTA|ACTIVO",
            "warnings": [],
            "editable_json": {"revision_status": "pending_revision"},
            "is_active": 1,
        }
        base.update(overrides)
        return base

    def test_import_y_eventos(self):
        row = self._sample_row()
        import_id = save_vacaciones_import(
            self.db_path,
            {
                "cliente": "Carrier",
                "source_filename": "test.xlsx",
                "file_hash": "abc",
                "total_rows": 1,
                "matched_count": 1,
                "warning_count": 0,
                "error_count": 0,
                "rows": [row],
                "raw_json": {},
            },
            created_by=None,
            now_iso="2026-05-20 10:00:00",
        )
        saved = list_vacaciones_empleados_all(self.db_path, import_id=import_id)[0]
        events = build_migration_events_from_row(
            saved,
            import_batch_id=import_id,
            imported_from_file="test.xlsx",
            created_at="2026-05-20 10:00:00",
        )
        for ev in events:
            ev["empleado_id"] = saved["id"]
        save_vacaciones_events(self.db_path, events, created_by=None)
        listed = list_vacaciones_eventos(self.db_path, empleado_id=saved["id"])
        self.assertGreaterEqual(len(listed), 1)

    def test_archivar_no_delete(self):
        row = self._sample_row()
        import_id = save_vacaciones_import(
            self.db_path,
            {
                "cliente": "Carrier",
                "source_filename": "test.xlsx",
                "file_hash": "abc2",
                "total_rows": 1,
                "matched_count": 1,
                "warning_count": 0,
                "error_count": 0,
                "rows": [row],
                "raw_json": {},
            },
            created_by=None,
            now_iso="2026-05-20 10:00:00",
        )
        archived = archive_vacaciones_import_empleados(self.db_path, import_id=import_id)
        self.assertEqual(archived, 1)
        active = list_vacaciones_empleados_all(self.db_path, import_id=import_id, include_inactive=False)
        all_rows = list_vacaciones_empleados_all(self.db_path, import_id=import_id, include_inactive=True)
        self.assertEqual(len(active), 0)
        self.assertEqual(len(all_rows), 1)

    def test_validar_base(self):
        save_vacaciones_import(
            self.db_path,
            {
                "cliente": "Carrier",
                "source_filename": "neg.xlsx",
                "file_hash": "abc3",
                "total_rows": 1,
                "matched_count": 0,
                "warning_count": 1,
                "error_count": 0,
                "rows": [
                    self._sample_row(
                        match_status="no_match",
                        saldo_calculado=-2,
                        dias_restantes_calculado=-2,
                        warnings=["Saldo negativo detectado"],
                    )
                ],
                "raw_json": {},
            },
            created_by=None,
            now_iso="2026-05-20 10:00:00",
        )
        resumen = validar_vacaciones_base(self.db_path)
        self.assertGreaterEqual(resumen["saldos_negativos"], 1)
        self.assertGreaterEqual(resumen["conflictos_match"], 1)


if __name__ == "__main__":
    unittest.main()
