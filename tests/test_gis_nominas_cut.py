"""GIS Nóminas — advertencias de corte semanal."""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from modules.gestion_idse_sua.nominas.planta_cliente_service import (
    expected_prior_week_bounds,
    period_cut_warnings,
)
from modules.gestion_idse_sua.nominas.schema import ensure_gis_nominas_tables


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "cut.db")
    connection.row_factory = sqlite3.Row
    ensure_gis_nominas_tables(connection)
    connection.execute(
        "INSERT INTO gis_cliente_corte (cliente, weekday_start) VALUES (?, ?)",
        ("PEPSI", 2),
    )
    connection.commit()
    yield connection
    connection.close()


def test_expected_prior_week_wednesday_cut():
    prior_start, prior_end = expected_prior_week_bounds(
        weekday_start=2,
        reference=date(2026, 6, 24),
    )
    assert prior_start.isoformat() == "2026-06-17"
    assert prior_end.isoformat() == "2026-06-23"


def test_no_warning_when_period_matches_expected_prior_week(conn):
    warnings = period_cut_warnings(
        conn,
        "PEPSI",
        "17/06/2026",
        "23/06/2026",
        reference=date(2026, 6, 24),
    )
    assert warnings == []


def test_warn_when_period_is_historical_not_expected_prior_week(conn):
    warnings = period_cut_warnings(
        conn,
        "PEPSI",
        "01/06/2026",
        "07/06/2026",
        reference=date(2026, 6, 24),
    )
    assert any("semana anterior esperada" in w for w in warnings)


def test_no_cut_configured_means_no_warning(conn):
    warnings = period_cut_warnings(conn, "SIN CORTE", "01/06/2026", "07/06/2026")
    assert warnings == []


def test_no_age_only_warning_for_old_period_without_cut(conn):
    warnings = period_cut_warnings(conn, None, "01/01/2024", "07/01/2024")
    assert warnings == []
