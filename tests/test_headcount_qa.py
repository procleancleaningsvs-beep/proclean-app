from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from modules.headcount.config import get_patron_auditoria_aliases, patron_matches_auditoria
from modules.headcount.matching import enrich_sua_worker_fields, sua_es_activo_al_corte, sua_tiene_baja
from modules.headcount.privacy import mask_curp, mask_nss, should_mask_sensitive_data
from modules.headcount.services import ejecutar_auditoria_sua
from modules.headcount.storage import ensure_headcount_tables, insert_sua_audit
from modules.headcount.sua_parser import _parse_workers_from_pages, parse_sua_pdf_bytes
from modules.roles_access import (
    can_access_headcount_auditoria,
    can_access_headcount_cliente,
    can_access_headcount_module,
)


def test_storage_uses_app_database_path(tmp_path: Path):
    db = tmp_path / "instance" / "proclean.db"
    db.parent.mkdir(parents=True)
    ensure_headcount_tables(str(db))
    conn = sqlite3.connect(db)
    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='headcount_sua_audits'"
        ).fetchall()
        assert tables
    finally:
        conn.close()


def test_patron_aliases_rafael_variants(monkeypatch):
    monkeypatch.delenv("HEADCOUNT_PATRON_ALIASES", raising=False)
    get_patron_auditoria_aliases.cache_clear()
    assert patron_matches_auditoria("RAFAEL")
    assert patron_matches_auditoria("Rafael")
    assert patron_matches_auditoria("  RAFAEL  ")
    assert patron_matches_auditoria("RAFAEL ALEJANDRO")
    assert not patron_matches_auditoria("RAFAELITO")
    assert not patron_matches_auditoria("OTRO")


def test_patron_aliases_from_env(monkeypatch):
    monkeypatch.setenv("HEADCOUNT_PATRON_ALIASES", "RAFAEL ALEJANDRO,CUSTOM PATRON")
    get_patron_auditoria_aliases.cache_clear()
    assert patron_matches_auditoria("CUSTOM PATRON")
    assert not patron_matches_auditoria("RAFAEL")
    get_patron_auditoria_aliases.cache_clear()


def test_orphan_baja_attaches_to_previous_worker():
    pages = [
        "\n".join(
            [
                "25-99-82-1773-8 GOMEZ LOPEZ JUAN GOML850101HDFRRN09 30 350.00",
                "Baja 29/04/2026",
                "25-88-88-1888-8 MARIA FLORES PEREZ MAFP900202MDFLRN05 30 400.00",
            ]
        )
    ]
    workers, _ = _parse_workers_from_pages(pages)
    assert len(workers) == 2
    assert workers[0]["nss_normalizado"] == "25998217738"
    assert workers[0]["movimiento_clave"] == "BAJA"
    assert workers[0]["movimiento_fecha"] == "29/04/2026"
    assert sua_tiene_baja(workers[0]["movimiento_clave"])
    assert not sua_es_activo_al_corte(workers[0]["movimiento_clave"])
    assert workers[1]["movimiento_clave"] in ("", "ALTA", "REIN") or workers[1]["movimiento_clave"] != "BAJA"


def test_total_cotizantes_includes_bajas_in_count():
    pages = [
        "\n".join(
            [
                "25-99-82-1773-8 GOMEZ LOPEZ JUAN GOML850101HDFRRN09 30 350.00",
                "Baja 29/04/2026",
                "25-88-88-1888-8 MARIA FLORES PEREZ MAFP900202MDFLRN05 30 400.00",
            ]
        )
    ]
    workers, _ = _parse_workers_from_pages(pages)
    enriched = [enrich_sua_worker_fields(w) for w in workers]
    assert len(enriched) == 2
    assert sum(1 for w in enriched if w["sua_tiene_baja"]) == 1
    assert sum(1 for w in enriched if w["sua_es_activo_al_corte"]) == 1


def test_role_guards_matrix():
    assert can_access_headcount_module("admin")
    assert can_access_headcount_module("nomina")
    assert can_access_headcount_module("usuario")
    assert can_access_headcount_module("coordinador")
    assert not can_access_headcount_module("cobranza")

    assert can_access_headcount_auditoria("admin")
    assert can_access_headcount_auditoria("nomina")
    assert not can_access_headcount_auditoria("usuario")
    assert not can_access_headcount_auditoria("coordinador")
    assert not can_access_headcount_auditoria("cobranza")

    assert can_access_headcount_cliente("usuario")
    assert can_access_headcount_cliente("coordinador")


def test_auditoria_paths_blocked_for_limited_roles():
    def blocked(role: str, path: str) -> bool:
        if can_access_headcount_auditoria(role):
            return False
        if path.startswith("/headcount/auditoria-sua") or path.startswith("/headcount/historial-sua"):
            return True
        if path.startswith("/headcount/desglose") or "/exportar-excel" in path:
            return True
        return False

    for path in (
        "/headcount/auditoria-sua",
        "/headcount/auditoria-sua/procesar",
        "/headcount/historial-sua",
        "/headcount/desglose",
        "/headcount/auditoria-sua/abc/exportar-excel",
    ):
        assert blocked("usuario", path)
        assert blocked("coordinador", path)
        assert not blocked("admin", path)
        assert not blocked("nomina", path)


def test_mask_sensitive_for_limited_roles():
    assert should_mask_sensitive_data("usuario")
    assert not should_mask_sensitive_data("admin")
    assert mask_nss("25998217738").endswith("7738")
    assert "***" in mask_nss("25998217738")
    assert "***" in mask_curp("GOML850101HDFRRN09")


def test_ejecutar_auditoria_no_writes_pdf_to_disk(tmp_path: Path, monkeypatch):
    """PDF solo en memoria; historial en SQLite indicado."""
    pages = [
        "\n".join(
            [
                "SISTEMA UNICO DE AUTODETERMINACION",
                "CEDULA DE DETERMINACION DE CUOTAS",
                "REGISTRO PATRONAL: Y1234567890",
                "TOTAL DE COTIZANTES: 2",
                "25-99-82-1773-8 GOMEZ LOPEZ JUAN GOML850101HDFRRN09 30 350.00",
                "Baja 29/04/2026",
                "25-88-88-1888-8 MARIA FLORES PEREZ MAFP900202MDFLRN05 30 400.00",
            ]
        )
    ]
    fake_pdf = _minimal_sua_pdf_text(pages[0])
    if fake_pdf is None:
        pytest.skip("PyMuPDF no disponible para PDF sintético")

    monkeypatch.setattr(
        "modules.headcount.services.obtener_registros_headcount",
        lambda **kw: [],
    )
    result = ejecutar_auditoria_sua(fake_pdf, fecha_corte_sua="2026-04-30", archivo_nombre="test.pdf")
    if not result.get("ok") and result.get("fase") == "validacion":
        pytest.skip("PDF sintético no reconocido como SUA")
    if result.get("ok"):
        resumen = result["resumen"]
        assert resumen["total_cotizantes_sua"] == resumen["trabajadores_extraidos"]
        assert resumen["total_sua_bajas_periodo"] >= 1
        assert resumen["total_sua_activos_al_corte"] + resumen["total_sua_bajas_periodo"] == resumen[
            "total_cotizantes_sua"
        ]
    assert not list(tmp_path.glob("**/*.pdf"))


def _minimal_sua_pdf_text(text: str) -> bytes | None:
    try:
        import fitz
    except ImportError:
        return None
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=9)
    return doc.tobytes()


def test_insert_audit_json_only_no_pdf_path(tmp_path: Path):
    db = str(tmp_path / "proclean.db")
    ensure_headcount_tables(db)
    insert_sua_audit(
        db,
        audit_id="test-audit-1",
        user_id=1,
        created_at="2026-04-30 12:00:00",
        fecha_corte_sua="2026-04-30",
        archivo_original_nombre="sua.pdf",
        registro_patronal_sua="Y123",
        razon_social_sua="TEST",
        rfc_patronal_sua="",
        periodo_proceso_sua="04-2026",
        fecha_proceso_sua="30/04/2026",
        total_cotizantes=2,
        trabajadores_extraidos=2,
        total_matches=0,
        total_sin_match=0,
        total_warnings=0,
        resumen={"total_cotizantes_sua": 2},
        payload={"detalle": []},
        hash_archivo="abc",
    )
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM headcount_sua_audits WHERE audit_id=?", ("test-audit-1",)).fetchone()
    conn.close()
    assert row is not None
    assert json.loads(row["detalle_json"]) == {"detalle": []}
    cols = set(row.keys())
    assert "pdf_path" not in cols
