"""Pruebas QA del módulo Nóminas > Vacaciones."""

from __future__ import annotations

import math
import os
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from modules.finiquitos.calc import calcular_dias_vacaciones_devengados as fin_devengados
from modules.nomina.db import (
    archive_all_active_vacaciones_data,
    ensure_nomina_tables,
    ejecutar_limpieza_base_vacaciones,
    list_vacaciones_empleados_all,
    list_vacaciones_eventos,
    preview_limpieza_vacaciones_base,
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
from modules.nomina.vacaciones_util import (
    MATCH_OK,
    SIN_MATCH,
    enrich_vacaciones_row_for_display,
    resolve_status_headcount,
    sanitize_display_value,
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
        self.assertGreater(parsed.weekly_events_total, 0)

    def test_antonia_desglose_semanal(self):
        if not CARRIER_XLSX.exists():
            self.skipTest("Archivo Carrier no disponible")
        parsed = parse_vacaciones_historico_excel(CARRIER_XLSX.read_bytes(), CARRIER_XLSX.name)
        antonia = next(r for r in parsed.rows if "Antonia" in r["excel_nombre_original"])
        self.assertGreaterEqual(len(antonia["desglose_semanal"]), 2)
        self.assertEqual(antonia["dias_utilizados_calculado_semanal"], 24.0)
        self.assertEqual(antonia["dias_utilizados_excel_resumen"], 24.0)


class TestVacacionesUtil(unittest.TestCase):
    def test_sanitize_nan(self):
        self.assertEqual(sanitize_display_value(float("nan")), "")
        self.assertEqual(sanitize_display_value("nan"), "")
        self.assertEqual(sanitize_display_value(None), "")

    def test_status_headcount_sin_status(self):
        self.assertEqual(resolve_status_headcount(None), "SIN STATUS HEADCOUNT")
        self.assertEqual(resolve_status_headcount({}), "SIN STATUS HEADCOUNT")
        self.assertEqual(resolve_status_headcount({"status_imss": float("nan")}), "SIN STATUS HEADCOUNT")

    def test_status_headcount_activo(self):
        self.assertEqual(resolve_status_headcount({"status_operacion": "ALTA"}), "ACTIVO")

    def test_display_no_nan_match(self):
        row = enrich_vacaciones_row_for_display(
            {
                "estatus_headcount": float("nan"),
                "match_status": "match_name",
                "match_score": float("nan"),
                "nombre_historico": "Test",
            }
        )
        self.assertEqual(row["status_headcount"], "SIN STATUS HEADCOUNT")
        self.assertEqual(row["match_status_display"], "MATCH_OK")
        self.assertNotIn("nan", row["status_headcount"].lower())


class TestVacacionesLogic(unittest.TestCase):
    def test_tabla_lft_coincide_finiquitos(self):
        self.assertEqual(dias_vacaciones_ley_por_anio_servicio(1), 12)
        self.assertEqual(dias_vacaciones_ley_por_anio_servicio(5), 20)

    def test_devengados_misma_logica_finiquitos(self):
        ingreso = date(2024, 3, 1)
        corte = date(2026, 5, 20)
        shared = calcular_dias_vacaciones_devengados(ingreso, corte)
        finiq = fin_devengados(ingreso, corte)
        self.assertEqual(float(shared["dias_vac_total_dev"]), float(finiq["dias_vac_total_dev"]))

    def test_saldo_usa_desglose_semanal(self):
        row = {
            "fecha_ingreso_usada": "2024-03-01",
            "dias_utilizados_calculado_semanal": 10,
            "dias_utilizados_excel_resumen": 8,
            "vacaciones_laboradas": 0,
            "dias_pagados": 0,
            "warnings": [],
        }
        calc = calcular_balance_vacaciones_trabajador(row, fecha_corte=date(2025, 5, 20))
        self.assertEqual(calc["dias_utilizados"], 10)
        self.assertTrue(any("semanal" in w.lower() for w in calc["warnings"]))

    def test_eventos_semanales(self):
        row = {
            "id": 1,
            "nombre_normalizado": "TEST",
            "desglose_semanal": [
                {"period_label": "9 AL 15 MAY (SEM 19)", "days": 6, "anio": "2024", "excel_row": 3},
                {"period_label": "16 AL 22 MAY (SEM 20)", "days": 6, "anio": "2024", "excel_row": 3},
            ],
            "dias_utilizados": 12,
            "warnings": [],
        }
        events = build_migration_events_from_row(row, import_batch_id=1, imported_from_file="t.xlsx", created_at="2026-01-01")
        sem = [e for e in events if e["event_type"] == "vacaciones_tomadas"]
        self.assertEqual(len(sem), 2)
        self.assertNotIn("vacaciones_disfrutadas", {e["event_type"] for e in events})


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
            "estatus_headcount": "ACTIVO",
            "status_headcount": "ACTIVO",
            "fecha_ingreso_usada": "2024-03-01",
            "sueldo_usado": 300.0,
            "dias_generados": 12.0,
            "dias_utilizados": 0.0,
            "dias_utilizados_calculado_semanal": 0.0,
            "dias_utilizados_excel_resumen": 0.0,
            "vacaciones_laboradas": 0.0,
            "dias_pagados": 0.0,
            "saldo_calculado": 12.0,
            "match_status": MATCH_OK,
            "match_method": "nombre_completo",
            "match_notes": "",
            "warnings": [],
            "editable_json": {"revision_status": "pending_revision", "desglose_semanal": []},
            "is_active": 1,
        }
        base.update(overrides)
        return base

    def test_limpieza_base_con_backup(self):
        save_vacaciones_import(
            self.db_path,
            {
                "cliente": "Carrier",
                "source_filename": "test.xlsx",
                "file_hash": "abc",
                "total_rows": 1,
                "matched_count": 1,
                "warning_count": 0,
                "error_count": 0,
                "rows": [self._sample_row()],
                "raw_json": {},
            },
            created_by=None,
            now_iso="2026-05-20 10:00:00",
        )
        preview = preview_limpieza_vacaciones_base(self.db_path)
        self.assertEqual(preview["total_registros"], 1)
        result = ejecutar_limpieza_base_vacaciones(self.db_path, created_by=1, now_iso="2026-05-20 11:00:00")
        self.assertGreater(result["backup_id"], 0)
        self.assertEqual(preview_limpieza_vacaciones_base(self.db_path)["total_registros"], 0)
        active = list_vacaciones_empleados_all(self.db_path, include_inactive=False)
        self.assertEqual(len(active), 0)

    def test_reimport_no_duplica_activos(self):
        import_id = save_vacaciones_import(
            self.db_path,
            {
                "cliente": "Carrier",
                "source_filename": "a.xlsx",
                "file_hash": "1",
                "total_rows": 1,
                "matched_count": 1,
                "warning_count": 0,
                "error_count": 0,
                "rows": [self._sample_row()],
                "raw_json": {},
            },
            created_by=None,
            now_iso="2026-05-20 10:00:00",
        )
        archived = archive_all_active_vacaciones_data(self.db_path)
        self.assertEqual(archived["empleados"], 1)
        save_vacaciones_import(
            self.db_path,
            {
                "cliente": "Carrier",
                "source_filename": "b.xlsx",
                "file_hash": "2",
                "total_rows": 1,
                "matched_count": 1,
                "warning_count": 0,
                "error_count": 0,
                "rows": [self._sample_row(nombre_historico="Otro")],
                "raw_json": {},
            },
            created_by=None,
            now_iso="2026-05-20 11:00:00",
        )
        active = list_vacaciones_empleados_all(self.db_path, include_inactive=False)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["nombre_historico"], "Otro")

    def test_sin_match_no_rompe(self):
        save_vacaciones_import(
            self.db_path,
            {
                "cliente": "Carrier",
                "source_filename": "nomatch.xlsx",
                "file_hash": "x",
                "total_rows": 1,
                "matched_count": 0,
                "warning_count": 1,
                "error_count": 0,
                "rows": [self._sample_row(match_status=SIN_MATCH, status_headcount="SIN STATUS HEADCOUNT")],
                "raw_json": {},
            },
            created_by=None,
            now_iso="2026-05-20 10:00:00",
        )
        resumen = validar_vacaciones_base(self.db_path)
        self.assertGreaterEqual(resumen["conflictos_match"], 1)


if __name__ == "__main__":
    unittest.main()
