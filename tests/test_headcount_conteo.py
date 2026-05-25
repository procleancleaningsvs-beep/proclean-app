from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch

from modules.headcount import services as svc
from modules.headcount.blueprint import conteo_personal
from modules.headcount.matching import _is_status_activo_operacion
from modules.headcount.services import normalize_status, resumen_cliente_view


def _patch_df(df: pd.DataFrame):
    return patch.object(svc, "obtener_df_headcount", return_value=df)


def _sample_headers(*, status_col: str = "STATUS OPERACION") -> list[str]:
    cols = [
        "CLIENTE",
        "UBICACION",
        "PUESTO",
        "SUELDO DIARIO",
        "SUELDO SEMANAL",
        "PATRON",
        "FECHA DE INGRESO",
        status_col,
        "STATUS IMSS",
        "RFC HOMOCLAVE",
        "CP FISCAL",
        "CURP",
        "NSS",
        "APELLIDO PATERNO",
        "APELLIDO MATERNO",
        "NOMBRE",
        "NOMBRE COMPLETO",
        "GENERO",
        "FECHA DE NACIMIENTO",
        "LUGAR DE NACIMIENTO",
    ]
    return cols


def test_normalize_status_cleans_contaminated_values():
    assert normalize_status(None) == ""
    assert normalize_status(float("nan")) == ""
    assert normalize_status("NAN match_name") == ""
    assert normalize_status("  activo  ") == "ACTIVO"
    assert normalize_status("VIGENTE") == "VIGENTE"


def test_obtener_registros_with_estatus_column():
    headers = _sample_headers(status_col="ESTATUS")
    row = [
        "ACME",
        "Planta 1",
        "Op",
        100,
        700,
        "RAFAEL",
        "2024-01-01",
        "ACTIVO",
        "ALTA",
        "RFC1",
        "12345",
        "CURP1",
        "111",
        "PEREZ",
        "LOPEZ",
        "JUAN",
        "JUAN PEREZ LOPEZ",
        "M",
        "2000-01-01",
        "CDMX",
    ]
    df = pd.DataFrame([headers, row])
    with _patch_df(df):
        regs = svc.obtener_registros_headcount(solo_activos=False)
    assert len(regs) == 1
    assert regs[0]["cliente"] == "ACME"
    assert regs[0]["status_operacion"] == "ACTIVO"
    assert resumen_cliente_view(regs)["activos"] == 1


def test_obtener_registros_without_status_column_still_loads_rows():
    headers = [c for c in _sample_headers() if c not in {"STATUS OPERACION", "STATUS IMSS"}]
    row = [
        "ACME",
        "Planta",
        "Op",
        100,
        700,
        "RAFAEL",
        "2024-01-01",
        "RFC",
        "12345",
        "CURP",
        "111",
        "A",
        "B",
        "C",
        "JUAN PEREZ",
        "M",
        "2000-01-01",
        "CDMX",
    ]
    df = pd.DataFrame([headers, row])
    with _patch_df(df):
        regs = svc.obtener_registros_headcount(solo_activos=False)
    assert len(regs) == 1
    assert regs[0]["status_operacion"] == "SIN ESTATUS"
    assert resumen_cliente_view(regs)["activos"] == 0


def test_obtener_registros_empty_dataframe():
    df = pd.DataFrame()
    with _patch_df(df):
        assert svc.obtener_registros_headcount() == []


def test_obtener_registros_nan_status_and_missing_cliente_planta():
    headers = _sample_headers()
    row_nan = [
        "ACME",
        np.nan,
        "Op",
        np.nan,
        np.nan,
        "RAFAEL",
        np.nan,
        np.nan,
        np.nan,
        np.nan,
        np.nan,
        np.nan,
        np.nan,
        "PEREZ",
        "LOPEZ",
        "JUAN",
        np.nan,
        np.nan,
        np.nan,
        np.nan,
    ]
    row_bad_name = [
        "ACME",
        "Planta",
        "Op",
        100,
        700,
        "RAFAEL",
        "2024-01-01",
        "NAN match_name",
        "ALTA",
        "RFC",
        "12345",
        "CURP",
        "222",
        "A",
        "B",
        "C",
        "NAN match_name",
        "M",
        "2000-01-01",
        "CDMX",
    ]
    df = pd.DataFrame([headers, row_nan, row_bad_name])
    with _patch_df(df):
        regs = svc.obtener_registros_headcount(solo_activos=False)
    assert len(regs) == 2
    by_name = {r["nombre_completo"]: r for r in regs}
    assert by_name["JUAN PEREZ LOPEZ"]["status_operacion"] == "SIN ESTATUS"
    assert by_name["C A B"]["status_operacion"] == "ALTA"
    resumen = resumen_cliente_view(regs)
    assert resumen["activos"] == 1
    assert resumen["sin_ubicacion"] == 1
    assert resumen["sin_nss"] == 1


def test_solo_activos_accepts_vigente_and_alta():
    headers = _sample_headers()
    rows = [
        ["ACME", "P1", "Op", 100, 700, "RAFAEL", "2024-01-01", "ALTA", "ALTA", "", "", "C1", "1", "", "", "", "UNO", "M", "", ""],
        ["ACME", "P2", "Op", 100, 700, "RAFAEL", "2024-01-01", "VIGENTE", "ALTA", "", "", "C2", "2", "", "", "", "DOS", "M", "", ""],
        ["ACME", "P3", "Op", 100, 700, "RAFAEL", "2024-01-01", "BAJA", "BAJA", "", "", "C3", "3", "", "", "", "TRES", "M", "", ""],
    ]
    df = pd.DataFrame([headers, *rows])
    with _patch_df(df):
        regs = svc.obtener_registros_headcount(solo_activos=True)
    assert len(regs) == 2
    assert all(_is_status_activo_operacion(normalize_status(r["status_operacion"])) for r in regs)


def test_conteo_personal_route_returns_200_with_estatus_column():
    from pathlib import Path
    from flask import Flask, g

    repo = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(repo / "templates"))
    app.config["DATABASE"] = ":memory:"
    app.config["SECRET_KEY"] = "test"
    from modules.headcount.blueprint import register_headcount

    register_headcount(app)

    headers = _sample_headers(status_col="ESTATUS")
    row = [
        "ACME",
        "Planta",
        "Op",
        np.float64(100),
        np.int64(700),
        "RAFAEL",
        pd.Timestamp("2024-01-01"),
        "ALTA",
        "ALTA",
        "RFC",
        "12345",
        "CURP",
        "NSS123",
        "A",
        "B",
        "C",
        "JUAN PEREZ",
        "M",
        pd.Timestamp("2000-01-01"),
        "CDMX",
    ]
    df = pd.DataFrame([headers, row])

    with _patch_df(df):
        with app.test_request_context("/headcount/conteo-personal"):
            g.user = {"id": 1, "role": "admin", "username": "admin"}
            html = conteo_personal()
    assert isinstance(html, str)
    assert "Conteo de personal activo" in html
    assert "JUAN PEREZ" in html


def test_headcount_unavailable_returns_empty_structure():
    with patch.object(svc, "obtener_df_headcount", side_effect=ValueError("OneDrive down")):
        regs = svc.obtener_registros_headcount()
    assert regs == []
    resumen = resumen_cliente_view(regs)
    assert resumen == {
        "total": 0,
        "activos": 0,
        "bajas": 0,
        "sin_curp": 0,
        "sin_nss": 0,
        "sin_ubicacion": 0,
    }
