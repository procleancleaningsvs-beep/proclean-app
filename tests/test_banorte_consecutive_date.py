"""Banorte Fase 2.1B — consecutive validation and authoritative layout date."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from modules.nomina.banorte.export_service import ExportBlockedError, normalize_consecutive, resolve_layout_date_monterrey


def test_consecutive_presets_valid():
    for n in range(1, 11):
        assert normalize_consecutive(f"{n:02d}") == f"{n:02d}"


def test_consecutive_other_range():
    assert normalize_consecutive("11") == "11"
    assert normalize_consecutive("99") == "99"


def test_consecutive_invalid():
    with pytest.raises(ExportBlockedError):
        normalize_consecutive("00")
    with pytest.raises(ExportBlockedError):
        normalize_consecutive("100")
    with pytest.raises(ExportBlockedError):
        normalize_consecutive("ab")
    with pytest.raises(ExportBlockedError):
        normalize_consecutive("1a")


def test_layout_date_monterrey_not_client():
    fixed = datetime(2026, 7, 14, 23, 30, tzinfo=ZoneInfo("America/Monterrey"))
    with patch("modules.nomina.banorte.export_service.datetime") as mock_dt:
        mock_dt.now.return_value = fixed
        layout_date, display = resolve_layout_date_monterrey(client_layout_date="20200101")
    assert layout_date == "20260714"
    assert display == "14/07/2026"


def test_layout_date_ignores_client():
    fixed = datetime(2026, 1, 1, 19, 0, tzinfo=ZoneInfo("America/Monterrey"))
    with patch("modules.nomina.banorte.export_service.datetime") as mock_dt:
        mock_dt.now.return_value = fixed
        layout_date, _ = resolve_layout_date_monterrey(client_layout_date="20991231")
    assert layout_date == "20260101"
