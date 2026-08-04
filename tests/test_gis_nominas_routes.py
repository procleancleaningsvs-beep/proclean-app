"""GIS Nóminas — rutas del workspace."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash


def _full_app(tmp_path, monkeypatch, role: str = "admin"):
    from modules.nomina.db import ensure_nomina_tables

    db = str(tmp_path / f"gis_nom_{role}.db")
    monkeypatch.setattr("app.DB_PATH", Path(db))
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
    monkeypatch.setenv("PERF_LOG_ENABLED", "0")
    from app import create_app

    app = create_app()
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
