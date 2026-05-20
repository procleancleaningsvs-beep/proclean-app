from __future__ import annotations

from modules.headcount.matching import (
    build_headcount_global_indexes,
    build_headcount_rafael_indexes,
    enrich_row_warnings,
    match_trabajador_sua,
    normalize_curp,
)


def _hc_record(
    *,
    curp: str,
    nss: str,
    patron: str,
    nombre: str = "GOMEZ LOPEZ JUAN",
) -> dict:
    return {
        "headcount_id": f"hc_{curp[:6]}",
        "cliente": "ACME",
        "ubicacion": "PLANTA 1",
        "puesto": "OP",
        "patron": patron,
        "fecha_ingreso": "01/01/2024",
        "status_operacion": "ALTA",
        "status_imss": "ALTA",
        "rfc_homoclave": "XAXX010101000",
        "curp": curp,
        "nss": nss,
        "apellido_paterno": "GOMEZ",
        "apellido_materno": "LOPEZ",
        "nombre": "JUAN",
        "nombre_completo": nombre,
    }


def _match_sua(curp: str, *, registros: list[dict]) -> dict:
    _, by_curp, by_nss, by_nombre = build_headcount_rafael_indexes(registros)
    _, g_curp, g_nss, g_nombre = build_headcount_global_indexes(registros)
    return match_trabajador_sua(
        {
            "nss_sua_original": "25998217738",
            "nss_normalizado": "25998217738",
            "nombre_sua_original": "GOMEZ LOPEZ JUAN",
            "nombre_normalizado": "GOMEZ LOPEZ JUAN",
            "curp": normalize_curp(curp),
            "movimiento_clave": "ALTA",
        },
        by_curp=by_curp,
        by_nss=by_nss,
        by_nombre=by_nombre,
        nombre_keys=list(by_nombre.keys()),
        global_by_curp=g_curp,
        global_by_nss=g_nss,
        global_by_nombre=g_nombre,
        global_nombre_keys=list(g_nombre.keys()),
    )


def test_match_curp_en_rafael():
    curp = "GOML850101HDFRRN09"
    registros = [_hc_record(curp=curp, nss="11111111111", patron="RAFAEL")]
    row = _match_sua(curp, registros=registros)
    enrich_row_warnings(row, dias_periodo=31, dup_warnings={})
    assert row["match_status"] == "MATCH_CURP"
    assert "SUA_ACTIVO_SIN_MATCH_HEADCOUNT" not in row["warnings"]


def test_match_curp_otro_patron_no_sin_match():
    curp = "HEPL660825MSPRNS06"
    registros = [_hc_record(curp=curp, nss="22222222222", patron="DESARROLLO IN F")]
    row = _match_sua(curp, registros=registros)
    enrich_row_warnings(row, dias_periodo=31, dup_warnings={})
    assert row["match_status"] == "MATCH_OTRO_PATRON"
    assert "PATRON_DIFERENTE" in row["warnings"]
    assert "SUA_ACTIVO_SIN_MATCH_HEADCOUNT" not in row["warnings"]
    assert row["matching_debug"]["encontrado_en_headcount_global"] is True
    assert row["matching_debug"]["metodo_match_global"] == "CURP"


def test_sin_match_cuando_no_existe_en_headcount():
    curp = "ZZZZ000000HZZZZZZ00"
    registros = [_hc_record(curp="GOML850101HDFRRN09", nss="11111111111", patron="RAFAEL")]
    _, by_curp, by_nss, by_nombre = build_headcount_rafael_indexes(registros)
    _, g_curp, g_nss, g_nombre = build_headcount_global_indexes(registros)
    row = match_trabajador_sua(
        {
            "nss_sua_original": "99999999999",
            "nss_normalizado": "99999999999",
            "nombre_sua_original": "PERSONA INEXISTENTE XYZ",
            "nombre_normalizado": "PERSONA INEXISTENTE XYZ",
            "curp": normalize_curp(curp),
            "movimiento_clave": "ALTA",
        },
        by_curp=by_curp,
        by_nss=by_nss,
        by_nombre=by_nombre,
        nombre_keys=list(by_nombre.keys()),
        global_by_curp=g_curp,
        global_by_nss=g_nss,
        global_by_nombre=g_nombre,
        global_nombre_keys=list(g_nombre.keys()),
    )
    enrich_row_warnings(row, dias_periodo=31, dup_warnings={})
    assert row["match_status"] == "SIN_MATCH"
    assert "SUA_ACTIVO_SIN_MATCH_HEADCOUNT" in row["warnings"]


def test_sin_match_nunca_si_curp_existe_global():
    curp = "LARK010816MGTZDRA5"
    registros = [
        _hc_record(curp=curp, nss="33333333333", patron="OTRO PATRON"),
    ]
    row = _match_sua(curp, registros=registros)
    assert row["match_status"] != "SIN_MATCH"
    assert row["match_status"] in {"MATCH_OTRO_PATRON", "MATCH_CURP", "MATCH_NSS"}


def test_dedupe_sua_detalle():
    from modules.headcount.services import _dedupe_detalle_sua

    rows = [
        {"nss_normalizado": "111", "curp": "A"},
        {"nss_normalizado": "111", "curp": "A"},
        {"nss_normalizado": "222", "curp": "B"},
    ]
    assert len(_dedupe_detalle_sua(rows)) == 2
