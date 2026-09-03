"""GIS Nóminas — rutas del workspace."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash


def _full_app(tmp_path, monkeypatch, role: str = "admin"):
    test_instance_dir = tmp_path / "instance"
    monkeypatch.setenv("PROCLEAN_INSTANCE_DIR", str(test_instance_dir))
    monkeypatch.setenv("PERF_LOG_ENABLED", "0")

    from modules.nomina.db import ensure_nomina_tables
    import app as app_module

    db = str(tmp_path / f"gis_nom_{role}.db")
    monkeypatch.setattr(app_module, "INSTANCE_DIR", test_instance_dir)
    monkeypatch.setattr(app_module, "ADMIN_CREDENTIALS_PATH", test_instance_dir / "admin_credentials.txt")
    monkeypatch.setattr(app_module, "SECRET_KEY_PATH", test_instance_dir / "secret_key.txt")
    monkeypatch.setattr(app_module, "DB_PATH", Path(db))
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    ensure_nomina_tables(conn)
    conn.execute(
        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
        ("gisuser", generate_password_hash("secret"), role, "2026-01-01 00:00:00"),
    )
    conn.commit()
    conn.close()
    app = app_module.create_app()
    app.config["TESTING"] = True
    app.config["DATABASE"] = db
    return app


def _login(client):
    return client.post("/login", data={"username": "gisuser", "password": "secret"}, follow_redirects=True)


def test_nominas_workspace_loads(tmp_path, monkeypatch):
    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    res = client.get("/gestion-idse-sua/nominas")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Nóminas y análisis" in html
    assert "Importar Excel" in html
    assert "Comparativo legado" in html


def test_nominas_not_redirect_to_legacy(tmp_path, monkeypatch):
    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    res = client.get("/gestion-idse-sua/nominas", follow_redirects=False)
    assert res.status_code == 200
    assert "/comparativo" not in (res.headers.get("Location") or "")


def test_hub_points_to_new_nominas(tmp_path, monkeypatch):
    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    html = client.get("/gestion-idse-sua/").get_data(as_text=True)
    assert 'href="/gestion-idse-sua/nominas"' in html


def test_nominas_import_post_with_session_user_row(tmp_path, monkeypatch):
    from io import BytesIO
    from pathlib import Path

    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    fixture = Path("tests/fixtures/nomina_carrier_anon.xlsx").read_bytes()
    res = client.post(
        "/gestion-idse-sua/nominas/import",
        data={"file": (BytesIO(fixture), "Carrier 10 al 16 jul.xlsx")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert res.status_code == 302
    location = res.headers.get("Location") or ""
    assert "/gestion-idse-sua/nominas/import/" in location
    assert "/login" not in location


def test_period_review_previews_clients_before_confirmation(tmp_path, monkeypatch):
    from io import BytesIO

    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    fixture = Path("tests/fixtures/nomina_carrier_anon.xlsx").read_bytes()
    response = client.post(
        "/gestion-idse-sua/nominas/import",
        data={"file": (BytesIO(fixture), "Carrier 10 al 16 jul.xlsx")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    import_id = int((response.headers["Location"].rstrip("/").split("/")[-1]))

    connection = sqlite3.connect(app.config["DATABASE"])
    sheet_id = connection.execute(
        "SELECT id FROM gis_nomina_sheets WHERE import_id = ? ORDER BY sheet_index LIMIT 1",
        (import_id,),
    ).fetchone()[0]
    connection.close()
    client.post(
        f"/gestion-idse-sua/nominas/import/{import_id}/classify",
        data={f"sheet_{sheet_id}": "nomina"},
    )
    from modules.gestion_idse_sua.routes_nominas import _staging_path

    _staging_path(import_id).unlink(missing_ok=True)
    monkeypatch.setattr(
        "modules.gestion_idse_sua.routes_nominas.obtener_activos",
        lambda *args, **kwargs: [{"cliente": "CARRIER", "nombre_completo": "PERSONA HC"}],
    )

    html = client.get(f"/gestion-idse-sua/nominas/import/{import_id}/period").get_data(as_text=True)
    assert "Clientes detectados" in html
    assert 'name="clientes" value="CARRIER"' in html
    assert "Confianza" in html
    assert "Periodo inicio" in html

    confirmed = client.post(
        f"/gestion-idse-sua/nominas/sheet/{sheet_id}/period",
        data={
            "fecha_inicio": "10/07/2025",
            "fecha_fin": "16/07/2025",
            "clientes": ["CARRIER"],
        },
        follow_redirects=False,
    )
    assert confirmed.status_code == 302
    assert "/workspace/" in confirmed.headers["Location"]
    workspace_html = client.get(confirmed.headers["Location"]).get_data(as_text=True)
    assert "Nombre completo" in workspace_html
    assert "Movimientos sugeridos" in workspace_html
    assert "data-excel-filter" in workspace_html
    connection = sqlite3.connect(app.config["DATABASE"])
    assignments = connection.execute(
        "SELECT DISTINCT cliente_confirmado FROM gis_nomina_workers"
    ).fetchall()
    connection.close()
    assert assignments == [("CARRIER",)]

    opened = client.get(
        f"/gestion-idse-sua/nominas/import/{import_id}/open", follow_redirects=False
    )
    assert "/workspace/" in opened.headers["Location"]
    client.post(
        f"/gestion-idse-sua/nominas/import/{import_id}/archive",
        data={"reason": "QA"},
    )
    active_html = client.get("/gestion-idse-sua/nominas").get_data(as_text=True)
    assert "Carrier 10 al 16 jul.xlsx" not in active_html
    archived_html = client.get("/gestion-idse-sua/nominas?archived=1").get_data(as_text=True)
    assert "Carrier 10 al 16 jul.xlsx" in archived_html
    assert "Restaurar" in archived_html
    client.post(f"/gestion-idse-sua/nominas/import/{import_id}/restore")
    restored_html = client.get("/gestion-idse-sua/nominas").get_data(as_text=True)
    assert "Carrier 10 al 16 jul.xlsx" in restored_html


def test_multiclient_compare_recalculates_all_resolved_clients(tmp_path, monkeypatch):
    from modules.gestion_idse_sua.nominas import repository as repo
    from modules.gestion_idse_sua.nominas.match_service import match_worker
    from modules.gestion_idse_sua.nominas.schema import ensure_gis_nominas_tables

    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.row_factory = sqlite3.Row
    ensure_gis_nominas_tables(connection)
    connection.execute(
        "INSERT INTO gis_nomina_imports (id, original_filename, file_hash, uploaded_at, status) "
        "VALUES (1, 'mixta.xlsx', 'h', '2026-01-01', 'extracted')"
    )
    connection.execute(
        "INSERT INTO gis_nomina_sheets "
        "(id, import_id, sheet_index, sheet_name, is_hidden, confirmed_classification, estimated_rows) "
        "VALUES (1, 1, 0, 'Mixta', 0, 'nomina', 3)"
    )
    period_id = repo.upsert_period(
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
    worker_ids = repo.insert_workers(
        connection,
        period_id,
        [
            {
                "row_number": 4,
                "num_empleado": "C1",
                "nombre_original": "Carrier Activo",
                "nombre_normalizado": "CARRIER ACTIVO",
                "puesto": "Op",
                "planta_original": "T",
                "planta_normalizada": "T",
                "cuenta": "1",
                "row_json": "{}",
            },
            {
                "row_number": 5,
                "num_empleado": "P1",
                "nombre_original": "Pepsi Activo",
                "nombre_normalizado": "PEPSI ACTIVO",
                "puesto": "Op",
                "planta_original": "N",
                "planta_normalizada": "N",
                "cuenta": "2",
                "row_json": "{}",
            },
            {
                "row_number": 6,
                "num_empleado": "X1",
                "nombre_original": "Pendiente Sin Cliente",
                "nombre_normalizado": "PENDIENTE SIN CLIENTE",
                "puesto": "Op",
                "planta_original": "",
                "planta_normalizada": "",
                "cuenta": "3",
                "row_json": "{}",
            },
        ],
    )
    connection.execute(
        "UPDATE gis_nomina_workers SET cliente_confirmado = 'CARRIER' WHERE id = ?",
        (worker_ids[0],),
    )
    connection.execute(
        "UPDATE gis_nomina_workers SET cliente_confirmado = 'PEPSI' WHERE id = ?",
        (worker_ids[1],),
    )
    connection.execute(
        "UPDATE gis_nomina_workers SET cliente_confirmado = 'NUEVO', "
        "suggestion_source = 'manual_new_client' WHERE id = ?",
        (worker_ids[2],),
    )
    headcount = [
        {"nombre_completo": "CARRIER ACTIVO", "cliente": "CARRIER", "numero_empleado": "C1", "nss": "111"},
        {"nombre_completo": "CARRIER AUSENTE", "cliente": "CARRIER", "numero_empleado": "C2", "nss": "112"},
        {"nombre_completo": "PEPSI ACTIVO", "cliente": "PEPSI", "numero_empleado": "P1", "nss": "211"},
        {"nombre_completo": "PEPSI AUSENTE", "cliente": "PEPSI", "numero_empleado": "P2", "nss": "212"},
    ]
    for worker_id in worker_ids:
        worker = dict(connection.execute("SELECT * FROM gis_nomina_workers WHERE id = ?", (worker_id,)).fetchone())
        repo.upsert_match(connection, worker_id, match_worker(worker, headcount))
    connection.commit()
    connection.close()

    def _active_rows(cliente=None):
        if not cliente:
            return headcount
        return [row for row in headcount if row["cliente"] == str(cliente).upper()]

    monkeypatch.setattr("modules.gestion_idse_sua.routes_nominas.obtener_activos", _active_rows)
    monkeypatch.setattr(
        "modules.gestion_idse_sua.nominas.comparative_service.load_full_headcount",
        lambda: headcount,
    )

    response = client.post(
        f"/gestion-idse-sua/nominas/workspace/{period_id}/compare",
        data={},
        follow_redirects=False,
    )
    assert response.status_code == 302

    connection = sqlite3.connect(app.config["DATABASE"])
    connection.row_factory = sqlite3.Row
    comparative_counts = dict(
        connection.execute(
            "SELECT cliente, COUNT(*) FROM gis_nomina_comparatives GROUP BY cliente"
        ).fetchall()
    )
    assert comparative_counts == {"CARRIER": 1, "NUEVO": 1, "PEPSI": 1}
    assert connection.execute(
        """
        SELECT COUNT(*)
        FROM gis_nomina_results r
        JOIN gis_nomina_workers w ON w.id = r.worker_id
        WHERE w.num_empleado = 'X1'
        """
    ).fetchone()[0] == 1
    connection.close()

    workspace_html = client.get(
        f"/gestion-idse-sua/nominas/workspace/{period_id}"
    ).get_data(as_text=True)
    assert "CARRIER AUSENTE" in workspace_html
    assert "PEPSI AUSENTE" in workspace_html
    assert re.search(r'PENDIENTE SIN CLIENTE[^\"]*Posible alta', workspace_html, re.S)
    assert re.search(r'CARRIER AUSENTE[^\"]*CARRIER', workspace_html, re.S)
    assert re.search(r'PEPSI AUSENTE[^\"]*PEPSI', workspace_html, re.S)

    response = client.post(
        f"/gestion-idse-sua/nominas/workspace/{period_id}/compare",
        data={"cliente": "CARRIER"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    connection = sqlite3.connect(app.config["DATABASE"])
    comparative_counts = dict(
        connection.execute(
            "SELECT cliente, COUNT(*) FROM gis_nomina_comparatives GROUP BY cliente"
        ).fetchall()
    )
    connection.close()
    assert comparative_counts == {"CARRIER": 2, "NUEVO": 2, "PEPSI": 2}


def test_manual_match_keeps_cross_client_options_and_manual_movement_can_be_cleared(tmp_path, monkeypatch):
    from modules.gestion_idse_sua.nominas import repository as repo
    from modules.gestion_idse_sua.nominas.schema import ensure_gis_nominas_tables

    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.row_factory = sqlite3.Row
    ensure_gis_nominas_tables(connection)
    connection.execute(
        "INSERT INTO gis_nomina_imports (id, original_filename, file_hash, uploaded_at, status) "
        "VALUES (1, 'manual.xlsx', 'hm', '2026-01-01', 'extracted')"
    )
    connection.execute(
        "INSERT INTO gis_nomina_sheets "
        "(id, import_id, sheet_index, sheet_name, is_hidden, confirmed_classification, estimated_rows) "
        "VALUES (1, 1, 0, 'Manual', 0, 'nomina', 1)"
    )
    period_id = repo.upsert_period(
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
    worker_id = repo.insert_workers(
        connection,
        period_id,
        [
            {
                "row_number": 4,
                "num_empleado": "",
                "nombre_original": "Gabriel Jimenez Vargas",
                "nombre_normalizado": "GABRIEL JIMENEZ VARGAS",
                "puesto": "Op",
                "planta_original": "NOCHE",
                "planta_normalizada": "NOCHE",
                "cuenta": "1",
                "row_json": "{}",
            }
        ],
    )[0]
    repo.update_worker_cliente(connection, worker_id, "AURIGA")
    comparative_id = repo.create_comparative(
        connection,
        period_id=period_id,
        cliente="AURIGA",
        generated_by="test",
    )
    result_id = repo.insert_result(
        connection,
        comparative_id,
        {
            "worker_id": worker_id,
            "resultado": "Posible alta",
            "semaforo": "verde",
            "tipo_sugerido": "ALTA",
            "decision_final": "Posible alta",
        },
    )
    connection.commit()
    connection.close()

    monkeypatch.setattr(
        "modules.gestion_idse_sua.routes_nominas.manual_search",
        lambda _query, _campo: {
            "encontrado": True,
            "duplicado": True,
            "opciones": [
                {"nombre_completo": "GABRIEL JIMENEZ VARGAS", "cliente": "AURIGA", "nss": "111"},
                {"nombre_completo": "GABRIEL VARGAS JIMENEZ", "cliente": "AURIGA", "nss": "112"},
                {"nombre_completo": "GABRIEL JIMENEZ VARGAS", "cliente": "CARRIER", "nss": "999"},
            ],
        },
    )

    response = client.post(
        f"/gestion-idse-sua/nominas/worker/{worker_id}/match",
        data={"campo": "nombre_completo", "query": "GABRIEL"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.row_factory = sqlite3.Row
    match = dict(
        connection.execute(
            "SELECT * FROM gis_nomina_matches WHERE worker_id = ?", (worker_id,)
        ).fetchone()
    )
    connection.close()
    candidates = json.loads(match["hc_json"])
    assert match["status"] == "review"
    assert match["headcount_key"] is None
    assert len(candidates) == 3
    assert {item["cliente"] for item in candidates} == {"AURIGA", "CARRIER"}

    movement_response = client.post(
        f"/gestion-idse-sua/nominas/result/{result_id}/decision",
        data={"decision_final": "Revisión", "tipo_sugerido": ""},
        follow_redirects=False,
    )
    assert movement_response.status_code == 302
    connection = sqlite3.connect(app.config["DATABASE"])
    movement = connection.execute(
        "SELECT decision_final, tipo_sugerido FROM gis_nomina_results WHERE id = ?",
        (result_id,),
    ).fetchone()
    audit_count = connection.execute(
        "SELECT COUNT(*) FROM gis_workspace_audit WHERE record_type = 'result' AND record_id = ?",
        (result_id,),
    ).fetchone()[0]
    connection.close()
    assert movement == ("Revisión", "")
    assert audit_count == 1


def _seed_candidate_resolution(app, candidate):
    from modules.gestion_idse_sua.nominas import repository as repo
    from modules.gestion_idse_sua.nominas.match_service import build_review_match
    from modules.gestion_idse_sua.nominas.schema import ensure_gis_nominas_tables

    connection = sqlite3.connect(app.config["DATABASE"])
    connection.row_factory = sqlite3.Row
    ensure_gis_nominas_tables(connection)
    connection.execute(
        "INSERT INTO gis_nomina_imports (id, original_filename, file_hash, uploaded_at, status) "
        "VALUES (1, 'synthetic-manual.xlsx', 'manual-hash', '2026-01-01', 'extracted')"
    )
    connection.execute(
        "INSERT INTO gis_nomina_sheets "
        "(id, import_id, sheet_index, sheet_name, is_hidden, confirmed_classification, estimated_rows) "
        "VALUES (1, 1, 0, 'Synthetic', 0, 'nomina', 1)"
    )
    period_id = repo.upsert_period(
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
    worker_id = repo.insert_workers(
        connection,
        period_id,
        [
            {
                "row_number": 4,
                "num_empleado": "",
                "nombre_original": "Diego Rocha",
                "nombre_normalizado": "DIEGO ROCHA",
                "puesto": "Operador",
                "planta_original": "DIA",
                "planta_normalizada": "DIA",
                "cuenta": "",
                "row_json": "{}",
            }
        ],
    )[0]
    repo.update_worker_cliente(connection, worker_id, "AURIGA")
    comparative_id = repo.create_comparative(
        connection,
        period_id=period_id,
        cliente="AURIGA",
        generated_by="test",
    )
    result_id = repo.insert_result(
        connection,
        comparative_id,
        {
            "worker_id": worker_id,
            "resultado": "Revisión",
            "semaforo": "amarillo",
            "tipo_sugerido": "",
            "decision_final": "Revisión",
        },
    )
    repo.upsert_match(
        connection,
        worker_id,
        build_review_match(
            [candidate],
            method="candidato_nombre",
            reason="Nombre Diego exacto · Apellido Rocha exacto",
        ),
    )
    connection.commit()
    connection.close()
    return period_id, worker_id, result_id


@pytest.mark.parametrize(
    ("operation_status", "headcount_client", "expected_result", "expected_movement", "reason"),
    [
        ("ALTA", "AURIGA", "Coincidencia", "", ""),
        ("BAJA", "AURIGA", "Reingreso", "ALTA", ""),
        ("ALTA", "GM", "Revisión", "", "Activo en otro cliente"),
    ],
)
def test_selecting_candidate_recalculates_result_from_headcount_status(
    tmp_path,
    monkeypatch,
    operation_status,
    headcount_client,
    expected_result,
    expected_movement,
    reason,
):
    candidate = {
        "nombre_completo": "DIEGO ESTEBAN ROCHA MEZA",
        "nombre": "DIEGO ESTEBAN",
        "apellido_paterno": "ROCHA",
        "apellido_materno": "MEZA",
        "cliente": headcount_client,
        "ubicacion": "DIA",
        "status_operacion": operation_status,
        "nss": "55555555555",
    }
    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    _, worker_id, result_id = _seed_candidate_resolution(app, candidate)
    monkeypatch.setattr(
        "modules.gestion_idse_sua.routes_nominas.load_full_headcount",
        lambda: [candidate],
    )
    monkeypatch.setattr(
        "modules.gestion_idse_sua.routes_nominas.manual_search",
        lambda *_args, **_kwargs: {"encontrado": False},
    )

    response = client.post(
        f"/gestion-idse-sua/nominas/worker/{worker_id}/match",
        data={
            "action": "confirm_candidate",
            "candidate_index": "0",
            "result_id": str(result_id),
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    connection = sqlite3.connect(app.config["DATABASE"])
    match = connection.execute(
        "SELECT status, match_method FROM gis_nomina_matches WHERE worker_id = ?",
        (worker_id,),
    ).fetchone()
    result = connection.execute(
        "SELECT resultado, decision_final, tipo_sugerido, observaciones FROM gis_nomina_results WHERE id = ?",
        (result_id,),
    ).fetchone()
    connection.close()
    assert match == ("confirmed", "manual_candidate")
    assert result[:3] == (expected_result, expected_result, expected_movement)
    assert reason in (result[3] or "")


def test_candidate_can_be_confirmed_before_a_comparative_result_exists(tmp_path, monkeypatch):
    candidate = {
        "nombre_completo": "DIEGO ESTEBAN ROCHA MEZA",
        "nombre": "DIEGO ESTEBAN",
        "apellido_paterno": "ROCHA",
        "apellido_materno": "MEZA",
        "cliente": "AURIGA",
        "ubicacion": "DIA",
        "status_operacion": "ALTA",
        "nss": "55555555555",
    }
    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    period_id, worker_id, result_id = _seed_candidate_resolution(app, candidate)
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.execute("DELETE FROM gis_nomina_results WHERE id = ?", (result_id,))
    connection.commit()
    connection.close()
    monkeypatch.setattr(
        "modules.gestion_idse_sua.routes_nominas.load_full_headcount",
        lambda: [candidate],
    )
    monkeypatch.setattr(
        "modules.gestion_idse_sua.routes_nominas.obtener_activos", lambda: [candidate]
    )
    monkeypatch.setattr(
        "modules.gestion_idse_sua.routes_nominas.obtener_patrones", lambda: []
    )

    html = client.get(
        f"/gestion-idse-sua/nominas/workspace/{period_id}"
    ).get_data(as_text=True)
    assert f'id="candidate-resolution-{worker_id}"' in html

    missing_choice = client.post(
        f"/gestion-idse-sua/nominas/worker/{worker_id}/match",
        data={"action": "confirm_candidate"},
        follow_redirects=True,
    )
    assert "Seleccione una opción de identidad" in missing_choice.get_data(as_text=True)

    response = client.post(
        f"/gestion-idse-sua/nominas/worker/{worker_id}/match",
        data={"action": "confirm_candidate", "candidate_index": "0"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Identidad confirmada" in response.get_data(as_text=True)
    connection = sqlite3.connect(app.config["DATABASE"])
    persisted = connection.execute(
        "SELECT status, match_method FROM gis_nomina_matches WHERE worker_id = ?",
        (worker_id,),
    ).fetchone()
    connection.close()
    assert persisted == ("confirmed", "manual_candidate")


def test_rejecting_current_candidates_persists_no_match_and_releases_possible_baja(
    tmp_path, monkeypatch
):
    from modules.gestion_idse_sua.nominas.comparative_service import run_comparative

    candidate = {
        "nombre_completo": "NORA ISABEL LEON CRUZ",
        "nombre": "NORA ISABEL",
        "apellido_paterno": "LEON",
        "apellido_materno": "CRUZ",
        "cliente": "AURIGA",
        "ubicacion": "DIA",
        "status_operacion": "ALTA",
        "nss": "66666666666",
    }
    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    period_id, worker_id, result_id = _seed_candidate_resolution(app, candidate)
    monkeypatch.setattr(
        "modules.gestion_idse_sua.routes_nominas.obtener_activos", lambda: [candidate]
    )
    monkeypatch.setattr(
        "modules.gestion_idse_sua.routes_nominas.obtener_patrones", lambda: []
    )
    monkeypatch.setattr(
        "modules.gestion_idse_sua.routes_nominas.manual_search",
        lambda *_args, **_kwargs: {"encontrado": False},
    )

    html = client.get(
        f"/gestion-idse-sua/nominas/workspace/{period_id}"
    ).get_data(as_text=True)
    assert 'name="candidate_index" value="0"' in html
    assert 'value="confirm_candidate"' in html
    assert "Ninguna corresponde" in html
    assert "Movimiento final" in html
    assert "Movimiento manual" not in html

    response = client.post(
        f"/gestion-idse-sua/nominas/worker/{worker_id}/match",
        data={"action": "reject_candidates", "result_id": str(result_id)},
        follow_redirects=False,
    )

    assert response.status_code == 302
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.row_factory = sqlite3.Row
    persisted = connection.execute(
        "SELECT status, match_method FROM gis_nomina_matches WHERE worker_id = ?",
        (worker_id,),
    ).fetchone()
    result = connection.execute(
        "SELECT resultado, decision_final, tipo_sugerido FROM gis_nomina_results WHERE id = ?",
        (result_id,),
    ).fetchone()
    rerun = run_comparative(
        connection,
        period_id=period_id,
        cliente="AURIGA",
        generated_by="test",
        headcount_rows=[candidate],
    )
    rerun_match = connection.execute(
        "SELECT status, match_method FROM gis_nomina_matches WHERE worker_id = ?",
        (worker_id,),
    ).fetchone()
    possible_bajas = {
        row[0]
        for row in connection.execute(
            "SELECT hc_nombre FROM gis_nomina_results WHERE comparative_id = ? AND resultado = 'Posible baja'",
            (rerun["comparative_id"],),
        ).fetchall()
    }
    connection.close()
    assert tuple(persisted) == ("unmatched", "manual_reject_candidates")
    assert tuple(result) == ("Posible alta", "Posible alta", "ALTA")
    assert tuple(rerun_match) == ("unmatched", "manual_reject_candidates")
    assert possible_bajas == {"NORA ISABEL LEON CRUZ"}


def test_monthly_workspace_renders_shared_table_and_event_panel(tmp_path, monkeypatch):
    import json

    from modules.gestion_idse_sua.nominas.schema import ensure_gis_nominas_tables
    from modules.gestion_idse_sua.reportes.schema import ensure_gis_monthly_tables

    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    connection = sqlite3.connect(app.config["DATABASE"])
    ensure_gis_nominas_tables(connection)
    ensure_gis_monthly_tables(connection)
    report_id = connection.execute(
        """
        INSERT INTO gis_monthly_reports
            (cliente, mes, anio, estado, created_at, updated_at, warnings_json, snapshot_json)
        VALUES ('PEPSI', 6, 2026, 'generado', '2026-07-01', '2026-07-01', '[]', ?)
        """,
        (json.dumps({"coverage_complete": True, "missing_dates": []}),),
    ).lastrowid
    person_id = connection.execute(
        """
        INSERT INTO gis_monthly_report_persons
            (report_id, identity_key, num_empleado, nombre_nomina, nombre_hc, match_method,
             match_status, nss, clientes_json, plantas_json, estado_mensual, totals_json,
             primera_a, ultima_a, warnings_json, daily_json, trajectory_json)
        VALUES (?, 'nss:111', '101', 'JUAN NOMINA', 'JUAN HC', 'nss', 'confirmed', '111',
                '["PEPSI"]', '["PLANTA A"]', 'Todo el mes', '{"A":20,"D":8}',
                '2026-06-01', '2026-06-30', '[]', '[]', '{}')
        """,
        (report_id,),
    ).lastrowid
    connection.execute(
        """
        INSERT INTO gis_monthly_report_events
            (report_id, person_id, event_type_suggested, fecha_suggested, estado)
        VALUES (?, ?, 'ALTA', '2026-06-01', 'propuesto')
        """,
        (report_id, person_id),
    )
    connection.commit()
    connection.close()

    response = client.get(f"/gestion-idse-sua/reportes/{report_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Nombre completo" in html
    assert "Eventos del reporte" in html
    assert "data-excel-filter" in html
    assert "JUAN HC" in html


@pytest.mark.parametrize(
    ("resolution", "canonical", "expected_client", "expected_source", "expected_bajas"),
    [
        ("existing", "GM", "GM", "manual_existing_client", {"ACTIVO GM AUSENTE"}),
        ("new", "", "GN", "manual_new_client", set()),
    ],
)
def test_unknown_payroll_client_requires_human_resolution(
    tmp_path,
    monkeypatch,
    resolution,
    canonical,
    expected_client,
    expected_source,
    expected_bajas,
):
    from modules.gestion_idse_sua.nominas import repository as repo
    from modules.gestion_idse_sua.nominas.schema import ensure_gis_nominas_tables

    headcount = [
        {
            "nombre_completo": "ACTIVO GM AUSENTE",
            "cliente": "GM",
            "status_operacion": "ALTA",
            "nss": "10000000001",
        }
    ]
    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.row_factory = sqlite3.Row
    ensure_gis_nominas_tables(connection)
    connection.execute(
        "INSERT INTO gis_nomina_imports (id, original_filename, file_hash, uploaded_at, status) "
        "VALUES (1, 'gn.xlsx', 'h', '2026-01-01', 'extracted')"
    )
    connection.execute(
        "INSERT INTO gis_nomina_sheets "
        "(id, import_id, sheet_index, sheet_name, is_hidden, confirmed_classification, estimated_rows) "
        "VALUES (1, 1, 0, 'GN', 0, 'nomina', 1)"
    )
    period_id = repo.upsert_period(
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
    worker_id = repo.insert_workers(
        connection,
        period_id,
        [
            {
                "row_number": 4,
                "num_empleado": "GN-1",
                "nombre_original": "Persona Cliente Nuevo",
                "nombre_normalizado": "PERSONA CLIENTE NUEVO",
                "puesto": "Op",
                "planta_original": "NORTE",
                "planta_normalizada": "NORTE",
                "cuenta": "",
                "row_json": "{}",
                "cliente_sugerido": "GN",
                "suggestion_source": "payroll",
                "suggestion_confidence": 1.0,
            }
        ],
    )[0]
    connection.commit()
    connection.close()
    monkeypatch.setattr(
        "modules.gestion_idse_sua.routes_nominas.obtener_activos", lambda: headcount
    )
    monkeypatch.setattr(
        "modules.gestion_idse_sua.routes_nominas.obtener_patrones", lambda: []
    )
    monkeypatch.setattr(
        "modules.gestion_idse_sua.routes_nominas._clientes_disponibles", lambda: ["GM"]
    )
    monkeypatch.setattr(
        "modules.gestion_idse_sua.nominas.comparative_service.load_full_headcount",
        lambda: headcount,
    )

    blocked = client.post(
        f"/gestion-idse-sua/nominas/workspace/{period_id}/compare",
        data={},
        follow_redirects=True,
    )
    blocked_html = blocked.get_data(as_text=True)
    assert "Cliente no reconocido: GN" in blocked_html
    assert "resolver" in blocked_html.lower()

    resolved = client.post(
        f"/gestion-idse-sua/nominas/workspace/{period_id}/resolve-client",
        data={
            "source_client": "GN",
            "resolution": resolution,
            "canonical_client": canonical,
        },
        follow_redirects=False,
    )
    assert resolved.status_code == 302
    connection = sqlite3.connect(app.config["DATABASE"])
    assignment = connection.execute(
        "SELECT cliente_confirmado, suggestion_source FROM gis_nomina_workers WHERE id = ?",
        (worker_id,),
    ).fetchone()
    connection.close()
    assert assignment == (expected_client, expected_source)

    compared = client.post(
        f"/gestion-idse-sua/nominas/workspace/{period_id}/compare",
        data={},
        follow_redirects=False,
    )
    assert compared.status_code == 302
    connection = sqlite3.connect(app.config["DATABASE"])
    bajas = {
        row[0]
        for row in connection.execute(
            "SELECT hc_nombre FROM gis_nomina_results WHERE resultado = 'Posible baja'"
        ).fetchall()
    }
    connection.close()
    assert bajas == expected_bajas


def test_all_comparative_results_render_and_persist_movement_change(tmp_path, monkeypatch):
    from modules.gestion_idse_sua.nominas import repository as repo
    from modules.gestion_idse_sua.nominas.schema import ensure_gis_nominas_tables

    app = _full_app(tmp_path, monkeypatch, role="admin")
    client = app.test_client()
    _login(client)
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.row_factory = sqlite3.Row
    ensure_gis_nominas_tables(connection)
    connection.execute(
        "INSERT INTO gis_nomina_imports (id, original_filename, file_hash, uploaded_at, status) "
        "VALUES (1, 'movimientos.xlsx', 'h', '2026-01-01', 'extracted')"
    )
    connection.execute(
        "INSERT INTO gis_nomina_sheets "
        "(id, import_id, sheet_index, sheet_name, is_hidden, confirmed_classification, estimated_rows) "
        "VALUES (1, 1, 0, 'Movimientos', 0, 'nomina', 4)"
    )
    period_id = repo.upsert_period(
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
    result_names = ("Coincidencia", "Revisión", "Posible alta", "Reingreso")
    workers = repo.insert_workers(
        connection,
        period_id,
        [
            {
                "row_number": index + 4,
                "num_empleado": str(index),
                "nombre_original": result,
                "nombre_normalizado": result.upper(),
                "puesto": "Op",
                "planta_original": "NORTE",
                "planta_normalizada": "NORTE",
                "cuenta": "",
                "row_json": "{}",
            }
            for index, result in enumerate(result_names, start=1)
        ],
    )
    connection.executemany(
        "UPDATE gis_nomina_workers SET cliente_confirmado = 'AURIGA' WHERE id = ?",
        [(worker_id,) for worker_id in workers],
    )
    comparative_id = repo.create_comparative(
        connection, period_id=period_id, cliente="AURIGA", generated_by="test"
    )
    defaults = {
        "Coincidencia": "",
        "Revisión": "",
        "Posible alta": "ALTA",
        "Reingreso": "ALTA",
        "Posible baja": "BAJA",
    }
    result_ids = [
        repo.insert_result(
            connection,
            comparative_id,
            {
                "worker_id": worker_id,
                "resultado": result,
                "semaforo": "amarillo",
                "tipo_sugerido": defaults[result],
                "decision_final": result,
                "observaciones": "Motivo sintético",
            },
        )
        for worker_id, result in zip(workers, result_names)
    ]
    result_ids.append(
        repo.insert_result(
            connection,
            comparative_id,
            {
                "worker_id": None,
                "headcount_only": True,
                "hc_nombre": "ACTIVO AUSENTE",
                "resultado": "Posible baja",
                "semaforo": "rojo",
                "tipo_sugerido": "BAJA",
                "decision_final": "Posible baja",
                "observaciones": "Ausente",
            },
        )
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr("modules.gestion_idse_sua.routes_nominas.obtener_activos", lambda: [])
    monkeypatch.setattr("modules.gestion_idse_sua.routes_nominas.obtener_patrones", lambda: [])

    html = client.get(
        f"/gestion-idse-sua/nominas/workspace/{period_id}"
    ).get_data(as_text=True)
    assert html.count(">Cambiar</summary>") == 5

    for result_id in result_ids:
        response = client.post(
            f"/gestion-idse-sua/nominas/result/{result_id}/decision",
            data={"decision_final": "Revisión", "tipo_sugerido": ""},
            follow_redirects=False,
        )
        assert response.status_code == 302
    connection = sqlite3.connect(app.config["DATABASE"])
    persisted = connection.execute(
        "SELECT COUNT(*) FROM gis_nomina_results WHERE tipo_sugerido = '' AND id IN (?,?,?,?,?)",
        result_ids,
    ).fetchone()[0]
    connection.close()
    assert persisted == 5
    refreshed = client.get(
        f"/gestion-idse-sua/nominas/workspace/{period_id}"
    ).get_data(as_text=True)
    assert re.search(r"ACTIVO AUSENTE.*?Ninguno", refreshed, re.S)
