"""GIS Nóminas — importación multihoja y extracción."""

from __future__ import annotations

import sqlite3
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook

from modules.gestion_idse_sua.nominas.import_service import (
    confirm_classifications,
    confirm_period,
    extract_sheet_workers,
    inspect_workbook,
    register_import,
)
from modules.gestion_idse_sua.nominas.schema import ensure_gis_nominas_tables


def _build_multisheet_bytes() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "3 al 9 jun"
    ws["A2"] = "NOMINA"
    ws["B3"] = "NO."
    ws["C3"] = "NOMBRE DE EMPLEADO"
    ws["D3"] = "PLANTA"
    ws["E3"] = "PUESTO"
    ws["F3"] = "CUENTA"
    ws["B4"] = "101"
    ws["C4"] = "JUAN PEREZ LOPEZ"
    ws["D4"] = "FLOTADO"
    ws["E4"] = "Operador"
    ws["F4"] = "123"
    aux = wb.create_sheet("Auxiliar")
    aux["A1"] = "Totales generales"
    hidden = wb.create_sheet("Oculta")
    hidden["A1"] = "NOMBRE DE EMPLEADO"
    hidden.sheet_state = "hidden"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "gis_import.db"
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    ensure_gis_nominas_tables(connection)
    connection.commit()
    yield connection
    connection.close()


def test_inspect_multisheet_workbook():
    data = inspect_workbook(_build_multisheet_bytes(), filename="nomina.xlsx")
    assert len(data["sheets"]) == 3
    nomina = data["sheets"][0]
    assert nomina["suggested_classification"] == "nomina"
    assert nomina["estimated_rows"] == 1
    assert data["sheets"][1]["suggested_classification"] == "auxiliar"


def test_register_import_persists_sheets(conn):
    payload = register_import(conn, file_bytes=_build_multisheet_bytes(), filename="nomina.xlsx", uploaded_by="tester")
    sheets = conn.execute("SELECT COUNT(*) FROM gis_nomina_sheets WHERE import_id = ?", (payload["import_id"],)).fetchone()[0]
    assert sheets == 3


def test_extract_workers_from_fixture(conn):
    fixture = Path("tests/fixtures/nomina_carrier_anon.xlsx").read_bytes()
    reg = register_import(conn, file_bytes=fixture, filename="carrier.xlsx", uploaded_by="tester")
    sheet_id = conn.execute(
        "SELECT id FROM gis_nomina_sheets WHERE import_id = ? AND suggested_classification = 'nomina'",
        (reg["import_id"],),
    ).fetchone()[0]
    conn.execute(
        "UPDATE gis_nomina_sheets SET confirmed_classification = 'nomina' WHERE id = ?",
        (sheet_id,),
    )
    confirm_period(conn, sheet_id, fecha_inicio="01/06/2026", fecha_fin="07/06/2026")
    out = extract_sheet_workers(conn, file_bytes=fixture, sheet_id=sheet_id, headcount_rows=[])
    assert out["workers_count"] == 4


def test_duplicate_period_warns(conn):
    from modules.gestion_idse_sua.nominas.repository import find_conflicting_periods

    conn.execute(
        "INSERT INTO gis_nomina_imports (original_filename, file_hash, uploaded_at, status) VALUES (?,?,?,?)",
        ("a.xlsx", "h1", "2026-01-01", "classified"),
    )
    conn.execute(
        "INSERT INTO gis_nomina_sheets (import_id, sheet_index, sheet_name, is_hidden, confirmed_classification, estimated_rows) VALUES (1,0,'S1',0,'nomina',1)"
    )
    conn.execute(
        "INSERT INTO gis_nomina_sheets (import_id, sheet_index, sheet_name, is_hidden, confirmed_classification, estimated_rows) VALUES (1,1,'S2',0,'nomina',1)"
    )
    confirm_period(conn, 1, fecha_inicio="01/06/2026", fecha_fin="07/06/2026")
    conflicts = find_conflicting_periods(conn, fecha_inicio="01/06/2026", fecha_fin="07/06/2026", exclude_sheet_id=2)
    assert len(conflicts) == 1
    out = confirm_period(conn, 2, fecha_inicio="01/06/2026", fecha_fin="07/06/2026")
    assert out["conflicts"]
