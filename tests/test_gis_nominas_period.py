"""GIS Nóminas — detección de periodo."""

from __future__ import annotations

from datetime import date

import pytest

from modules.gestion_idse_sua.nominas.period_parser import detect_period, parse_manual_period


def test_period_same_month():
    out = detect_period("3 al 9 jun", reference=date(2026, 6, 15))
    assert out["detected"] is True
    assert out["fecha_inicio"] == "03/06/2026"
    assert out["fecha_fin"] == "09/06/2026"
    assert out["days"] == 7


def test_period_cross_month():
    out = detect_period("31 may al 6 jun", reference=date(2026, 6, 1))
    assert out["detected"] is True
    assert out["fecha_inicio"] == "31/05/2026"
    assert out["fecha_fin"] == "06/06/2026"


def test_period_nomina_del():
    out = detect_period("NOMINA DEL 12 AL 19 JUL", reference=date(2026, 7, 20))
    assert out["detected"] is True
    assert out["fecha_fin"] == "19/07/2026"


def test_period_unparsed():
    out = detect_period("Hoja auxiliar sin fechas")
    assert out["detected"] is False


def test_manual_period_warns_non_seven_days():
    out = parse_manual_period("01/06/2026", "05/06/2026")
    assert out["days"] == 5
    assert "5 días" in (out.get("cut_warning") or "")
