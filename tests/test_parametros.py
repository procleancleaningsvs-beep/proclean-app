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
        {"nombre_completo": "Empleado Demo Uno", "nss": "11122233344", "cliente": "Carrier", "status_operacion": "ALTA"},
        {"nombre_completo": "Empleado Demo Dos", "nss": "55566677788", "cliente": "Carrier", "status_operacion": "ALTA"},
        {"nombre_completo": "Otro Nombre Distinto", "nss": "99988877766", "cliente": "Pepsi", "status_operacion": "ALTA"},
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


def test_detect_cliente_from_filename():
    from modules.nomina.parametros_excel import detect_cliente_from_import

    cliente, src = detect_cliente_from_import(
        filename="nomina_carrier_mayo.xlsx",
        sheet_name="Hoja1",
        row_clientes=[],
    )
    assert cliente == "Carrier"
    assert src == "nombre_archivo_o_hoja"


def test_detect_cliente_requires_fallback_when_unknown():
    from modules.nomina.parametros_excel import detect_cliente_from_import

    cliente, src = detect_cliente_from_import(
        filename="datos.xlsx",
        sheet_name="Sheet1",
        row_clientes=[],
    )
    assert cliente is None
    assert src is None


def test_compute_parametros_stats_from_headcount(tmp_path):
    import sqlite3

    from modules.nomina.db import ensure_nomina_tables, upsert_empleado_parametros, save_parametros_import
    from modules.nomina.parametros_consolidado import compute_parametros_stats, RECORD_EXTERNAL_CONTPAQ

    db = str(tmp_path / "stats.db")
    conn = sqlite3.connect(db)
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()

    hc = [
        {"nombre_completo": "Activo Uno", "nss": "111", "cliente": "Carrier", "status_operacion": "ALTA"},
        {"nombre_completo": "Activo Dos", "nss": "222", "cliente": "Carrier", "status_operacion": "ALTA"},
        {"nombre_completo": "Baja Tres", "nss": "333", "cliente": "Carrier", "status_operacion": "BAJA"},
    ]
    iso = "2026-01-01 12:00:00"
    imp_id = save_parametros_import(
        db,
        {"tipo_importacion": "CONTPAQ", "cliente": "", "source_filename": "c.xlsx", "total_rows": 5},
        created_by=None,
        now_iso=iso,
    )
    upsert_empleado_parametros(
        db,
        [
            {
                "nombre": "Externo CONTPAQ",
                "nombre_normalizado": "EXTERNO CONTPAQ",
                "nss": "999",
                "headcount_match_status": "no_match_headcount",
                "contpaq_match_status": "imported",
                "record_kind": RECORD_EXTERNAL_CONTPAQ,
                "warnings": [],
                "editable_json": {},
            }
        ],
        import_id=imp_id,
        now_iso=iso,
    )
    stats = compute_parametros_stats(db, hc)
    assert stats["activos_headcount"] == 2
    assert stats["registros_externos_sin_vinculo"] >= 1


def test_legacy_stats_do_not_inflate_activos_headcount(tmp_path):
    import sqlite3

    from modules.nomina.db import ensure_nomina_tables, get_parametros_stats, upsert_empleado_parametros, save_parametros_import
    from modules.nomina.parametros_consolidado import RECORD_EXTERNAL_CONTPAQ

    db = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db)
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()
    iso = "2026-01-01 12:00:00"
    imp_id = save_parametros_import(
        db,
        {"tipo_importacion": "CONTPAQ", "cliente": "", "source_filename": "c.xlsx", "total_rows": 1039},
        created_by=None,
        now_iso=iso,
    )
    rows = [
        {
            "nombre": f"Externo {i}",
            "nombre_normalizado": f"EXTERNO {i}",
            "nss": f"9000000000{i}",
            "headcount_match_status": "no_match_headcount",
            "contpaq_match_status": "imported",
            "record_kind": RECORD_EXTERNAL_CONTPAQ,
            "warnings": [],
            "editable_json": {},
        }
        for i in range(5)
    ]
    upsert_empleado_parametros(db, rows, import_id=imp_id, now_iso=iso)
    stats = get_parametros_stats(db, None)
    assert stats["stats_mode"] == "legacy"
    assert stats["activos_headcount"] == 0
    assert stats["total_registros_parametros"] == 5
    assert stats["registros_contpaq_importados"] == 1039


def test_manual_link_merges_and_deactivates_external(tmp_path):
    import sqlite3

    from modules.nomina.db import ensure_nomina_tables, upsert_empleado_parametros, save_parametros_import
    from modules.nomina.parametros_consolidado import (
        RECORD_EXTERNAL_NOMINA,
        RECORD_HEADCOUNT_CANONICAL,
        apply_manual_headcount_link,
        build_consolidado_view,
    )

    db = str(tmp_path / "link.db")
    conn = sqlite3.connect(db)
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()
    iso = "2026-01-01 12:00:00"
    imp_id = save_parametros_import(
        db,
        {"tipo_importacion": "NOMINA_ACTUAL", "cliente": "Carrier", "source_filename": "n.xlsx", "total_rows": 1},
        created_by=None,
        now_iso=iso,
    )
    upsert_empleado_parametros(
        db,
        [
            {
                "nombre": "Pedro Alfonso Martinez Vaca",
                "nombre_normalizado": "PEDRO ALFONSO MARTINEZ VACA",
                "nss": "11122233344",
                "cliente": "Carrier",
                "record_kind": RECORD_HEADCOUNT_CANONICAL,
                "headcount_match_status": "headcount_canonical",
                "warnings": [],
                "editable_json": {},
            },
            {
                "nombre": "Pedro Martinez",
                "nombre_normalizado": "PEDRO MARTINEZ",
                "nss": None,
                "cliente": "Carrier",
                "salario_operativo": 3000.0,
                "valor_x_he": 80.0,
                "nomina_match_status": "imported",
                "record_kind": RECORD_EXTERNAL_NOMINA,
                "headcount_match_status": "no_match_headcount",
                "warnings": [],
                "editable_json": {},
            },
        ],
        import_id=imp_id,
        now_iso=iso,
    )
    conn = sqlite3.connect(db)
    external_id = conn.execute(
        "SELECT id FROM nomina_empleado_parametros WHERE nombre = 'Pedro Martinez'"
    ).fetchone()[0]
    conn.close()

    ok = apply_manual_headcount_link(
        db,
        int(external_id),
        headcount_nss="11122233344",
        headcount_nombre="Pedro Alfonso Martinez Vaca",
        headcount_cliente="Carrier",
        linked_by=1,
        now_iso=iso,
    )
    assert ok

    conn = sqlite3.connect(db)
    ext_active = conn.execute(
        "SELECT is_active FROM nomina_empleado_parametros WHERE id = ?", (int(external_id),)
    ).fetchone()[0]
    canonical = conn.execute(
        "SELECT salario_operativo, valor_x_he, headcount_match_status FROM nomina_empleado_parametros WHERE nss = '11122233344'"
    ).fetchone()
    conn.close()
    assert int(ext_active) == 0
    assert float(canonical[0]) == 3000.0
    assert float(canonical[1]) == 80.0
    assert canonical[2] == "manual_link"

    hc = [
        {
            "nombre_completo": "Pedro Alfonso Martinez Vaca",
            "nss": "11122233344",
            "cliente": "Carrier",
            "status_operacion": "ALTA",
        }
    ]
    view = build_consolidado_view(db, hc, limit=100)
    externals = [r for r in view if r.get("is_external")]
    canonical_rows = [r for r in view if r.get("is_canonical")]
    assert len(canonical_rows) == 1
    assert canonical_rows[0]["salario_operativo"] == 3000.0
    assert len(externals) == 0


def test_limpiar_nomina_preserves_headcount_canonical(tmp_path):
    from modules.nomina.db import ensure_nomina_tables, upsert_empleado_parametros, save_parametros_import
    from modules.nomina.parametros_consolidado import RECORD_HEADCOUNT_CANONICAL, limpiar_importaciones_nomina
    import sqlite3

    db = str(tmp_path / "clean_nom.db")
    conn = sqlite3.connect(db)
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()
    iso = "2026-01-01 12:00:00"
    imp_id = save_parametros_import(
        db,
        {"tipo_importacion": "NOMINA_ACTUAL", "cliente": "Carrier", "source_filename": "n.xlsx", "total_rows": 1},
        created_by=None,
        now_iso=iso,
    )
    upsert_empleado_parametros(
        db,
        [
            {
                "nombre": "Activo Uno",
                "nombre_normalizado": "ACTIVO UNO",
                "nss": "111",
                "cliente": "Carrier",
                "salario_operativo": 2500.0,
                "nomina_match_status": "imported",
                "record_kind": RECORD_HEADCOUNT_CANONICAL,
                "headcount_match_status": "exact_nss",
                "editable_json": {"manual_headcount_nss": "111"},
                "warnings": [],
            }
        ],
        import_id=imp_id,
        now_iso=iso,
    )
    limpiar_importaciones_nomina(db, now_iso=iso)
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT is_active, salario_operativo, editable_json FROM nomina_empleado_parametros WHERE nss = '111'"
    ).fetchone()
    conn.close()
    assert int(row[0]) == 1
    assert row[1] is None
    assert "manual_headcount_nss" in (row[2] or "")


def test_search_active_headcount_by_token():
    from modules.nomina.parametros_consolidado import search_active_headcount

    hc = [
        {
            "nombre_completo": "Pedro Alfonso Martinez Vaca",
            "nss": "11122233344",
            "cliente": "Carrier",
            "patron": "Planta A",
            "status_operacion": "ALTA",
        }
    ]
    hits = search_active_headcount(hc, "Martinez")
    assert len(hits) == 1
    hits2 = search_active_headcount(hc, "Pedro")
    assert len(hits2) == 1


def test_smg_frontera_vs_general_via_config():
    from modules.nomina.config import get_smg_for_year, get_exento_he_for_year
    assert float(get_smg_for_year(2026, "GENERAL")) == 315.04
    assert float(get_smg_for_year(2026, "FRONTERA")) == 440.87
    assert float(get_exento_he_for_year(2026, "GENERAL")) == 236.28
    assert float(get_exento_he_for_year(2026, "FRONTERA")) == 330.65


# --- Microfase 2: bandeja de conciliación ---


def test_detect_periodo_from_filename_mayo():
    from modules.nomina.parametros_excel import detect_periodo_from_import

    periodo, src = detect_periodo_from_import(filename="Carrier 1 al 7 may.xlsx")
    assert periodo == "1 al 7 may"
    assert src == "nombre_archivo"


def test_suggest_headcount_matches_pedro_martinez():
    from modules.nomina.parametros_conciliacion import suggest_headcount_matches

    row = {"nombre": "Pedro Martinez", "nss": None, "cliente": "Carrier", "planta": "Planta F"}
    hc = [
        {
            "nombre_completo": "Pedro Alfonso Martinez Vaca",
            "nss": "11122233344",
            "cliente": "Pepsi",
            "patron": "Planta F",
            "puesto": "Aux",
            "status_operacion": "ALTA",
        },
        {
            "nombre_completo": "Pedro Martinez López",
            "nss": "55566677788",
            "cliente": "Carrier",
            "patron": "Planta A",
            "puesto": "Aux",
            "status_operacion": "ALTA",
        },
    ]
    suggestions = suggest_headcount_matches(row, hc, limit=5)
    assert suggestions
    assert suggestions[0]["nombre_completo"] == "Pedro Alfonso Martinez Vaca"
    assert suggestions[0]["etiqueta"] in {"Alta", "Media"}


def test_ignore_warning_excludes_from_active_pendientes(tmp_path):
    import sqlite3

    from modules.nomina.config import WARN_HEADCOUNT_ACTIVO_SIN_SALARIO
    from modules.nomina.db import ensure_nomina_tables, upsert_empleado_parametros, save_parametros_import
    from modules.nomina.parametros_conciliacion import (
        build_conciliacion_inbox,
        build_parametro_detail,
        get_active_warnings,
        ignore_parametro_warning,
    )
    from modules.nomina.parametros_consolidado import RECORD_HEADCOUNT_CANONICAL

    db = str(tmp_path / "ignore.db")
    conn = sqlite3.connect(db)
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()
    iso = "2026-01-01 12:00:00"
    imp_id = save_parametros_import(
        db,
        {"tipo_importacion": "NOMINA_ACTUAL", "cliente": "Carrier", "source_filename": "n.xlsx", "total_rows": 1},
        created_by=None,
        now_iso=iso,
    )
    upsert_empleado_parametros(
        db,
        [
            {
                "nombre": "Activo Sin Salario",
                "nombre_normalizado": "ACTIVO SIN SALARIO",
                "nss": "111",
                "cliente": "Carrier",
                "record_kind": RECORD_HEADCOUNT_CANONICAL,
                "headcount_match_status": "headcount_canonical",
                "warnings": [f"{WARN_HEADCOUNT_ACTIVO_SIN_SALARIO}: falta salario"],
                "editable_json": {},
            }
        ],
        import_id=imp_id,
        now_iso=iso,
    )
    conn = sqlite3.connect(db)
    row_id = conn.execute("SELECT id FROM nomina_empleado_parametros LIMIT 1").fetchone()[0]
    conn.close()

    hc = [{"nombre_completo": "Activo Sin Salario", "nss": "111", "cliente": "Carrier", "status_operacion": "ALTA"}]
    inbox_before = build_conciliacion_inbox(db, hc, filtro="sin_salario")
    assert inbox_before["total"] == 1
    assert len(inbox_before["rows"]) == 1

    ok = ignore_parametro_warning(
        db,
        int(row_id),
        warning_codes=[WARN_HEADCOUNT_ACTIVO_SIN_SALARIO],
        motivo="Validado manualmente por nómina",
        user_id=1,
        now_iso=iso,
    )
    assert ok

    conn = sqlite3.connect(db)
    raw = conn.execute("SELECT warnings_json, editable_json FROM nomina_empleado_parametros WHERE id = ?", (row_id,)).fetchone()
    conn.close()
    warnings = __import__("json").loads(raw[0])
    editable = __import__("json").loads(raw[1])
    row = {"warnings": warnings, "editable_json": editable}
    assert get_active_warnings(row) == []
    assert any(i.get("code") == WARN_HEADCOUNT_ACTIVO_SIN_SALARIO for i in editable.get("ignored_warnings") or [])

    inbox_after = build_conciliacion_inbox(db, hc, filtro="sin_salario")
    assert inbox_after["total"] == 0
    assert len(inbox_after["rows"]) == 0

    detail = build_parametro_detail(db, int(row_id), hc)
    assert detail is not None
    assert detail["conciliacion"]["warnings_ignorados"]


def test_post_import_rematch_respects_manual_link_first(tmp_path):
    import sqlite3

    from modules.nomina.db import ensure_nomina_tables, upsert_empleado_parametros, save_parametros_import
    from modules.nomina.parametros_conciliacion import post_import_rematch_controlled
    from modules.nomina.parametros_consolidado import RECORD_EXTERNAL_NOMINA

    db = str(tmp_path / "rematch_manual.db")
    conn = sqlite3.connect(db)
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()
    iso = "2026-01-01 12:00:00"
    imp_id = save_parametros_import(
        db,
        {"tipo_importacion": "NOMINA_ACTUAL", "cliente": "Carrier", "source_filename": "n.xlsx", "total_rows": 1},
        created_by=None,
        now_iso=iso,
    )
    upsert_empleado_parametros(
        db,
        [
            {
                "nombre": "Pedro Martinez",
                "nombre_normalizado": "PEDRO MARTINEZ",
                "nss": "99988877766",
                "cliente": "Carrier",
                "record_kind": RECORD_EXTERNAL_NOMINA,
                "headcount_match_status": "no_match_headcount",
                "nomina_match_status": "imported",
                "warnings": [],
                "editable_json": {"manual_headcount_nss": "11122233344"},
            }
        ],
        import_id=imp_id,
        now_iso=iso,
    )
    hc = [
        {"nombre_completo": "Pedro Alfonso Martinez Vaca", "nss": "11122233344", "cliente": "Carrier", "status_operacion": "ALTA"},
        {"nombre_completo": "Otro Nombre", "nss": "99988877766", "cliente": "Carrier", "status_operacion": "ALTA"},
    ]
    summary = post_import_rematch_controlled(db, import_id=imp_id, headcount_rows=hc, now_iso=iso)
    assert summary["manual_link"] == 1
    assert summary["exactos"] == 0

    conn = sqlite3.connect(db)
    ms = conn.execute("SELECT headcount_match_status FROM nomina_empleado_parametros WHERE last_import_id = ?", (imp_id,)).fetchone()[0]
    conn.close()
    assert ms == "no_match_headcount"


def test_post_import_rematch_does_not_auto_fuse_doubtful(tmp_path):
    import sqlite3

    from modules.nomina.db import ensure_nomina_tables, upsert_empleado_parametros, save_parametros_import
    from modules.nomina.parametros_conciliacion import post_import_rematch_controlled
    from modules.nomina.parametros_consolidado import RECORD_EXTERNAL_NOMINA, RECORD_HEADCOUNT_CANONICAL

    db = str(tmp_path / "rematch_doubt.db")
    conn = sqlite3.connect(db)
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()
    iso = "2026-01-01 12:00:00"
    imp_id = save_parametros_import(
        db,
        {"tipo_importacion": "NOMINA_ACTUAL", "cliente": "Carrier", "source_filename": "n.xlsx", "total_rows": 1},
        created_by=None,
        now_iso=iso,
    )
    upsert_empleado_parametros(
        db,
        [
            {
                "nombre": "Pedro Martinez",
                "nombre_normalizado": "PEDRO MARTINEZ",
                "nss": None,
                "cliente": "Carrier",
                "record_kind": RECORD_EXTERNAL_NOMINA,
                "headcount_match_status": "no_match_headcount",
                "nomina_match_status": "imported",
                "warnings": [],
                "editable_json": {},
            }
        ],
        import_id=imp_id,
        now_iso=iso,
    )
    hc = [
        {"nombre_completo": "Pedro Alfonso Martinez Vaca", "nss": "11122233344", "cliente": "Carrier", "status_operacion": "ALTA"},
        {"nombre_completo": "Pedro Martinez López", "nss": "55566677788", "cliente": "Carrier", "status_operacion": "ALTA"},
    ]
    summary = post_import_rematch_controlled(db, import_id=imp_id, headcount_rows=hc, now_iso=iso)
    assert summary["probables_revision"] >= 1 or summary["externos_sin_vinculo"] >= 1

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT headcount_match_status, record_kind FROM nomina_empleado_parametros WHERE last_import_id = ?",
        (imp_id,),
    ).fetchone()
    conn.close()
    assert row[0] in {"pending_review", "no_match_headcount", "probable_match", "multiple_candidates"}
    assert row[1] != RECORD_HEADCOUNT_CANONICAL or row[0] == "manual_link"


def test_correct_manual_link_changes_headcount_target(tmp_path):
    import sqlite3

    from modules.nomina.db import ensure_nomina_tables, upsert_empleado_parametros, save_parametros_import
    from modules.nomina.parametros_conciliacion import correct_manual_headcount_link
    from modules.nomina.parametros_consolidado import (
        RECORD_EXTERNAL_NOMINA,
        RECORD_HEADCOUNT_CANONICAL,
        apply_manual_headcount_link,
    )

    db = str(tmp_path / "correct_link.db")
    conn = sqlite3.connect(db)
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()
    iso = "2026-01-01 12:00:00"
    imp_id = save_parametros_import(
        db,
        {"tipo_importacion": "NOMINA_ACTUAL", "cliente": "Carrier", "source_filename": "n.xlsx", "total_rows": 2},
        created_by=None,
        now_iso=iso,
    )
    upsert_empleado_parametros(
        db,
        [
            {
                "nombre": "Pedro Alfonso Martinez Vaca",
                "nombre_normalizado": "PEDRO ALFONSO MARTINEZ VACA",
                "nss": "11122233344",
                "cliente": "Carrier",
                "record_kind": RECORD_HEADCOUNT_CANONICAL,
                "headcount_match_status": "headcount_canonical",
                "warnings": [],
                "editable_json": {},
            },
            {
                "nombre": "Pedro Martinez López",
                "nombre_normalizado": "PEDRO MARTINEZ LOPEZ",
                "nss": "55566677788",
                "cliente": "Carrier",
                "record_kind": RECORD_HEADCOUNT_CANONICAL,
                "headcount_match_status": "headcount_canonical",
                "warnings": [],
                "editable_json": {},
            },
            {
                "nombre": "Pedro Martinez",
                "nombre_normalizado": "PEDRO MARTINEZ",
                "nss": None,
                "cliente": "Carrier",
                "salario_operativo": 2500.0,
                "valor_x_he": 70.0,
                "record_kind": RECORD_EXTERNAL_NOMINA,
                "headcount_match_status": "no_match_headcount",
                "nomina_match_status": "imported",
                "warnings": [],
                "editable_json": {},
            },
        ],
        import_id=imp_id,
        now_iso=iso,
    )
    conn = sqlite3.connect(db)
    external_id = conn.execute("SELECT id FROM nomina_empleado_parametros WHERE nombre = 'Pedro Martinez'").fetchone()[0]
    conn.close()

    apply_manual_headcount_link(
        db,
        int(external_id),
        headcount_nss="11122233344",
        headcount_nombre="Pedro Alfonso Martinez Vaca",
        headcount_cliente="Carrier",
        linked_by=1,
        now_iso=iso,
    )
    ok = correct_manual_headcount_link(
        db,
        int(external_id),
        new_headcount_nss="55566677788",
        new_headcount_nombre="Pedro Martinez López",
        new_headcount_cliente="Carrier",
        linked_by=1,
        now_iso=iso,
    )
    assert ok

    conn = sqlite3.connect(db)
    old_target = conn.execute(
        "SELECT salario_operativo, headcount_match_status FROM nomina_empleado_parametros WHERE nss = '11122233344'"
    ).fetchone()
    new_target = conn.execute(
        "SELECT salario_operativo, headcount_match_status, editable_json FROM nomina_empleado_parametros WHERE nss = '55566677788'"
    ).fetchone()
    conn.close()
    assert float(new_target[0]) == 2500.0
    assert new_target[1] == "manual_link"
    assert __import__("json").loads(new_target[2]).get("manual_headcount_nss") == "55566677788"
    assert old_target[1] in {"pending_review", "headcount_canonical"}


def test_rematch_row_preserves_manual_editable_fields(tmp_path):
    import sqlite3

    from modules.nomina.db import ensure_nomina_tables, upsert_empleado_parametros, save_parametros_import
    from modules.nomina.parametros_conciliacion import rematch_parametro_row
    from modules.nomina.parametros_consolidado import RECORD_EXTERNAL_NOMINA

    db = str(tmp_path / "recalc.db")
    conn = sqlite3.connect(db)
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()
    iso = "2026-01-01 12:00:00"
    imp_id = save_parametros_import(
        db,
        {"tipo_importacion": "NOMINA_ACTUAL", "cliente": "Carrier", "source_filename": "n.xlsx", "total_rows": 1},
        created_by=None,
        now_iso=iso,
    )
    upsert_empleado_parametros(
        db,
        [
            {
                "nombre": "Pedro Martinez",
                "nombre_normalizado": "PEDRO MARTINEZ",
                "nss": None,
                "cliente": "Carrier",
                "record_kind": RECORD_EXTERNAL_NOMINA,
                "headcount_match_status": "no_match_headcount",
                "nomina_match_status": "imported",
                "warnings": [],
                "editable_json": {
                    "manual_headcount_nss": "11122233344",
                    "nota_operativa": "Revisado por admin",
                },
            }
        ],
        import_id=imp_id,
        now_iso=iso,
    )
    conn = sqlite3.connect(db)
    row_id = conn.execute("SELECT id FROM nomina_empleado_parametros LIMIT 1").fetchone()[0]
    conn.close()
    hc = [
        {"nombre_completo": "Pedro Alfonso Martinez Vaca", "nss": "11122233344", "cliente": "Carrier", "status_operacion": "ALTA"},
    ]
    result = rematch_parametro_row(db, int(row_id), hc, user_id=1, now_iso=iso)
    assert result["ok"]
    assert result["match_status"] == "manual_link"

    conn = sqlite3.connect(db)
    ed = __import__("json").loads(conn.execute("SELECT editable_json FROM nomina_empleado_parametros WHERE id = ?", (row_id,)).fetchone()[0])
    conn.close()
    assert ed.get("manual_headcount_nss") == "11122233344"
    assert ed.get("nota_operativa") == "Revisado por admin"
    assert any(e.get("action") == "recalcular_fila" for e in ed.get("audit_events") or [])

