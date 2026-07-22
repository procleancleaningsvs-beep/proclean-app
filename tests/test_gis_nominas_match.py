"""GIS Nóminas — planta-cliente y match."""

from __future__ import annotations

import sqlite3

import pytest

from modules.gestion_idse_sua.nominas.match_service import match_worker
from modules.gestion_idse_sua.nominas.planta_cliente_service import (
    confirm_planta_cliente,
    get_planta_cliente,
    suggest_cliente_for_planta,
)
from modules.gestion_idse_sua.nominas.schema import ensure_gis_nominas_tables


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "gis_match.db")
    connection.row_factory = sqlite3.Row
    ensure_gis_nominas_tables(connection)
    connection.commit()
    yield connection
    connection.close()


HC = [
    {
        "nombre_completo": "JUAN PEREZ LOPEZ",
        "cliente": "VITROFLEX",
        "planta": "FLOTADO",
        "nss": "12345678901",
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
        "nombre_completo": "MARIA GARCIA SOLIS",
        "cliente": "PEPSI",
        "nss": "10987654321",
    },
]


def test_known_planta_cliente_seed(conn):
    row = get_planta_cliente(conn, "FLOTADO")
    assert row["cliente"] == "VITROFLEX"


def test_headcount_trend_suggestion(conn):
    sug = suggest_cliente_for_planta(conn, "NUEVA PLANTA", [{"cliente": "PEPSI", "planta": "NUEVA PLANTA"}] * 3)
    assert sug["cliente"] == "PEPSI"
    assert sug["requires_confirmation"] is True


def test_match_by_num_empleado():
    worker = {"num_empleado": "101", "nombre_normalizado": "OTRO NOMBRE"}
    match = match_worker(worker, HC)
    assert match["match_method"] == "num_empleado"
    assert match["status"] == "auto"


def test_match_exact_name():
    worker = {"nombre_normalizado": "MARIA GARCIA SOLIS"}
    match = match_worker(worker, HC)
    assert match["match_method"] == "nombre_exacto"


def test_match_unmatched():
    worker = {"nombre_normalizado": "SIN COINCIDENCIA"}
    match = match_worker(worker, HC)
    assert match["status"] == "unmatched"


def test_match_alias_resolves_to_headcount(monkeypatch):
    monkeypatch.setattr(
        "modules.gestion_idse_sua.nominas.match_service.alias_service.obtener_alias",
        lambda _name: "MARIA GARCIA SOLIS",
    )
    worker = {"nombre_normalizado": "MARIA G SOLIS"}
    match = match_worker(worker, HC)
    assert match["match_method"] == "alias"
    assert match["status"] == "confirmed"
