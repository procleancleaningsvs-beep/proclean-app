"""GIS — inferencia automática de clientes en nómina."""

from __future__ import annotations

import sqlite3

import pytest

from modules.gestion_idse_sua.nominas.client_inference_service import (
    infer_period_clients,
    infer_worker_client,
    summarize_period_clients,
)
from modules.gestion_idse_sua.nominas.schema import ensure_gis_nominas_tables


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "clients.db")
    connection.row_factory = sqlite3.Row
    ensure_gis_nominas_tables(connection)
    connection.execute(
        """
        INSERT OR IGNORE INTO gis_planta_cliente (planta_normalizada, planta_original, cliente, source)
        VALUES ('FLOTADO', 'FLOTADO', 'VITROFLEX', 'seed')
        """
    )
    connection.commit()
    yield connection
    connection.close()


def test_infer_from_planta_catalog(conn):
    hc = [{"cliente": "PEPSI", "planta": "A", "nombre_completo": "X"}]
    out = infer_worker_client(
        conn,
        worker={"planta_normalizada": "FLOTADO", "cliente_confirmado": ""},
        match=None,
        headcount_rows=hc,
        filename="carrier.xlsx",
        sheet_name="Semana",
    )
    assert out["cliente"] == "VITROFLEX"
    assert out["confidence"] == 1.0


def test_infer_from_filename(conn):
    hc = [{"cliente": "CARRIER", "nombre_completo": "X"}]
    out = infer_worker_client(
        conn,
        worker={"planta_normalizada": "X", "cliente_confirmado": ""},
        match=None,
        headcount_rows=hc,
        filename="Carrier 10 al 16 jul.xlsx",
        sheet_name="Hoja1",
    )
    assert out["cliente"] == "CARRIER"
    assert out["source"] == "filename"


def test_contradiction_summary():
    summary = summarize_period_clients(
        [
            {"cliente": "PEPSI", "requires_review": False},
            {"cliente": "CARRIER", "requires_review": True},
        ]
    )
    assert summary["contradictions"] is True
    assert summary["requires_review"] is True


def test_infer_period_clients_multi(conn):
    hc = [{"cliente": "PEPSI", "nombre_completo": "A"}, {"cliente": "CARRIER", "nombre_completo": "B"}]
    workers = [
        {"id": 1, "planta_normalizada": "FLOTADO", "cliente_confirmado": ""},
        {"id": 2, "planta_normalizada": "NORTE", "cliente_confirmado": ""},
    ]
    payload = infer_period_clients(
        conn,
        period_id=1,
        workers=workers,
        matches={1: None, 2: None},
        headcount_rows=hc,
        filename="mixto.xlsx",
        sheet_name="S1",
    )
    assert payload["summary"]["primary_cliente"] in {"VITROFLEX", "PEPSI", "CARRIER", ""}
