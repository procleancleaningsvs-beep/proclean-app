"""Tests for Microfase 4.0 parsers (nomina actual + CONTPAQ) and triple match."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from modules.nomina.asistencia_excel import build_asistencia_template_file
from modules.nomina.contpaq_excel import parse_contpaq
from modules.nomina.db import NominaBaseRow
from modules.nomina.parametros_excel import parse_nomina_actual
from modules.nomina.parametros_match import build_headcount_index, match_to_headcount
from modules.nomina.validators import parse_and_validate_asistencia_excel

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def carrier_bytes() -> bytes:
    return (FIXTURES / "nomina_carrier_anon.xlsx").read_bytes()


@pytest.fixture(scope="module")
def pepsi_bytes() -> bytes:
    return (FIXTURES / "nomina_pepsi_anon.xlsx").read_bytes()


@pytest.fixture(scope="module")
def contpaq_bytes() -> bytes:
    return (FIXTURES / "contpaq_anon.xlsx").read_bytes()


def test_master_v4_template_uses_horas_extra_normales():
    base = [
        NominaBaseRow(
            nombre_empleado="Juan Perez",
            cliente="Carrier",
            planta="A",
            puesto="Aux",
            banco="Banorte",
            cuenta="123",
            nss="11122233344",
        )
    ]
    data = build_asistencia_template_file(
        fecha_inicio=date(2026, 5, 1),
        fecha_fin=date(2026, 5, 7),
        cliente="Carrier",
        coordinador="QA",
        base_rows=base,
    )
    parsed = parse_and_validate_asistencia_excel(data, filename="master_v4.xlsx")
    assert parsed["cliente"] == "Carrier"
    assert parsed["semana"].startswith("01/05/2026")
    assert parsed["total_rows"] == 1
    row = parsed["rows"][0]
    # FE precargado en V1 (1 mayo 2026)
    assert row["dia_1_value"] == "FE"
    # campo renombrado
    assert "horas_extra_normales" in row
    # festivo_laborado eliminado del payload
    assert "festivo_laborado" not in row


def test_master_v4_no_holiday_does_not_preload_fe():
    base = [NominaBaseRow(nombre_empleado="Ana Lopez", cliente="GM", planta="", puesto="", banco="", cuenta="", nss="")]
    data = build_asistencia_template_file(
        fecha_inicio=date(2026, 4, 6),  # Mon
        fecha_fin=date(2026, 4, 12),
        cliente="GM",
        coordinador="QA",
        base_rows=base,
    )
    parsed = parse_and_validate_asistencia_excel(data, filename="master_v4.xlsx")
    row = parsed["rows"][0]
    daily = [row[f"dia_{i}_value"] for i in range(1, 8)]
    assert all(v == "" for v in daily), f"FE no debería precargarse en semana no festiva: {daily}"


def test_parse_nomina_carrier_extracts_base_params(carrier_bytes):
    out = parse_nomina_actual(carrier_bytes, filename="carrier.xlsx", cliente_hint="Carrier")
    assert out["total_rows"] == 4
    by_name = {r["nombre"]: r for r in out["rows"]}
    uno = by_name["Empleado Demo Uno"]
    assert uno["salario_operativo"] == 2470.0
    assert uno["valor_x_he"] == 68.75
    assert uno["numero_empleado"] == "121"
    sin_sal = by_name["Empleado Sin Salario"]
    assert sin_sal["salario_operativo"] is None
    assert any("SALARIO OPERATIVO" in w for w in sin_sal["warnings"])
    sin_he = by_name["Empleado Sin HE"]
    assert sin_he["valor_x_he"] is None
    assert any("VALOR X HE" in w for w in sin_he["warnings"])


def test_parse_nomina_pepsi_detects_frontera_localidades(pepsi_bytes):
    out = parse_nomina_actual(pepsi_bytes, filename="pepsi.xlsx", cliente_hint="Pepsi")
    assert out["total_rows"] == 4
    localidades = {(it["localidad_normalizada"], it["es_frontera"]) for it in out["localidades"]}
    assert ("tijuana", True) in localidades
    assert ("mexicali", True) in localidades
    assert ("apodaca", False) in localidades
    sin_loc = next(r for r in out["rows"] if r["nombre"] == "Empleada Sin Loc")
    assert any("FRONTERA TRUE pero LOCALIDAD vacía" in w for w in sin_loc["warnings"])


def test_parse_contpaq_handles_basic_columns(contpaq_bytes):
    out = parse_contpaq(contpaq_bytes, filename="contpaq.xlsx")
    assert out["total_rows"] == 4
    rows_by_codigo = {r["codigo_contpaq"]: r for r in out["rows"]}
    uno = rows_by_codigo["001"]
    assert uno["nss"] == "12345678901"
    assert uno["fecha_alta"] == "2024-04-02"
    assert uno["fecha_baja"] == "2025-03-06"
    assert uno["estatus"] == "A"
    sin_nss = rows_by_codigo["004"]
    assert sin_nss["nss"] is None


def test_match_to_headcount_priorities():
    hc = [
        {"nombre_completo": "Empleado Demo Uno", "nss": "11122233344", "cliente": "Carrier"},
        {"nombre_completo": "Empleado Demo Dos", "nss": "55566677788", "cliente": "Carrier"},
        {"nombre_completo": "Otro Nombre Distinto", "nss": "99988877766", "cliente": "Pepsi"},
    ]
    idx = build_headcount_index(hc)
    status, rec, score = match_to_headcount(
        nombre="Empleado Demo Uno", nss="11122233344", cliente=None, index=idx
    )
    assert status == "exact_nss"
    status, rec, score = match_to_headcount(
        nombre="Empleado Demo Uno", nss=None, cliente=None, index=idx
    )
    assert status == "exact_name"
    status, rec, score = match_to_headcount(
        nombre="Empleado Inexistente Z", nss=None, cliente=None, index=idx
    )
    assert status == "no_match_headcount"


def test_headcount_unavailable_yields_pending_status():
    idx = build_headcount_index([], unavailable_reason="HEADCOUNT_ONEDRIVE_URL no configurada")
    assert idx.unavailable_reason
    status, rec, score = match_to_headcount(
        nombre="Cualquiera", nss="12345678901", cliente=None, index=idx
    )
    assert status == "pending_headcount_unavailable"
    assert rec is None


def test_derive_smg_excel_false_keeps_learned_frontera(monkeypatch):
    from modules.nomina.config import WARN_FRONTERA_EXCEL_VS_LEARNED
    from modules.nomina.parametros_match import derive_smg_from_locality

    monkeypatch.setattr(
        "modules.nomina.parametros_match.localidad_is_frontera",
        lambda db_path, cliente, loc: True,
    )
    is_f, smg, ex, warns = derive_smg_from_locality(
        cliente="Pepsi",
        localidad="Tijuana",
        localidad_normalizada="tijuana",
        es_frontera_hint=False,
        year=2026,
        db_path=":memory:",
    )
    assert is_f is True
    assert smg == 440.87
    assert any(WARN_FRONTERA_EXCEL_VS_LEARNED in w for w in warns)


def test_upsert_localidad_no_auto_demotion(tmp_path):
    import sqlite3

    from modules.nomina.db import ensure_nomina_tables, list_localidades_frontera, upsert_localidades_frontera

    db = str(tmp_path / "loc.db")
    conn = sqlite3.connect(db)
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()
    iso = "2026-01-01 12:00:00"
    upsert_localidades_frontera(
        db,
        [
            {
                "cliente": "Pepsi",
                "localidad": "Tijuana",
                "localidad_normalizada": "tijuana",
                "es_frontera": True,
                "source_filename": "a.xlsx",
            }
        ],
        now_iso=iso,
    )
    _, _, warns = upsert_localidades_frontera(
        db,
        [
            {
                "cliente": "Pepsi",
                "localidad": "Tijuana",
                "localidad_normalizada": "tijuana",
                "es_frontera": False,
                "source_filename": "b.xlsx",
            }
        ],
        now_iso=iso,
    )
    assert any("localidad_frontera_demotion_blocked" in w for w in warns)
    rows = list_localidades_frontera(db, cliente="Pepsi")
    assert rows[0]["es_frontera"] == 1


def test_precheck_flags_block_calc_for_he_without_valor():
    from modules.nomina.config import WARN_BLOCK_CALC_MISSING_VALOR_HE
    from modules.nomina.parametros_match import append_parametro_precheck_warnings

    row = {
        "salario_operativo": 3000.0,
        "valor_x_he": None,
        "horas_extra_periodo": 8.0,
        "headcount_match_status": "exact_nss",
        "warnings": [],
        "editable_json": {},
    }
    append_parametro_precheck_warnings(row)
    assert any(WARN_BLOCK_CALC_MISSING_VALOR_HE in w for w in row["warnings"])
    assert row["editable_json"].get("block_calc_missing_valor_x_he_when_he") is True


def test_nss_merge_conflict_detects_cliente_mismatch():
    from modules.nomina.db import _nss_merge_conflict

    assert _nss_merge_conflict(
        {"nss": "12345678901", "cliente": "Carrier", "planta": "A", "salario_operativo": 100.0},
        {"nss": "12345678901", "cliente": "Pepsi", "planta": "A", "salario_operativo": 100.0},
    )
    assert not _nss_merge_conflict(
        {"nss": "12345678901", "cliente": "", "planta": "", "salario_operativo": None},
        {"nss": "12345678901", "cliente": "Pepsi", "planta": "A", "salario_operativo": 200.0},
    )


def test_smg_frontera_vs_general_via_config():
    from modules.nomina.config import get_smg_for_year, get_exento_he_for_year
    assert float(get_smg_for_year(2026, "GENERAL")) == 315.04
    assert float(get_smg_for_year(2026, "FRONTERA")) == 440.87
    assert float(get_exento_he_for_year(2026, "GENERAL")) == 236.28
    assert float(get_exento_he_for_year(2026, "FRONTERA")) == 330.65
