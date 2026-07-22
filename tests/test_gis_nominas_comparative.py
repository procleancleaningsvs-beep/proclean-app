"""GIS Nóminas — comparativo manual."""

from __future__ import annotations

import sqlite3

import pytest

from modules.gestion_idse_sua.nominas.comparative_service import run_comparative
from modules.gestion_idse_sua.nominas.match_service import match_worker
from modules.gestion_idse_sua.nominas.repository import insert_workers, upsert_match, upsert_period
from modules.gestion_idse_sua.nominas.schema import ensure_gis_nominas_tables


HC = [
    {
        "nombre_completo": "JUAN PEREZ LOPEZ",
        "cliente": "PEPSI",
        "nss": "11111111111",
        "numero_empleado": "101",
        "rfc_homoclave": "PELJ800101XXX",
        "curp": "PELJ800101HDFRNN09",
        "apellido_paterno": "PEREZ",
        "apellido_materno": "LOPEZ",
        "nombre": "JUAN",
        "sueldo_diario": 500,
        "patron": "12345678901",
    },
    {
        "nombre_completo": "MARIA SOLO HEADCOUNT",
        "cliente": "PEPSI",
        "nss": "22222222222",
    },
]


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "cmp.db")
    connection.row_factory = sqlite3.Row
    ensure_gis_nominas_tables(connection)
    connection.execute(
        "INSERT INTO gis_nomina_imports (original_filename, file_hash, uploaded_at, status) VALUES (?,?,?,?)",
        ("f.xlsx", "h", "2026-01-01", "extracted"),
    )
    connection.execute(
        "INSERT INTO gis_nomina_sheets (import_id, sheet_index, sheet_name, is_hidden, confirmed_classification, estimated_rows) VALUES (1,0,'S',0,'nomina',2)"
    )
    period_id = upsert_period(
        connection,
        1,
        {
            "fecha_inicio": "01/06/2026",
            "fecha_fin": "07/06/2026",
            "semana_num": 23,
            "source": "manual",
            "cut_warning": None,
        },
        confirmed=True,
    )
    insert_workers(
        connection,
        period_id,
        [
            {
                "row_number": 4,
                "num_empleado": "101",
                "nombre_original": "Juan",
                "nombre_normalizado": "JUAN PEREZ LOPEZ",
                "puesto": "Op",
                "planta_original": "A",
                "planta_normalizada": "A",
                "cuenta": "1",
                "row_json": "{}",
            },
            {
                "row_number": 5,
                "num_empleado": "999",
                "nombre_original": "Nuevo",
                "nombre_normalizado": "NUEVO EMPLEADO",
                "puesto": "Op",
                "planta_original": "A",
                "planta_normalizada": "A",
                "cuenta": "2",
                "row_json": "{}",
            },
        ],
    )
    workers = connection.execute("SELECT id, num_empleado, nombre_normalizado FROM gis_nomina_workers").fetchall()
    for w in workers:
        m = match_worker(dict(w), HC)
        upsert_match(connection, int(w["id"]), m)
    connection.commit()
    yield connection, period_id
    connection.close()


def test_comparative_manual_produces_semaforo(conn):
    connection, period_id = conn
    out = run_comparative(connection, period_id=period_id, cliente="PEPSI", generated_by="test", headcount_rows=HC)
    rows = connection.execute(
        "SELECT resultado, semaforo, tipo_sugerido FROM gis_nomina_results WHERE comparative_id = ?",
        (out["comparative_id"],),
    ).fetchall()
    resultados = {r["resultado"]: r["semaforo"] for r in rows}
    assert resultados.get("Coincidencia") == "azul"
    assert resultados.get("Posible alta") == "verde"
    assert resultados.get("Posible baja") == "rojo"
    assert out["totals"]["coincidencias"] >= 1
    assert out["totals"]["bajas"] >= 1


def test_homonym_goes_to_review():
    hc = [
        {"nombre_completo": "JUAN PEREZ LOPEZ", "cliente": "A", "nss": "1"},
        {"nombre_completo": "JUAN PEREZ LOPEZ", "cliente": "B", "nss": "2"},
    ]
    match = match_worker({"nombre_normalizado": "JUAN PEREZ LOPEZ"}, hc)
    assert match["status"] == "review"
