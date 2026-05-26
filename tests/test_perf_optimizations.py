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


def test_nomina_index_get_does_not_load_headcount_completo(tmp_path, monkeypatch):
    import sqlite3

    from modules.nomina.db import ensure_nomina_tables

    db = str(tmp_path / "hub.db")
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()

    calls = {"completo": 0, "activos": 0}

    def boom_completo():
        calls["completo"] += 1
        raise AssertionError("obtener_headcount_completo must not run on GET /nomina/")

    def boom_activos(*_a, **_k):
        calls["activos"] += 1
        raise AssertionError("obtener_activos must not run on GET /nomina/")

    monkeypatch.setenv("PERF_LOG_ENABLED", "1")
    monkeypatch.setattr("modules.nomina.headcount_bridge.obtener_headcount_completo", boom_completo)
    monkeypatch.setattr("modules.comparativo.headcount_service.obtener_activos", boom_activos)

    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["DATABASE"] = db

    with app.app_context():
        with app.test_client() as client:
            # Create admin user inline
            from werkzeug.security import generate_password_hash

            conn = sqlite3.connect(db)
            conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, 'admin', ?)",
                ("perfadmin", generate_password_hash("secret"), "2026-01-01 00:00:00"),
            )
            conn.commit()
            conn.close()
            client.post("/login", data={"username": "perfadmin", "password": "secret"})
            resp = client.get("/nomina/", follow_redirects=True)
            assert resp.status_code == 200
    assert calls["completo"] == 0
    assert calls["activos"] == 0


def test_get_nomina_dashboard_summary_fast_uses_local_stats_only(tmp_path):
    import sqlite3

    from modules.nomina.db import ensure_nomina_tables, get_nomina_dashboard_summary_fast

    db = str(tmp_path / "dash_fast.db")
    conn = sqlite3.connect(db)
    ensure_nomina_tables(conn)
    conn.commit()
    conn.close()
    summary = get_nomina_dashboard_summary_fast(db, recent_limit=5)
    assert summary["headcount_source"] == "hub_fast"
    assert summary["param_stats"]["stats_mode"] == "legacy"
    assert "dash" in summary and "vac_stats" in summary


def test_headcount_bridge_avoids_row_iloc_loop():
    import inspect

    from modules.nomina import headcount_bridge

    src = inspect.getsource(headcount_bridge.obtener_headcount_completo)
    assert "for i in range" not in src
    assert "df.iloc[i]" not in src
    assert "itertuples" in src


def test_perf_startup_log_when_enabled(monkeypatch, caplog):
    monkeypatch.setenv("PERF_LOG_ENABLED", "1")
    from app import create_app

    with caplog.at_level("INFO"):
        create_app()
    assert any("[PERF] performance logging enabled" in r.message for r in caplog.records)
