from __future__ import annotations

from modules.headcount.matching import enrich_row_warnings
from datetime import date

from modules.headcount.services import calc_metricas_desarrollo_inf, filtrar_detalle
from modules.headcount.ui_format import (
    agrupar_resumen_por_cliente,
    build_cliente_cards_for_ui,
    display_cell,
    display_cliente,
    display_fecha_ingreso,
    display_registro_patronal,
    display_ubicacion,
    parse_fecha_ingreso,
    resumen_sin_cliente_card,
)


def test_display_cell_hides_nan():
    assert display_cell("nan") == "—"
    assert display_cell(None) == "—"
    assert display_cell("Carrier") == "Carrier"


def test_display_cliente_ubicacion_empty():
    assert display_cliente("") == "Sin cliente"
    assert display_ubicacion("nan") == "Sin ubicación"


def test_display_registro_patronal_rejects_nombre_placeholder():
    assert display_registro_patronal("Nombre", "Y37-52430-10-2") == "Y37-52430-10-2"
    assert display_registro_patronal("Nombre", "") == "No detectado"


def test_agrupar_resumen_por_cliente():
    detalle = [
        {
            "cliente_headcount": "Carrier",
            "ubicacion_headcount": "Lark",
            "sua_es_activo_al_corte": True,
            "sua_tiene_baja": False,
            "match_status": "MATCH_NSS",
            "warnings": [],
            "info_estado": "",
        },
        {
            "cliente_headcount": "",
            "ubicacion_headcount": "",
            "sua_es_activo_al_corte": False,
            "sua_tiene_baja": True,
            "match_status": "MATCH_NSS",
            "warnings": [],
            "info_estado": "BAJA_CONCILIADA",
        },
    ]
    groups = agrupar_resumen_por_cliente(detalle)
    assert len(groups) == 1
    carrier = next(g for g in groups if g["cliente_key"] == "Carrier")
    assert carrier["activos_sua"] == 1
    assert carrier["ubicaciones_list"][0]["ubicacion_label"] == "Lark"


def test_parse_fecha_ingreso_variants():
    assert parse_fecha_ingreso("2024-05-15") == date(2024, 5, 15)
    assert parse_fecha_ingreso("15/05/2024") == date(2024, 5, 15)
    assert parse_fecha_ingreso("nan") is None
    assert display_fecha_ingreso(None) == "—"
    assert display_fecha_ingreso("2024-05-15") == "15/05/2024"


def test_resumen_sin_cliente_card_counts_sin_match():
    detalle = [
        {"match_status": "SIN_MATCH", "sua_es_activo_al_corte": True},
        {"match_status": "MATCH_NSS", "sua_es_activo_al_corte": True, "cliente_headcount": "X"},
    ]
    card = resumen_sin_cliente_card(detalle)
    assert card["total_registros"] == 1
    assert card["activos_sua"] == 1


def test_filtrar_detalle_ubicacion_vacia_requires_provided():
    detalle = [
        {"cliente_headcount": "Carrier", "ubicacion_headcount": ""},
        {"cliente_headcount": "Carrier", "ubicacion_headcount": "A"},
    ]
    out = filtrar_detalle(detalle, cliente="Carrier", ubicacion="", ubicacion_provided=True)
    assert len(out) == 1
    assert out[0]["ubicacion_headcount"] == ""


def test_build_cliente_cards_excludes_empty_cliente_from_grid():
    detalle = [
        {"cliente_headcount": "Carrier", "ubicacion_headcount": "A", "sua_es_activo_al_corte": True, "match_status": "MATCH_NSS", "warnings": [], "info_estado": ""},
        {"cliente_headcount": "", "match_status": "SIN_MATCH", "sua_es_activo_al_corte": True, "warnings": [], "info_estado": ""},
    ]
    clientes, sin_card = build_cliente_cards_for_ui(detalle)
    assert len(clientes) == 1
    assert sin_card["total_registros"] == 1


def test_calc_metricas_desarrollo_inf(monkeypatch):
    corte = date(2026, 5, 31)
    registros = [
        {
            "patron": "DESARROLLO IN F",
            "status_operacion": "ALTA",
            "fecha_ingreso": "2025-11-01",
        },
        {
            "patron": "DESARROLLO IN F",
            "status_operacion": "ALTA",
            "fecha_ingreso": "2024-01-01",
        },
        {
            "patron": "DESARROLLO IN F",
            "status_operacion": "BAJA",
            "fecha_ingreso": "2020-01-01",
        },
        {
            "patron": "RAFAEL",
            "status_operacion": "ALTA",
            "fecha_ingreso": "2020-01-01",
        },
    ]

    def fake_obtener(*_a, **_k):
        return registros

    monkeypatch.setattr("modules.headcount.services.obtener_registros_headcount", fake_obtener)
    out = calc_metricas_desarrollo_inf("2026-05-31")
    assert out["desarrollo_inf_mas_6_meses"] == 2
    assert out["desarrollo_inf_mas_1_anio"] == 1


def test_no_status_imss_inconsistente_warning():
    row = {
        "match_status": "MATCH_NSS",
        "headcount_id": "hc_1",
        "sua_tiene_baja": False,
        "sua_es_activo_al_corte": True,
        "sua_movimiento_clave": "",
        "status_operacion_headcount": "ALTA",
        "status_imss_headcount": "BAJA",
        "cliente_headcount": "ACME",
        "ubicacion_headcount": "P1",
        "patron_headcount": "RAFAEL",
        "dias": 30,
    }
    enrich_row_warnings(row, dias_periodo=31, dup_warnings={})
    assert "STATUS_IMSS_INCONSISTENTE" not in row["warnings"]
