from __future__ import annotations

from pathlib import Path

import pytest

from modules.nomina.banorte.catalog_row_adapter import prepare_capture_rows
from modules.nomina.banorte.draft_repository import create_manual_draft_shell, get_draft, save_draft_rows
from modules.nomina.banorte.rows_capture import (
    CaptureRow,
    parse_capture_input,
    parse_rows_from_lists,
    parse_tsv_capture,
)
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.db import ensure_nomina_tables


def _db(tmp_path: Path) -> str:
    import sqlite3

    path = str(tmp_path / "grid.db")
    conn = sqlite3.connect(path)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    conn.commit()
    conn.close()
    return path


def test_parse_lists_preserves_empty_lines_and_mismatch():
    rows = parse_rows_from_lists("A\nB", "1\n2\n3")
    assert len(rows) == 3
    assert all("LENGTH_MISMATCH" in row.observation_codes for row in rows)


def test_parse_tsv_unambiguous():
    rows = parse_tsv_capture("Persona A\t100.00\nPersona B\t200.50")
    assert rows is not None
    assert len(rows) == 2
    assert rows[0].name_raw == "Persona A"
    assert rows[0].amount_raw == "100.00"


def test_parse_tsv_rejects_ambiguous_tab():
    text = "solo una columna\nPersona\t100"
    assert parse_tsv_capture(text) is None


def test_parse_payload_500_rows():
    payload = [
        {"position": i, "name_raw": f"N{i}", "amount_raw": f"{i}.00"}
        for i in range(1, 501)
    ]
    rows = parse_capture_input(rows_payload=payload)
    assert len(rows) == 500
    assert rows[-1].position == 500


def test_manual_draft_from_rows_legacy_mode(tmp_path):
    db = _db(tmp_path)
    shell = create_manual_draft_shell(db, "nomina", names_text="", amounts_text="")
    rows = parse_capture_input(rows_payload=[{"name_raw": "JUAN PEREZ", "amount_raw": "100.00"}])
    prepared = prepare_capture_rows(db, rows, origin_kind="MANUAL_CAPTURE")
    draft = save_draft_rows(db, int(shell["draft"]["id"]), "nomina", 1, prepared)
    assert draft["catalog_mode"] == "LEGACY"
    assert draft["rows"]
    assert draft["rows"][0]["nombre_recibido"] == "JUAN PEREZ"


def test_single_row_invalid_amount_stays_needs_review(tmp_path):
    db = _db(tmp_path)
    rows = parse_capture_input(rows_payload=[{"name_raw": "JUAN", "amount_raw": "abc"}])
    prepared = prepare_capture_rows(db, rows, origin_kind="MANUAL_CAPTURE")
    assert prepared[0]["row_state"] == "NEEDS_REVIEW"


def test_manual_grid_wires_catalog_sidebar_autocomplete():
    root = Path(__file__).resolve().parents[1]
    grid_js = (root / "static/nomina/banorte_payment_grid.js").read_text(encoding="utf-8")
    css = (root / "static/nomina/exportaciones_banorte.css").read_text(encoding="utf-8")
    assert "catalogo/sidebar/search" in grid_js
    assert "banorte-grid-name-suggest" in grid_js
    assert "filterEnabledSuggestions" in grid_js
    assert "resolveNameAutocompleteKeydown" in grid_js
    assert ".banorte-grid-name-suggest" in css
