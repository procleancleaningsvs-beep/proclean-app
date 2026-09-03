"""GIS Nóminas — planta-cliente y match."""

from __future__ import annotations

import json
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


def test_reordered_name_is_review_candidate_not_auto_match():
    worker = {
        "nombre_normalizado": "GABRIEL JIMENEZ VARGAS",
        "cliente_confirmado": "AURIGA",
    }
    headcount = [
        {
            "nombre_completo": "GABRIEL VARGAS JIMENEZ",
            "cliente": "AURIGA",
            "ubicacion": "DIA",
            "nss": "11111111111",
        }
    ]

    match = match_worker(worker, headcount)

    assert match["status"] == "review"
    assert match["match_method"] == "candidato_nombre"
    candidates = json.loads(match["hc_json"])
    assert [item["nombre_completo"] for item in candidates] == ["GABRIEL VARGAS JIMENEZ"]
    assert candidates[0]["candidate_reason"]


def test_minor_spelling_variant_is_review_candidate():
    worker = {
        "nombre_normalizado": "FELIPE SILOS",
        "cliente_confirmado": "AURIGA",
    }
    headcount = [
        {"nombre_completo": "FELIPE SILOZ", "cliente": "AURIGA", "nss": "12121212121"}
    ]

    match = match_worker(worker, headcount)

    assert match["status"] == "review"
    assert match["match_method"] == "candidato_nombre"


def test_no_reasonable_candidate_is_unmatched():
    worker = {
        "nombre_normalizado": "TANIA NIETO",
        "cliente_confirmado": "AURIGA",
    }
    headcount = [
        {"nombre_completo": "FELIPE SILOS", "cliente": "AURIGA", "nss": "22222222222"}
    ]

    match = match_worker(worker, headcount)

    assert match["status"] == "unmatched"


def test_exact_name_in_other_client_remains_an_identity_match():
    worker = {
        "nombre_normalizado": "HUGO RAMIREZ CASTRO",
        "cliente_confirmado": "AURIGA",
    }
    headcount = [
        {"nombre_completo": "HUGO RAMIREZ CASTRO", "cliente": "CARRIER", "nss": "33333333333"},
        {"nombre_completo": "PERSONA DISTINTA", "cliente": "AURIGA", "nss": "44444444444"},
    ]

    match = match_worker(worker, headcount)

    assert match["status"] == "auto"
    assert match["match_method"] == "nombre_exacto"
    assert match["hc_cliente"] == "CARRIER"


def test_partial_name_with_same_client_and_location_is_review_candidate():
    worker = {
        "nombre_normalizado": "MARTIN SALAZAR ROBLES",
        "cliente_confirmado": "GM",
        "planta_normalizada": "PFSA",
    }
    headcount = [
        {
            "nombre_completo": "MARTIN SALAZAR CORTES",
            "cliente": "GM",
            "ubicacion": "PFSA",
            "status_operacion": "ALTA",
        }
    ]

    match = match_worker(worker, headcount)

    assert match["status"] == "review"
    assert match["match_method"] == "candidato_nombre"
    candidates = json.loads(match["hc_json"])
    assert [item["nombre_completo"] for item in candidates] == [
        "MARTIN SALAZAR CORTES"
    ]
    assert "cliente" in candidates[0]["candidate_reason"].casefold()
    assert "ubicación" in candidates[0]["candidate_reason"].casefold()


def _structured_headcount(
    *,
    nombres: str,
    paterno: str,
    materno: str,
    cliente: str = "AURIGA",
    ubicacion: str = "DIA",
    status_operacion: str = "ALTA",
):
    return {
        "nombre_completo": f"{nombres} {paterno} {materno}",
        "nombre": nombres,
        "apellido_paterno": paterno,
        "apellido_materno": materno,
        "cliente": cliente,
        "ubicacion": ubicacion,
        "status_operacion": status_operacion,
    }


def test_shared_surnames_do_not_bypass_incompatible_given_names():
    worker = {
        "nombre_normalizado": "ALMA LUZ PARRA VEGA",
        "cliente_confirmado": "AURIGA",
        "planta_normalizada": "DIA",
    }
    headcount = [
        _structured_headcount(
            nombres="MARCOS JOEL",
            paterno="PARRA",
            materno="VEGA",
        )
    ]

    assert match_worker(worker, headcount)["status"] == "unmatched"


def test_shared_given_names_require_a_material_surname_match():
    worker = {
        "nombre_normalizado": "PEDRO LUIS MORA",
        "cliente_confirmado": "AURIGA",
    }
    valid = _structured_headcount(
        nombres="PEDRO LUIS",
        paterno="MORA",
        materno="CAMPOS",
    )
    incompatible = _structured_headcount(
        nombres="PEDRO LUIS",
        paterno="SALAS",
        materno="RIOS",
    )

    match = match_worker(worker, [incompatible, valid])

    candidates = json.loads(match["hc_json"])
    assert [item["nombre_completo"] for item in candidates] == [
        "PEDRO LUIS MORA CAMPOS"
    ]
    assert "Nombre Pedro exacto" in candidates[0]["candidate_reason"]
    assert "Apellido Mora exacto" in candidates[0]["candidate_reason"]


def test_unstructured_full_name_does_not_treat_second_given_name_as_a_surname():
    worker = {
        "nombre_normalizado": "PEDRO LUIS MORA",
        "cliente_confirmado": "AURIGA",
    }
    headcount = [
        {
            "nombre_completo": "PEDRO LUIS SALAS RIOS",
            "cliente": "AURIGA",
        }
    ]

    assert match_worker(worker, headcount)["status"] == "unmatched"


@pytest.mark.parametrize(
    ("payroll_name", "headcount", "expected_name"),
    [
        (
            "RAUL SANCHEZ LUNA",
            _structured_headcount(
                nombres="RAUL",
                paterno="SANCHEZ",
                materno="TORRES",
                cliente="GM",
                ubicacion="PFSA",
            ),
            "RAUL SANCHEZ TORRES",
        ),
        (
            "DIEGO ROCHA",
            _structured_headcount(
                nombres="DIEGO ESTEBAN",
                paterno="ROCHA",
                materno="MEZA",
            ),
            "DIEGO ESTEBAN ROCHA MEZA",
        ),
        (
            "NORA LEON",
            _structured_headcount(
                nombres="NORA ISABEL",
                paterno="LEON",
                materno="CRUZ",
            ),
            "NORA ISABEL LEON CRUZ",
        ),
    ],
)
def test_structurally_compatible_incomplete_names_are_review_candidates(
    payroll_name, headcount, expected_name
):
    worker = {
        "nombre_normalizado": payroll_name,
        "cliente_confirmado": headcount["cliente"],
        "planta_normalizada": headcount["ubicacion"],
    }

    match = match_worker(worker, [headcount])

    assert match["status"] == "review"
    assert [item["nombre_completo"] for item in json.loads(match["hc_json"])] == [
        expected_name
    ]


def test_visible_candidates_are_capped_at_three_without_padding():
    worker = {"nombre_normalizado": "PEDRO LUIS MORA", "cliente_confirmado": "AURIGA"}
    headcount = [
        _structured_headcount(
            nombres="PEDRO LUIS",
            paterno="MORA",
            materno=materno,
        )
        for materno in ("CAMPOS", "VEGA", "RIOS", "LUNA")
    ]

    match = match_worker(worker, headcount)

    assert len(json.loads(match["hc_json"])) == 3


@pytest.mark.parametrize(
    ("worker_field", "headcount_field", "value"),
    [
        ("nss", "nss", "12345678901"),
        ("curp", "curp", "PELJ800101HDFRNN09"),
        ("rfc", "rfc_homoclave", "PELJ800101XXX"),
        ("num_empleado", "numero_empleado", "501"),
    ],
)
def test_strong_identifier_confirms_unique_identity(worker_field, headcount_field, value):
    worker = {
        "nombre_normalizado": "NOMBRE CON VARIACION",
        "cliente_confirmado": "AURIGA",
        worker_field: value,
    }
    headcount = [
        {
            "nombre_completo": "GABRIEL JIMENEZ VARGAS",
            "cliente": "AURIGA",
            headcount_field: value,
        }
    ]

    match = match_worker(worker, headcount)

    assert match["status"] == "auto"
    assert match["match_method"] == worker_field


def test_contradictory_strong_identifiers_require_review():
    worker = {
        "nombre_normalizado": "GABRIEL JIMENEZ VARGAS",
        "cliente_confirmado": "AURIGA",
        "nss": "11111111111",
        "curp": "BBBB800101HDFBBB02",
    }
    headcount = [
        {
            "nombre_completo": "GABRIEL JIMENEZ VARGAS",
            "cliente": "AURIGA",
            "nss": "11111111111",
            "curp": "AAAA800101HDFAAA01",
        },
        {
            "nombre_completo": "OTRA PERSONA",
            "cliente": "AURIGA",
            "nss": "22222222222",
            "curp": "BBBB800101HDFBBB02",
        },
    ]

    match = match_worker(worker, headcount)

    assert match["status"] == "review"
    assert match["match_method"] == "identificadores_en_conflicto"
    assert len(json.loads(match["hc_json"])) == 2


def test_exact_name_does_not_override_strong_identifier_contradiction():
    worker = {
        "nombre_normalizado": "GABRIEL JIMENEZ VARGAS",
        "cliente_confirmado": "AURIGA",
        "nss": "11111111111",
    }
    headcount = [
        {
            "nombre_completo": "GABRIEL JIMENEZ VARGAS",
            "cliente": "AURIGA",
            "nss": "99999999999",
        }
    ]

    match = match_worker(worker, headcount)

    assert match["status"] == "review"
    assert match["match_method"] == "identificadores_en_conflicto"
