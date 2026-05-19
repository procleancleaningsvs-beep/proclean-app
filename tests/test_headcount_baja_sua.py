from __future__ import annotations

from modules.headcount.matching import (
    build_headcount_rafael_indexes,
    enrich_row_warnings,
    enrich_sua_worker_fields,
    estado_sua_al_corte,
    match_trabajador_sua,
    sua_es_activo_al_corte,
    sua_tiene_baja,
)


def test_baja_no_cuenta_como_activo_al_corte():
    w = enrich_sua_worker_fields({"movimiento_clave": "Baja", "nss_sua_original": "11-11-11-1111-1"})
    assert w["sua_tiene_baja"] is True
    assert w["sua_es_activo_al_corte"] is False
    assert w["estado_sua_al_corte"] == "Baja SUA"


def test_activo_sin_movimiento():
    w = enrich_sua_worker_fields({"movimiento_clave": "", "nss_sua_original": "22-22-22-2222-2"})
    assert w["sua_es_activo_al_corte"] is True
    assert estado_sua_al_corte("") == "Activo SUA"


def test_hc_activo_aparece_baja_en_sua_warning():
    registros = [
        {
            "headcount_id": "hc_1",
            "cliente": "ACME",
            "ubicacion": "P1",
            "puesto": "X",
            "patron": "RAFAEL",
            "fecha_ingreso": "01/01/2024",
            "status_operacion": "ALTA",
            "status_imss": "ALTA",
            "rfc_homoclave": "",
            "curp": "",
            "nss": "11111111111",
            "apellido_paterno": "A",
            "apellido_materno": "B",
            "nombre": "C",
            "nombre_completo": "A B C",
        }
    ]
    _, by_curp, by_nss, by_nombre = build_headcount_rafael_indexes(registros)
    row = match_trabajador_sua(
        enrich_sua_worker_fields(
            {
                "nss_sua_original": "11-11-11-1111-1",
                "nss_normalizado": "11111111111",
                "nombre_sua_original": "A B C",
                "movimiento_clave": "Baja",
            }
        ),
        by_curp=by_curp,
        by_nss=by_nss,
        by_nombre=by_nombre,
        nombre_keys=list(by_nombre.keys()),
    )
    enrich_row_warnings(row, dias_periodo=None, dup_warnings={})
    assert "HEADCOUNT_ACTIVO_APARECE_BAJA_EN_SUA" in row["warnings"]
    assert "HEADCOUNT_ACTIVO_NO_APARECE_EN_SUA" not in row["warnings"]
    assert "SUA_ACTIVO_SIN_MATCH_HEADCOUNT" not in row["warnings"]


def test_baja_conciliada_sin_warning_critico():
    registros = [
        {
            "headcount_id": "hc_1",
            "cliente": "ACME",
            "ubicacion": "P1",
            "puesto": "X",
            "patron": "RAFAEL",
            "fecha_ingreso": "01/01/2024",
            "status_operacion": "BAJA",
            "status_imss": "BAJA",
            "rfc_homoclave": "",
            "curp": "",
            "nss": "11111111111",
            "apellido_paterno": "A",
            "apellido_materno": "B",
            "nombre": "C",
            "nombre_completo": "A B C",
        }
    ]
    _, by_curp, by_nss, by_nombre = build_headcount_rafael_indexes(registros)
    row = match_trabajador_sua(
        enrich_sua_worker_fields(
            {
                "nss_sua_original": "11-11-11-1111-1",
                "nss_normalizado": "11111111111",
                "movimiento_clave": "Baja",
            }
        ),
        by_curp=by_curp,
        by_nss=by_nss,
        by_nombre=by_nombre,
        nombre_keys=list(by_nombre.keys()),
    )
    enrich_row_warnings(row, dias_periodo=None, dup_warnings={})
    assert row.get("info_estado") == "BAJA_CONCILIADA"
    assert "HEADCOUNT_BAJA_APARECE_ACTIVO_EN_SUA" not in row["warnings"]


def test_activo_sin_match_warning():
    _, by_curp, by_nss, by_nombre = build_headcount_rafael_indexes([])
    row = match_trabajador_sua(
        enrich_sua_worker_fields({"movimiento_clave": "", "nss_sua_original": "33-33-33-3333-3"}),
        by_curp=by_curp,
        by_nss=by_nss,
        by_nombre=by_nombre,
        nombre_keys=[],
    )
    enrich_row_warnings(row, dias_periodo=None, dup_warnings={})
    assert row["sua_es_activo_al_corte"]
    assert "SUA_ACTIVO_SIN_MATCH_HEADCOUNT" in row["warnings"]


def test_baja_sin_match_separate_warning():
    _, by_curp, by_nss, by_nombre = build_headcount_rafael_indexes([])
    row = match_trabajador_sua(
        enrich_sua_worker_fields({"movimiento_clave": "Baja", "nss_sua_original": "44-44-44-4444-4"}),
        by_curp=by_curp,
        by_nss=by_nss,
        by_nombre=by_nombre,
        nombre_keys=[],
    )
    enrich_row_warnings(row, dias_periodo=None, dup_warnings={})
    assert not sua_es_activo_al_corte(row.get("sua_movimiento_clave"))
    assert sua_tiene_baja(row.get("sua_movimiento_clave"))
    assert "SUA_BAJA_SIN_MATCH_HEADCOUNT" in row["warnings"]
