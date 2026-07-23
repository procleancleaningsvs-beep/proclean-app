"""GIS — helpers de tabla operativa."""

from __future__ import annotations

from modules.gestion_idse_sua.nominas.ui_helpers import build_weekly_workspace_rows, period_day_headers


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
