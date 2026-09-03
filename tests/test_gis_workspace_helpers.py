"""GIS — helpers de tabla operativa."""

from __future__ import annotations

import json

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
    assert rows[0]["identity_status"] == "Confirmado"
    assert rows[0]["name_badge"] == "Confirmado"


def test_weekly_workspace_template_uses_unified_operational_table():
    template = open("templates/gestion_idse_sua/nominas/workspace.html", encoding="utf-8").read()
    routes = open("modules/gestion_idse_sua/routes_nominas.py", encoding="utf-8").read()
    css = open("static/gestion_idse_sua/workspace_table.css", encoding="utf-8").read()
    javascript = open("static/gestion_idse_sua/workspace_table.js", encoding="utf-8").read()
    assert "Nombre completo" in template
    assert "Nombre HC</th>" not in template
    assert "Match</th>" not in template
    assert "data-toggle-detail" in template
    assert "gis-ws-modal" in template
    assert "data-excel-filter" in template
    assert "Movimientos sugeridos" in template
    assert "Movimiento manual" not in template
    assert "Movimiento final" in template
    assert ">Cambiar</summary>" in template
    assert "manual-movement-" in template
    assert "row.identity_status" in template
    assert "Candidatos Headcount" in template
    assert '(search.get("opciones") or [None])[0]' not in routes
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
                "cliente_confirmado": "PEPSI",
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
    assert rows[0]["identity_status"] == "Sin coincidencias"
    assert rows[0]["name_badge"] == "Sin coincidencias"


def test_weekly_worker_without_comparative_still_has_operational_result():
    rows = build_weekly_workspace_rows(
        workers=[
            {
                "id": 3,
                "nombre_normalizado": "FELIPE SILOS",
                "cliente_confirmado": "AURIGA",
                "match": {"status": "unmatched"},
            }
        ],
        results=[],
        attendance_rows=[],
        client_inferences={},
        trajectory_payload=None,
    )

    assert rows[0]["identity_status"] == "Sin coincidencias"
    assert rows[0]["resultado"] == "Posible alta"


def test_weekly_review_exposes_sanitized_candidate_evidence():
    candidate = {
        "nombre_completo": "GABRIEL VARGAS JIMENEZ",
        "cliente": "AURIGA",
        "ubicacion": "DIA",
        "nss": "11111111111",
        "candidate_reason": "Mismos componentes del nombre en orden distinto",
    }
    rows = build_weekly_workspace_rows(
        workers=[
            {
                "id": 4,
                "nombre_normalizado": "GABRIEL JIMENEZ VARGAS",
                "cliente_confirmado": "AURIGA",
                "match": {
                    "status": "review",
                    "match_method": "candidato_nombre",
                    "hc_json": json.dumps([candidate]),
                },
            }
        ],
        results=[{"id": 12, "worker_id": 4, "resultado": "Revisión"}],
        attendance_rows=[],
        client_inferences={},
        trajectory_payload=None,
    )

    assert rows[0]["identity_status"] == "Posible coincidencia"
    assert rows[0]["resultado"] == "Revisión"
    assert rows[0]["match_candidates"] == [{**candidate, "candidate_index": 0}]


def test_weekly_workspace_template_supports_candidate_choice_and_rejection():
    template = open("templates/gestion_idse_sua/nominas/workspace.html", encoding="utf-8").read()
    assert 'name="candidate_index"' in template
    assert 'value="confirm_candidate"' in template
    assert "Confirmar identidad" in template
    assert 'value="reject_candidates"' in template
    assert "Ninguna corresponde" in template
