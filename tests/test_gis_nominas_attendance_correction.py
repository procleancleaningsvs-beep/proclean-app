"""GIS Nóminas — manual attendance correction audit."""

from __future__ import annotations

import sqlite3

import pytest

from modules.gestion_idse_sua.nominas.attendance_service import correct_attendance_code
from modules.gestion_idse_sua.nominas.repository import insert_attendance, list_attendance_corrections
from modules.gestion_idse_sua.nominas.schema import ensure_gis_nominas_tables


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "corr.db")
    connection.row_factory = sqlite3.Row
    ensure_gis_nominas_tables(connection)
    connection.execute(
        "INSERT INTO gis_nomina_imports (original_filename, file_hash, uploaded_at, status) VALUES (?,?,?,?)",
        ("f.xlsx", "h", "2026-01-01", "extracted"),
    )
    connection.execute(
        "INSERT INTO gis_nomina_sheets (import_id, sheet_index, sheet_name, is_hidden, confirmed_classification, estimated_rows) VALUES (1,0,'S',0,'nomina',1)"
    )
    connection.execute(
        "INSERT INTO gis_nomina_periods (sheet_id, fecha_inicio, fecha_fin, semana_num, detection_source, user_confirmed) VALUES (1,'01/06/2026','07/06/2026',23,'manual',1)"
    )
    connection.execute(
        """
        INSERT INTO gis_nomina_workers
        (period_id, row_number, num_empleado, nombre_original, nombre_normalizado, planta_normalizada)
        VALUES (1,7,'9001','Demo','DEMO','P')
        """
    )
    yield connection
    connection.close()


def test_manual_correction_preserves_original(conn):
    att_id = insert_attendance(
        conn,
        {
            "worker_id": 1,
            "period_id": 1,
            "column_index": 1,
            "column_number": 11,
            "fecha_iso": "2026-06-01",
            "header_original": "J2",
            "code_original": "X",
            "code_normalized": "",
            "interpretation_status": "review",
            "warning": None,
            "created_at": "2026-01-01",
            "updated_at": "2026-01-01",
        },
    )
    conn.commit()
    correct_attendance_code(
        conn,
        attendance_id=att_id,
        code_corrected="A",
        corrected_by="tester",
        reason="Verificado con supervisor",
    )
    row = conn.execute("SELECT code_original, code_normalized, interpretation_status FROM gis_nomina_attendance WHERE id = ?", (att_id,)).fetchone()
    corrections = list_attendance_corrections(conn, att_id)
    assert row["code_original"] == "X"
    assert row["code_normalized"] == "A"
    assert row["interpretation_status"] == "corrected"
    assert corrections[0]["code_original"] == "X"
    assert corrections[0]["code_corrected"] == "A"
    assert corrections[0]["corrected_by"] == "tester"
