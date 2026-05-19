from __future__ import annotations

import os
from pathlib import Path

import pytest

from modules.headcount.sua_parser import parse_sua_pdf_bytes

FIXTURE = os.environ.get("HEADCOUNT_SUA_PDF_FIXTURE", "")


@pytest.mark.skipif(not FIXTURE or not Path(FIXTURE).is_file(), reason="Sin PDF SUA de prueba")
def test_sua_pdf_count_matches_total():
    data = Path(FIXTURE).read_bytes()
    result = parse_sua_pdf_bytes(data)
    assert result.es_sua
    assert result.total_cotizantes is not None
    assert result.ok
    assert result.trabajadores_extraidos == result.total_cotizantes


def test_sua_invalid_document():
    result = parse_sua_pdf_bytes(b"%PDF-1.4\nnot a sua doc")
    assert not result.ok
