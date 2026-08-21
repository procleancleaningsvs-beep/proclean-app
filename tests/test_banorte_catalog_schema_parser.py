from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from modules.nomina.banorte.catalog_parser import (
    CATALOG_HEADER_V1,
    CatalogParseError,
    catalog_name_key_v1,
    catalog_name_normalized_v1,
    parse_catalog_txt,
)
from modules.nomina.banorte.schema import ensure_banorte_tables


def _catalog_bytes(*rows: list[str], bom: bool = False) -> bytes:
    text = "\n".join(
        [
            "FECHA: 20/ago./2026",
            "EMISORA: 67059",
            "",
            "|".join(CATALOG_HEADER_V1) + "|",
            *("|".join(row) + "|" for row in rows),
        ]
    )
    return (("\ufeff" if bom else "") + text).encode("utf-8")


def _row(
    *,
    employee: str = "0000000001",
    name: str = "María de Jesús Demo",
    modified: str = "20/ago./2026",
    created: str = "01/ene./2026",
    rfc: str = "DEMO900101AB1",
    account: str = "0123456789",
    status: str = "APLICADO",
    result: str = "REGISTRO ACEPTADO",
) -> list[str]:
    return [
        employee,
        name,
        created,
        modified,
        "ADMIN",
        "01/ene./1990",
        rfc,
        "1000.00",
        "900.00",
        "NUEVO LEON",
        "01/ene./2020",
        "SEMANAL",
        "NUEVO LEON",
        "CUENTA BANORTE",
        account,
        "0",
        "ALTA",
        "INDIVIDUAL",
        status,
        result,
        "ADMIN",
        "20/ago./2026",
        "",
        "",
    ]


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return {str(row["name"]): row for row in conn.execute(f"PRAGMA table_info({table})")}


def test_catalog_schema_is_additive_idempotent_and_defaults_to_legacy(tmp_path):
    conn = sqlite3.connect(tmp_path / "catalog.db")
    conn.row_factory = sqlite3.Row
    ensure_banorte_tables(conn)
    ensure_banorte_tables(conn)

    names = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'nomina_banorte_catalog_%'"
        )
    }
    assert names == {
        "nomina_banorte_catalog_versions",
        "nomina_banorte_catalog_rows",
        "nomina_banorte_catalog_persons",
        "nomina_banorte_catalog_person_rows",
        "nomina_banorte_catalog_reconciliations",
        "nomina_banorte_catalog_events",
    }

    version_cols = _columns(conn, "nomina_banorte_catalog_versions")
    assert version_cols["delimiter"]["notnull"] == 1
    assert version_cols["delimiter"]["dflt_value"] == "'|'"

    for table in ("nomina_banorte_export_drafts", "nomina_banorte_exports"):
        cols = _columns(conn, table)
        assert cols["catalog_mode"]["notnull"] == 1
        assert cols["catalog_mode"]["dflt_value"] == "'LEGACY'"
        assert "catalog_version_id" in cols

    draft_row_cols = _columns(conn, "nomina_banorte_export_draft_rows")
    for name in (
        "catalog_person_id",
        "catalog_reconciliation_id",
        "catalog_match_method",
        "beneficiary_material_fingerprint_version",
        "beneficiary_material_fingerprint_seen",
    ):
        assert name in draft_row_cols
    assert draft_row_cols["catalog_observation_codes_json"]["dflt_value"] == "'[]'"

    item_cols = _columns(conn, "nomina_banorte_export_items")
    for name in (
        "catalog_version_id",
        "catalog_person_id",
        "catalog_reconciliation_id",
        "catalog_match_method",
        "beneficiary_material_fingerprint_version",
        "beneficiary_material_fingerprint",
    ):
        assert name in item_cols
    assert item_cols["catalog_observation_codes_json"]["dflt_value"] == "'[]'"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_catalog_parser_requires_pipe_and_trailing_empty_token():
    parsed = parse_catalog_txt(_catalog_bytes(_row()), filename="catalogo.txt")
    assert parsed.delimiter == "|"
    assert parsed.encoding == "UTF-8"
    assert parsed.data_row_count == 1
    assert len(parsed.rows[0].original_fields) == 24
    assert parsed.rows[0].eligibility == "ELIGIBLE"

    without_trailing_pipe = _catalog_bytes(_row()).rstrip(b"|")
    with pytest.raises(CatalogParseError, match="data_trailing_delimiter_required"):
        parse_catalog_txt(without_trailing_pipe, filename="catalogo.txt")

    with pytest.raises(CatalogParseError, match="header_delimiter_invalid"):
        parse_catalog_txt(_catalog_bytes(_row()).replace(b"|", b";"), filename="catalogo.txt")


def test_catalog_parser_hashes_raw_file_and_content_not_position():
    row = _row()
    payload = _catalog_bytes(row, row, bom=True)
    parsed = parse_catalog_txt(payload, filename="catalogo.txt")
    assert parsed.encoding == "UTF-8-BOM"
    assert parsed.file_sha256 == hashlib.sha256(payload).hexdigest()
    assert parsed.rows[0].source_position == 1
    assert parsed.rows[1].source_position == 2
    assert parsed.rows[0].row_content_sha256 == parsed.rows[1].row_content_sha256


def test_catalog_parser_fail_closed_eligibility_and_normalization():
    parsed = parse_catalog_txt(
        _catalog_bytes(
            _row(status="CAPTURADO"),
            _row(employee="0000000002", rfc="DEMO900101AB2", result=""),
        ),
        filename="catalogo.txt",
    )
    assert [row.eligibility for row in parsed.rows] == ["BLOCKED", "BLOCKED"]
    assert parsed.rows[0].eligibility_reason == "STATUS_NOT_APLICADO"
    assert parsed.rows[1].eligibility_reason == "RESULT_NOT_REGISTRO_ACEPTADO"
    assert catalog_name_normalized_v1("  Ma. de Jesús, Demo  ") == "MA DE JESUS DEMO"
    assert catalog_name_key_v1("  Ma. de Jesús, Demo  ") == "MARIA DE JESUS DEMO"


def test_catalog_parser_rejects_wrong_extension_size_header_and_invalid_utf8():
    payload = _catalog_bytes(_row())
    with pytest.raises(CatalogParseError, match="extension_invalid"):
        parse_catalog_txt(payload, filename="catalogo.csv")
    with pytest.raises(CatalogParseError, match="header_invalid"):
        parse_catalog_txt(payload.replace(b"No. Empleado", b"Empleado X"), filename="catalogo.txt")
    with pytest.raises(CatalogParseError, match="encoding_invalid"):
        parse_catalog_txt(payload + b"\xff", filename="catalogo.txt")
    with pytest.raises(CatalogParseError, match="file_too_large"):
        parse_catalog_txt(b"x" * (10 * 1024 * 1024 + 1), filename="catalogo.txt")


def test_catalog_schema_json_defaults_are_valid(tmp_path):
    conn = sqlite3.connect(tmp_path / "defaults.db")
    conn.row_factory = sqlite3.Row
    ensure_banorte_tables(conn)
    version = conn.execute(
        """
        INSERT INTO nomina_banorte_catalog_versions (
            source_filename, file_sha256, file_size_bytes, encoding,
            report_date, issuer_original, issuer_normalized,
            source_line_count, data_row_count, created_by, created_at
        ) VALUES (?, ?, ?, 'UTF-8', ?, ?, ?, ?, ?, ?, ?)
        RETURNING *
        """,
        (
            "synthetic.txt",
            "a" * 64,
            100,
            "2026-08-20",
            "67059",
            "67059",
            5,
            1,
            "tester",
            "2026-08-21T00:00:00+00:00",
        ),
    ).fetchone()
    assert version["status"] == "STAGED"
    assert version["delimiter"] == "|"
    assert json.loads(version["analysis_summary_json"]) == {}
    conn.close()
