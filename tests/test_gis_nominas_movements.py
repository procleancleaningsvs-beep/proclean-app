"""GIS Nóminas — conversión a movimientos."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from modules.gestion_idse_sua.nominas.movement_bridge import convert_results_to_movements
from modules.gestion_idse_sua.nominas.schema import ensure_gis_nominas_tables


@pytest.fixture
def conn(tmp_path, monkeypatch, tmp_path_factory):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    connection = sqlite3.connect(tmp_path / "mov.db")
    connection.row_factory = sqlite3.Row
    ensure_gis_nominas_tables(connection)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        """
        INSERT INTO gis_nomina_imports (id, original_filename, file_hash, uploaded_at, status)
        VALUES (1,'f.xlsx','h','2026-01-01','compared')
        """
    )
    connection.execute(
        "INSERT INTO gis_nomina_sheets (id, import_id, sheet_index, sheet_name, is_hidden, estimated_rows) VALUES (1,1,0,'S',0,1)"
    )
    connection.execute(
        "INSERT INTO gis_nomina_periods (id, sheet_id, fecha_inicio, fecha_fin, user_confirmed) VALUES (1,1,'01/06/2026','07/06/2026',1)"
    )
    connection.execute(
        "INSERT INTO gis_nomina_comparatives (id, period_id, cliente, generated_at, status) VALUES (1,1,'PEPSI','2026-01-01','completed')"
    )
    connection.execute(
        """
        INSERT INTO gis_nomina_workers (id, period_id, row_number, nombre_original, nombre_normalizado)
        VALUES (1,1,4,'Juan','JUAN PEREZ')
        """
    )
    connection.execute(
        """
        INSERT INTO gis_nomina_results
        (id, comparative_id, worker_id, headcount_only, resultado, semaforo, tipo_sugerido, conversion_status)
        VALUES (1, 1, 1, 0, 'Posible alta', 'verde', 'ALTA', 'none')
        """
    )
    connection.commit()
    yield connection
    connection.close()


@patch("modules.gestion_idse_sua.nominas.movement_bridge.guardar_movimiento")
def test_convert_incomplete_excluded(mock_save, conn):
    out = convert_results_to_movements(conn, result_ids=[1])
    assert out["converted_ids"] == []
    assert out["excluded"]
    mock_save.assert_not_called()
    row = conn.execute("SELECT conversion_status FROM gis_nomina_results WHERE id = 1").fetchone()
    assert row["conversion_status"] == "excluded"


@patch("modules.gestion_idse_sua.nominas.movement_bridge.guardar_movimiento")
def test_convert_idempotent(mock_save, conn):
    mock_save.return_value = {"id": "mov-1"}
    conn.execute(
        """
        UPDATE gis_nomina_results
        SET conversion_status = 'converted', movimiento_id = 'mov-1'
        WHERE id = 1
        """
    )
    conn.commit()
    out = convert_results_to_movements(conn, result_ids=[1])
    assert out["converted_ids"] == ["mov-1"]
    mock_save.assert_not_called()
