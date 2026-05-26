"""Performance helpers: cache TTL, pagination, stats without full materialization."""
from __future__ import annotations

import sqlite3
import time
from io import BytesIO

import pandas as pd
import pytest

from modules.comparativo import headcount_service
from modules.nomina.db import ensure_nomina_tables, upsert_empleado_parametros, save_parametros_import
from modules.nomina.parametros_consolidado import (
    RECORD_HEADCOUNT_CANONICAL,
    build_consolidado_view,
    compute_parametros_stats,
    count_consolidado_view,
)
from services.perf_logging import perf_log_enabled, perf_span


def _sample_hc() -> list[dict]:
    return [
        {
            "nombre_completo": "Empleado Uno",
            "nss": "11122233344",
            "cliente": "Carrier",
            "patron": "Planta A",
            "puesto": "Aux",
            "status_operacion": "ALTA",
            "status_imss": "ALTA",
            "fecha_ingreso": "2020-01-01",
        },
        {
            "nombre_completo": "Empleado Dos",
            "nss": "55566677788",
            "cliente": "Pepsi",
            "patron": "Planta B",
            "puesto": "Op",
            "status_operacion": "ALTA",
            "status_imss": "ALTA",
            "fecha_ingreso": "2021-02-02",
        },
    ]


def test_perf_log_disabled_by_default(monkeypatch):
    monkeypatch.delenv("PERF_LOG_ENABLED", raising=False)
    assert perf_log_enabled() is False


def test_perf_span_noop_when_disabled(monkeypatch, caplog):
    monkeypatch.delenv("PERF_LOG_ENABLED", raising=False)
    with perf_span("test.block"):
        pass
    assert not any("[PERF]" in r.message for r in caplog.records)


def test_headcount_cache_hit_avoids_download(monkeypatch):
    df = pd.DataFrame([["CLIENTE", "NSS"], ["Carrier", "111"]])
    calls = {"n": 0}

    def fake_download(_url: str) -> bytes:
        calls["n"] += 1
        bio = BytesIO()
        df.to_excel(bio, index=False, header=False)
        return bio.getvalue()

    monkeypatch.setenv("HEADCOUNT_ONEDRIVE_URL", "https://example.test/headcount.xlsx")
    monkeypatch.setenv("HEADCOUNT_CACHE_TTL_SECONDS", "300")
    headcount_service.actualizar_headcount()
    monkeypatch.setattr(headcount_service, "descargar_excel_desde_onedrive", fake_download)

    out1 = headcount_service.obtener_df_headcount()
    out2 = headcount_service.obtener_df_headcount()
    assert calls["n"] == 1
    assert len(out1.index) == len(out2.index)


def test_headcount_cache_expired_refreshes(monkeypatch):
    df = pd.DataFrame([["A"], ["B"]])
    calls = {"n": 0}

    def fake_download(_url: str) -> bytes:
        calls["n"] += 1
        bio = BytesIO()
        df.to_excel(bio, index=False, header=False)
        return bio.getvalue()

    monkeypatch.setenv("HEADCOUNT_ONEDRIVE_URL", "https://example.test/headcount.xlsx")
    monkeypatch.setenv("HEADCOUNT_CACHE_TTL_SECONDS", "1")
    headcount_service.actualizar_headcount()
    monkeypatch.setattr(headcount_service, "descargar_excel_desde_onedrive", fake_download)

    headcount_service.obtener_df_headcount()
    with headcount_service._cache_lock:
        headcount_service._cache_loaded_at = time.monotonic() - 999
    headcount_service.obtener_df_headcount()
    assert calls["n"] == 2


def test_headcount_stale_fallback_on_download_error(monkeypatch):
    df = pd.DataFrame([["cached"]])
    monkeypatch.setenv("HEADCOUNT_ONEDRIVE_URL", "https://example.test/headcount.xlsx")
    monkeypatch.setenv("HEADCOUNT_CACHE_TTL_SECONDS", "1")
    headcount_service.actualizar_headcount()

    def seed_then_fail(_url: str) -> bytes:
        bio = BytesIO()
        df.to_excel(bio, index=False, header=False)
        return bio.getvalue()

    monkeypatch.setattr(headcount_service, "descargar_excel_desde_onedrive", seed_then_fail)
    headcount_service.obtener_df_headcount()

    def boom(_url: str) -> bytes:
        raise ConnectionError("onedrive down")

    monkeypatch.setattr(headcount_service, "descargar_excel_desde_onedrive", boom)
    with headcount_service._cache_lock:
        headcount_service._cache_loaded_at = time.monotonic() - 10
    stale = headcount_service.obtener_df_headcount()
    assert stale.iloc[0, 0] == "cached"
    assert headcount_service.consume_headcount_cache_warning()


def test_pagination_preserves_total_count(tmp_path):
    db = str(tmp_path / "pag.db")
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
                "nombre": "Empleado Uno",
                "nombre_normalizado": "EMPLEADO UNO",
                "nss": "11122233344",
                "cliente": "Carrier",
                "record_kind": RECORD_HEADCOUNT_CANONICAL,
                "headcount_match_status": "headcount_canonical",
                "salario_operativo": 100.0,
                "valor_x_he": 10.0,
                "warnings": [],
                "editable_json": {},
            }
        ],
        import_id=imp_id,
        now_iso=iso,
    )
    hc = _sample_hc()
    total = count_consolidado_view(db, hc)
    page1 = build_consolidado_view(db, hc, offset=0, limit=1)
    page2 = build_consolidado_view(db, hc, offset=1, limit=1)
    assert total == 2
    assert len(page1) == 1
    assert len(page2) == 1
    assert page1[0]["nss"] != page2[0]["nss"]


def test_compute_parametros_stats_legacy_mode_does_not_inflate_activos(tmp_path):
    db = str(tmp_path / "legacy_stats.db")
    conn = sqlite3.connect(db)
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()
    stats = compute_parametros_stats(db, _sample_hc())
    assert stats["activos_headcount"] == 2
    assert stats["stats_mode"] == "headcount"
