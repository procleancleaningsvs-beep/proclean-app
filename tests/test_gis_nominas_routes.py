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


def test_multiclient_compare_combines_workspace_and_recalculates_one_client(tmp_path, monkeypatch):
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
    monkeypatch.setattr("modules.gestion_idse_sua.nominas.comparative_service.obtener_activos", _active_rows)

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
    assert comparative_counts == {"CARRIER": 1, "PEPSI": 1}
    assert connection.execute(
        """
        SELECT COUNT(*)
        FROM gis_nomina_results r
        JOIN gis_nomina_workers w ON w.id = r.worker_id
        WHERE w.num_empleado = 'X1'
        """
    ).fetchone()[0] == 0
    connection.close()

    workspace_html = client.get(
        f"/gestion-idse-sua/nominas/workspace/{period_id}"
    ).get_data(as_text=True)
    assert "CARRIER AUSENTE" in workspace_html
    assert "PEPSI AUSENTE" in workspace_html
    assert re.search(r'PENDIENTE SIN CLIENTE[^\"]*Revisión', workspace_html, re.S)
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
    assert comparative_counts == {"CARRIER": 2, "PEPSI": 1}


def test_manual_match_with_multiple_options_stays_in_review_and_same_client(tmp_path, monkeypatch):
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
    assert len(candidates) == 2
    assert {item["cliente"] for item in candidates} == {"AURIGA"}


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
