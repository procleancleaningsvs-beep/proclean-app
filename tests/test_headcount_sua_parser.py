from __future__ import annotations

import os
from pathlib import Path

import pytest

from modules.headcount.matching import enrich_sua_worker_fields, sua_es_activo_al_corte, sua_tiene_baja
from modules.headcount.sua_parser import (
    _extract_total_cotizantes,
    _parse_block,
    _parse_workers_from_pages,
    parse_sua_pdf_bytes,
)

FIXTURE = os.environ.get("HEADCOUNT_SUA_PDF_FIXTURE", "")
EXPECT_TOTAL = int(os.environ.get("HEADCOUNT_SUA_EXPECT_TOTAL", "0") or "0")
EXPECT_PAGES = int(os.environ.get("HEADCOUNT_SUA_EXPECT_PAGES", "0") or "0")


@pytest.mark.skipif(not FIXTURE or not Path(FIXTURE).is_file(), reason="Sin PDF SUA de prueba")
def test_sua_pdf_fixture_matches_total():
    data = Path(FIXTURE).read_bytes()
    result = parse_sua_pdf_bytes(data)
    assert result.es_sua
    assert result.total_cotizantes is not None
    assert result.ok
    assert result.trabajadores_extraidos == result.total_cotizantes
    if EXPECT_TOTAL:
        assert result.total_cotizantes == EXPECT_TOTAL
        assert result.trabajadores_extraidos == EXPECT_TOTAL
    if EXPECT_PAGES:
        assert result.paginas_procesadas == EXPECT_PAGES


@pytest.mark.skipif(
    not FIXTURE or not Path(FIXTURE).is_file() or EXPECT_TOTAL != 185,
    reason="Requiere HEADCOUNT_SUA_PDF_FIXTURE y HEADCOUNT_SUA_EXPECT_TOTAL=185 (Mayo 2026)",
)
def test_sua_pdf_mayo_2026_layout():
    data = Path(FIXTURE).read_bytes()
    result = parse_sua_pdf_bytes(data)
    assert result.ok
    assert result.paginas_procesadas == 15
    assert result.total_cotizantes == 185
    assert result.trabajadores_extraidos == 185
    assert result.total_cotizantes != result.paginas_procesadas
    assert any(n > 0 for n in result.registros_por_pagina)
    assert sum(result.registros_por_pagina) == 185

    movs = {w.get("sua_movimiento_clave") for w in result.trabajadores}
    assert "BAJA" in movs
    assert "ALTA" in movs
    assert "REIN" in movs

    bajas = [w for w in result.trabajadores if w.get("sua_tiene_baja")]
    assert bajas
    assert all(not w.get("sua_es_activo_al_corte") for w in bajas)
    assert result.total_sua_activos_al_corte == result.trabajadores_extraidos - len(bajas)
    assert sum(1 for w in result.trabajadores if w.get("curp")) >= 180


def test_extract_total_cotizantes_prefers_before_label_not_page_number():
    lines = [
        "otros datos",
        "185",
        "Total de Cotizantes:",
        "15",
        "Página:",
    ]
    total = _extract_total_cotizantes(lines, total_pages=15, nss_unique_count=185)
    assert total == 185


def test_extract_total_cotizantes_fallback_to_nss_count():
    lines = ["Total de Cotizantes:", "15"]
    total = _extract_total_cotizantes(lines, total_pages=15, nss_unique_count=185)
    assert total == 185


def test_parse_block_multiline_worker():
    block = [
        "45-88-56-2126-3",
        "ACU/A CASTILLO PASCUALA",
        "AUCP560517MSPCSS07",
        "1,422.01",
        "31",
    ]
    worker = _parse_block(block, pagina=1)
    assert worker is not None
    assert worker["nss_normalizado"] == "45885621263"
    assert worker["nombre_sua_original"] == "ACU/A CASTILLO PASCUALA"
    assert worker["curp"] == "AUCP560517MSPCSS07"


def test_parse_block_pensionado_pcv():
    block = [
        "12-34-56-7890-1",
        "P/CV",
        "GARCIA LOPEZ MARIA",
        "GALM850101MDFRRN09",
        "30",
    ]
    worker = _parse_block(block, pagina=1)
    assert worker is not None
    assert worker["movimiento_clave"] == "P/CV"
    assert "GARCIA" in worker["nombre_sua_original"]
    assert worker["curp"] == "GALM850101MDFRRN09"


def test_parse_block_movimiento_en_lineas_separadas():
    block = [
        "43-07-78-0777-4",
        "CAMACHO GAMEZ CESILIA",
        "CAGC781122MNLMMS07",
        "6",
        "Baja",
        "06/05/2026",
    ]
    worker = _parse_block(block, pagina=2)
    assert worker is not None
    assert worker["movimiento_clave"] == "BAJA"
    assert worker["movimiento_fecha"] == "06/05/2026"


def test_parse_workers_multiline_pages_orphan_baja():
    pages = [
        "\n".join(
            [
                "45-88-56-2126-3",
                "ACU/A CASTILLO PASCUALA",
                "AUCP560517MSPCSS07",
                "31",
                "43-07-78-0777-4",
                "CAMACHO GAMEZ CESILIA",
                "CAGC781122MNLMMS07",
                "6",
            ]
        ),
        "\n".join(["Baja", "06/05/2026"]),
    ]
    workers, por_pagina = _parse_workers_from_pages(pages)
    assert len(workers) == 2
    assert por_pagina == [2, 0]
    enriched = [enrich_sua_worker_fields(w) for w in workers]
    assert enriched[-1]["sua_movimiento_clave"] == "BAJA"
    assert enriched[-1]["sua_tiene_baja"]
    assert not enriched[-1]["sua_es_activo_al_corte"]


def test_single_line_worker_still_parsed():
    pages = [
        "\n".join(
            [
                "SISTEMA UNICO DE AUTODETERMINACION",
                "CEDULA DE DETERMINACION DE CUOTAS",
                "25-99-82-1773-8 GOMEZ LOPEZ JUAN GOML850101HDFRRN09 30 350.00",
                "25-88-88-1888-8 MARIA FLORES PEREZ MAFP900202MDFLRN05 30 400.00",
            ]
        )
    ]
    workers, _ = _parse_workers_from_pages(pages)
    assert len(workers) == 2
    assert workers[0]["curp"] == "GOML850101HDFRRN09"


def test_sua_invalid_document():
    result = parse_sua_pdf_bytes(b"%PDF-1.4\nnot a sua doc")
    assert not result.ok
