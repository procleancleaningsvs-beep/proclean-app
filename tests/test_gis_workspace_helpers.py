"""GIS — helpers de tabla operativa."""

from __future__ import annotations

from modules.gestion_idse_sua.nominas.ui_helpers import build_weekly_workspace_rows, period_day_headers


def test_shared_excel_table_component_is_loaded_by_attendance_hub():
    shared = open("static/shared/excel_table.js", encoding="utf-8").read()
    hub = open("templates/nomina/index.html", encoding="utf-8").read()
    visualizer = open("templates/nomina/_asistencia_hub_visualizer.html", encoding="utf-8").read()
    assert "ProCleanExcelTable" in shared
    assert "uniqueValues" in shared
    assert "data-select-all" in shared
    assert "data-sort" in shared
    assert "shared/excel_table.js" in hub
    assert "ProCleanExcelTable.normalize" in visualizer


def test_period_day_headers_real_dates():
    headers = period_day_headers("10/07/2026")
    assert len(headers) == 7
    assert headers[0]["label"].startswith("Vie 10 jul")
    assert headers[0]["fecha_iso"] == "2026-07-10"


def test_build_weekly_workspace_rows_merges_result_and_attendance():
    workers = [
        {
            "id": 1,
            "num_empleado": "101",
            "nombre_normalizado": "JUAN",
            "planta_normalizada": "A",
            "cliente_confirmado": "PEPSI",
            "match": {"status": "confirmed", "hc_nombre": "JUAN HC", "nss": "111"},
        }
    ]
    results = [
        {
            "id": 9,
            "worker_id": 1,
            "resultado": "Coincidencia",
            "decision_final": "Coincidencia",
            "tipo_sugerido": "",
            "conversion_status": "none",
        }
    ]
    attendance = [
        {
            "worker_id": 1,
            "column_index": 1,
            "code_normalized": "A",
            "code_original": "A",
            "fecha_iso": "2026-07-10",
            "id": 1,
            "num_empleado": "101",
            "nombre_normalizado": "JUAN",
            "interpretation_status": "ok",
        }
    ]
    rows = build_weekly_workspace_rows(
        workers=workers,
        results=results,
        attendance_rows=attendance,
        client_inferences={1: {"cliente": "PEPSI", "source": "confirmed", "confidence": 1.0}},
        trajectory_payload=None,
    )
    assert len(rows) == 1
    assert rows[0]["resultado"] == "Coincidencia"
    assert rows[0]["result_badge"] == "coincidencia"
    assert rows[0]["days"][1] == "A"
    assert rows[0]["display_name"] == "JUAN HC"
    assert rows[0]["name_badge"] == ""


def test_weekly_workspace_template_uses_unified_operational_table():
    template = open("templates/gestion_idse_sua/nominas/workspace.html", encoding="utf-8").read()
    css = open("static/gestion_idse_sua/workspace_table.css", encoding="utf-8").read()
    javascript = open("static/gestion_idse_sua/workspace_table.js", encoding="utf-8").read()
    assert "Nombre completo" in template
    assert "Nombre HC</th>" not in template
    assert "Match</th>" not in template
    assert "data-toggle-detail" in template
    assert "gis-ws-modal" in template
    assert "data-excel-filter" in template
    assert "Movimientos sugeridos" in template
    assert "grid-template-columns: minmax(0, 3fr) minmax(250px, 1fr)" in css
    assert "ProCleanExcelTable" in javascript


def test_unmatched_weekly_name_keeps_payroll_identity():
    rows = build_weekly_workspace_rows(
        workers=[
            {
                "id": 2,
                "num_empleado": "102",
                "nombre_normalizado": "NORA RIVERA",
                "planta_normalizada": "A",
                "match": {"status": "unmatched"},
            }
        ],
        results=[
            {
                "id": 10,
                "worker_id": 2,
                "resultado": "Posible alta",
                "decision_final": "Posible alta",
            }
        ],
        attendance_rows=[],
        client_inferences={},
        trajectory_payload=None,
    )
    assert rows[0]["display_name"] == "NORA RIVERA"
    assert rows[0]["name_badge"] == "Sin match"
