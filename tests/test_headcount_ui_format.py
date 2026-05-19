from __future__ import annotations

from modules.headcount.matching import enrich_row_warnings
from modules.headcount.ui_format import (
    agrupar_resumen_por_cliente,
    display_cell,
    display_cliente,
    display_registro_patronal,
    display_ubicacion,
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
    assert len(groups) == 2
    carrier = next(g for g in groups if g["cliente_key"] == "Carrier")
    assert carrier["activos_sua"] == 1
    assert carrier["ubicaciones_list"][0]["ubicacion_label"] == "Lark"


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
