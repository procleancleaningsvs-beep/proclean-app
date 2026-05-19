from __future__ import annotations

from modules.headcount.matching import (
    build_headcount_rafael_indexes,
    match_trabajador_sua,
    normalize_curp,
    normalize_nss,
    normalize_text,
    patron_es_rafael,
)


def test_normalize_nss():
    assert normalize_nss("25-99-82-1773-8") == "25998217738"


def test_normalize_text_accent():
    assert normalize_text("Rafael") == "RAFAEL"
    assert normalize_text("  José   María  ") == "JOSE MARIA"


def test_patron_rafael_variants():
    assert patron_es_rafael("RAFAEL")
    assert patron_es_rafael("Rafael")
    assert not patron_es_rafael("OTRO")


def test_match_priority_curp_before_nss():
    registros = [
        {
            "headcount_id": "hc_1",
            "cliente": "ACME",
            "ubicacion": "PLANTA 1",
            "puesto": "OP",
            "patron": "RAFAEL",
            "fecha_ingreso": "01/01/2024",
            "status_operacion": "ALTA",
            "status_imss": "ALTA",
            "rfc_homoclave": "XAXX010101000",
            "curp": "GOML850101HDFRRN09",
            "nss": "11111111111",
            "apellido_paterno": "GOMEZ",
            "apellido_materno": "LOPEZ",
            "nombre": "JUAN",
            "nombre_completo": "GOMEZ LOPEZ JUAN",
        }
    ]
    _, by_curp, by_nss, by_nombre = build_headcount_rafael_indexes(registros)
    row = match_trabajador_sua(
        {
            "nss_sua_original": "25-99-82-1773-8",
            "nss_normalizado": "25998217738",
            "nombre_sua_original": "GOMEZ LOPEZ JUAN",
            "nombre_normalizado": "GOMEZ LOPEZ JUAN",
            "curp": normalize_curp("GOML850101HDFRRN09"),
        },
        by_curp=by_curp,
        by_nss=by_nss,
        by_nombre=by_nombre,
        nombre_keys=list(by_nombre.keys()),
    )
    assert row["match_status"] == "MATCH_CURP"
