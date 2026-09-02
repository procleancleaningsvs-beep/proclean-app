"""GIS Nóminas — comparativo manual."""

from __future__ import annotations

import sqlite3

import pytest

from modules.gestion_idse_sua.nominas.comparative_service import run_comparative
from modules.gestion_idse_sua.nominas.match_service import match_worker
from modules.gestion_idse_sua.nominas.repository import (
    insert_attendance,
    insert_workers,
    list_workspace_audit,
    set_result_visibility,
    update_result_decision,
    upsert_match,
    upsert_period,
)
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
    connection.execute(
        "UPDATE gis_nomina_workers SET cliente_confirmado = 'PEPSI' WHERE period_id = ?",
        (period_id,),
    )
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


def test_comparative_isolates_workers_and_bajas_by_confirmed_client(conn):
    connection, period_id = conn
    carrier_hc = [
        {
            "nombre_completo": "CARLA CARRIER ACTIVA",
            "cliente": "CARRIER",
            "numero_empleado": "201",
            "nss": "33333333333",
        },
        {
            "nombre_completo": "CAMILA CARRIER AUSENTE",
            "cliente": "CARRIER",
            "numero_empleado": "202",
            "nss": "44444444444",
        },
    ]
    worker_ids = insert_workers(
        connection,
        period_id,
        [
            {
                "row_number": 6,
                "num_empleado": "201",
                "nombre_original": "Carla Carrier Activa",
                "nombre_normalizado": "CARLA CARRIER ACTIVA",
                "puesto": "Op",
                "planta_original": "T",
                "planta_normalizada": "T",
                "cuenta": "3",
                "row_json": "{}",
            },
            {
                "row_number": 7,
                "num_empleado": "299",
                "nombre_original": "Carlos Carrier Nuevo",
                "nombre_normalizado": "CARLOS CARRIER NUEVO",
                "puesto": "Op",
                "planta_original": "T",
                "planta_normalizada": "T",
                "cuenta": "4",
                "row_json": "{}",
            },
        ],
    )
    connection.executemany(
        "UPDATE gis_nomina_workers SET cliente_confirmado = 'CARRIER' WHERE id = ?",
        [(worker_id,) for worker_id in worker_ids],
    )
    all_hc = [*HC, *carrier_hc]
    for worker_id in worker_ids:
        worker = dict(
            connection.execute(
                "SELECT * FROM gis_nomina_workers WHERE id = ?", (worker_id,)
            ).fetchone()
        )
        upsert_match(connection, worker_id, match_worker(worker, all_hc))
    connection.commit()

    pepsi = run_comparative(
        connection,
        period_id=period_id,
        cliente="PEPSI",
        generated_by="test",
        headcount_rows=all_hc,
    )
    carrier = run_comparative(
        connection,
        period_id=period_id,
        cliente="CARRIER",
        generated_by="test",
        headcount_rows=all_hc,
    )

    def _result_scope(comparative_id):
        return connection.execute(
            """
            SELECT w.cliente_confirmado, w.num_empleado, r.hc_nombre, r.resultado
            FROM gis_nomina_results r
            LEFT JOIN gis_nomina_workers w ON w.id = r.worker_id
            WHERE r.comparative_id = ?
            ORDER BY r.id
            """,
            (comparative_id,),
        ).fetchall()

    pepsi_rows = _result_scope(pepsi["comparative_id"])
    carrier_rows = _result_scope(carrier["comparative_id"])
    assert {row["num_empleado"] for row in pepsi_rows if row["num_empleado"]} == {"101", "999"}
    assert {row["num_empleado"] for row in carrier_rows if row["num_empleado"]} == {"201", "299"}
    assert {row["hc_nombre"] for row in pepsi_rows if row["resultado"] == "Posible baja"} == {
        "MARIA SOLO HEADCOUNT"
    }
    assert {row["hc_nombre"] for row in carrier_rows if row["resultado"] == "Posible baja"} == {
        "CAMILA CARRIER AUSENTE"
    }


@pytest.mark.parametrize(
    ("codes", "expected_date"),
    [
        (["A", "F", "F", "F", "F", "F", "F"], "01/06/2026"),
        (["D", "NI", "F", "F", "A", "F", "F"], "05/06/2026"),
        (["D", "NI", "V", "I", "F", "", "F"], ""),
    ],
)
def test_possible_alta_uses_first_real_attendance(conn, codes, expected_date):
    connection, period_id = conn
    worker = connection.execute(
        "SELECT id FROM gis_nomina_workers WHERE num_empleado = '999'"
    ).fetchone()
    for index, code in enumerate(codes, start=1):
        insert_attendance(
            connection,
            {
                "worker_id": int(worker["id"]),
                "period_id": period_id,
                "column_index": index,
                "column_number": 10 + index,
                "fecha_iso": f"2026-06-{index:02d}",
                "header_original": f"D{index}",
                "code_original": code,
                "code_normalized": code,
                "interpretation_status": "ok",
                "created_at": "2026-06-08T10:00:00",
                "updated_at": "2026-06-08T10:00:00",
            },
        )
    connection.commit()

    out = run_comparative(
        connection,
        period_id=period_id,
        cliente="PEPSI",
        generated_by="test",
        headcount_rows=HC,
    )
    result = connection.execute(
        """
        SELECT fecha_sugerida
        FROM gis_nomina_results
        WHERE comparative_id = ? AND worker_id = ?
        """,
        (out["comparative_id"], int(worker["id"])),
    ).fetchone()
    assert result["fecha_sugerida"] == expected_date


def test_homonym_goes_to_review():
    hc = [
        {"nombre_completo": "JUAN PEREZ LOPEZ", "cliente": "A", "nss": "1"},
        {"nombre_completo": "JUAN PEREZ LOPEZ", "cliente": "B", "nss": "2"},
    ]
    match = match_worker({"nombre_normalizado": "JUAN PEREZ LOPEZ"}, hc)
    assert match["status"] == "review"


def test_weekly_result_hide_restore_and_edit_are_audited(conn):
    connection, period_id = conn
    out = run_comparative(
        connection, period_id=period_id, cliente="PEPSI", generated_by="test", headcount_rows=HC
    )
    result_id = connection.execute(
        "SELECT id FROM gis_nomina_results WHERE comparative_id = ? AND resultado = 'Posible alta'",
        (out["comparative_id"],),
    ).fetchone()[0]
    set_result_visibility(connection, result_id, hidden=True, changed_by="tester", reason="duplicada")
    assert connection.execute(
        "SELECT hidden_at FROM gis_nomina_results WHERE id = ?", (result_id,)
    ).fetchone()[0]
    set_result_visibility(connection, result_id, hidden=False, changed_by="tester")
    update_result_decision(
        connection,
        result_id,
        decision_final="Revisión",
        fecha_sugerida="",
        observaciones="Validar identidad",
        changed_by="tester",
    )
    connection.commit()
    audit = list_workspace_audit(
        connection, scope="weekly", record_type="result", record_id=result_id
    )
    assert [row["action"] for row in audit] == ["edit", "restore", "hide"]
