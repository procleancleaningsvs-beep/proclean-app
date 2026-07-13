from __future__ import annotations

from decimal import Decimal

import pytest

from modules.nomina.banorte.export_service import (
    DraftPaymentRow,
    ExportBlockedError,
    generate_export,
    get_export_blob,
)
from modules.nomina.banorte.pag_layout import sha256_hex
from modules.nomina.banorte.repository import connect
from modules.nomina.db import ensure_nomina_tables


def _ben(conn, emp="11", account="1321431243", validation="IMPORTADO_EXITOSO"):
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, employee_number_effective, account_number,
            source_kind, validation_status, record_status, imported_at, imported_by, created_at, updated_at
        ) VALUES ('ANA','ANA',?,?,'ALTA_MANUAL',?,'ACTIVO','t','u','t','t')
        """,
        (emp, account, validation),
    )
    return int(cur.lastrowid)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "ex.db"
    conn = connect(path)
    ensure_nomina_tables(conn)
    ben = _ben(conn)
    conn.commit()
    conn.close()
    return str(path), ben


def test_generate_and_redownload_exact_bytes(db):
    path, ben = db
    draft = [
        DraftPaymentRow(1, "Ana", ben, "2700.00", "EXACT", client_account_number="1321431243", client_employee_number="11")
    ]
    result = generate_export(path, "tester", draft, consecutive="07", layout_date="20260115", confirm_date_override=True)
    assert result.filename == "NI6705907.pag"
    assert result.file_sha256 == sha256_hex(result.file_bytes)
    name, blob, digest = get_export_blob(path, result.export_id)
    assert name == result.filename
    assert blob == result.file_bytes
    assert digest == result.file_sha256


def test_forged_client_account_blocked(db):
    path, ben = db
    draft = [
        DraftPaymentRow(
            1,
            "Ana",
            ben,
            "100.00",
            "EXACT",
            client_account_number="9999999999",
            client_employee_number="11",
        )
    ]
    with pytest.raises(ExportBlockedError) as ei:
        generate_export(path, "tester", draft, consecutive="01", layout_date="20260115", confirm_date_override=True)
    assert ei.value.code == "rows_require_review"


def test_inactive_since_preview_blocked(db):
    path, ben = db
    conn = connect(path)
    conn.execute(
        "UPDATE nomina_banorte_beneficiaries SET record_status='INACTIVO_REEMPLAZADO' WHERE id=?",
        (ben,),
    )
    conn.commit()
    conn.close()
    draft = [DraftPaymentRow(1, "Ana", ben, "100.00", "EXACT")]
    with pytest.raises(ExportBlockedError) as ei:
        generate_export(path, "tester", draft, consecutive="01", layout_date="20260115", confirm_date_override=True)
    assert ei.value.code == "rows_require_review"


def test_duplicate_consecutive_requires_confirm_and_keeps_prior(db):
    path, ben = db
    draft = [DraftPaymentRow(1, "Ana", ben, "100.00", "EXACT")]
    first = generate_export(path, "tester", draft, consecutive="03", layout_date="20260115", confirm_date_override=True)
    with pytest.raises(ExportBlockedError) as ei:
        generate_export(path, "tester", draft, consecutive="03", layout_date="20260115", confirm_date_override=True)
    assert ei.value.code == "duplicate_consecutive_confirmation_required"
    second = generate_export(
        path,
        "tester",
        draft,
        consecutive="03",
        layout_date="20260115",
        confirm_date_override=True,
        confirm_duplicate_consecutive=True,
    )
    conn = connect(path)
    prior = conn.execute("SELECT status, duplicate_of_export_id FROM nomina_banorte_exports WHERE id=?", (first.export_id,)).fetchone()
    assert prior["status"] == "GENERATED"
    assert prior["duplicate_of_export_id"] is None
    neo = conn.execute(
        "SELECT duplicate_consecutive_confirmed, duplicate_of_export_id FROM nomina_banorte_exports WHERE id=?",
        (second.export_id,),
    ).fetchone()
    assert neo["duplicate_consecutive_confirmed"] == 1
    assert neo["duplicate_of_export_id"] == first.export_id
    # prior blob unchanged
    assert get_export_blob(path, first.export_id)[1] == first.file_bytes
    conn.close()


def test_manual_requires_confirm(db):
    path, _ = db
    conn = connect(path)
    ben = _ben(conn, emp="22", account="1321000022", validation="MANUAL_PENDIENTE_VALIDACION")
    conn.commit()
    conn.close()
    draft = [DraftPaymentRow(1, "Manual", ben, "50.00", "MANUAL_CREATE")]
    with pytest.raises(ExportBlockedError) as ei:
        generate_export(path, "tester", draft, consecutive="05", layout_date="20260115", confirm_date_override=True)
    assert ei.value.code == "manual_beneficiaries_confirmation_required"
    ok = generate_export(
        path,
        "tester",
        draft,
        consecutive="05",
        layout_date="20260115",
        confirm_date_override=True,
        confirm_manuals=True,
    )
    assert ok.payment_count == 1
