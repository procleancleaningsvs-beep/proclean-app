from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

from modules.nomina.banorte.models import NormalizedPayment
from modules.nomina.banorte.pag_layout import (
    PagLayoutError,
    build_filename,
    build_pag_file,
    sha256_hex,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "banorte"
INDEPENDENT_BUILDER = FIXTURES / "build_synthetic_golden.py"
REAL_PAG = Path(__file__).resolve().parents[1] / "private_fixtures" / "banorte" / "NI6705903.pag"
REAL_HASH = "8472dcb4d52702a91ef9d1ae9a10fae8bd2e6cfe50f08a09f994ae85f124231f"


def _load_independent_builder():
    spec = importlib.util.spec_from_file_location("banorte_independent_golden", INDEPENDENT_BUILDER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _synthetic_payments() -> list[NormalizedPayment]:
    indie = _load_independent_builder()
    out = []
    for p in indie.SYNTHETIC_PAYMENTS:
        out.append(
            NormalizedPayment(
                beneficiary_id=None,
                employee_number=p["employee_number"],
                account_number=p["account_number"],
                amount=Decimal(p["amount_cents"]) / Decimal(100),
                source_reference=None,
            )
        )
    return out


def test_independent_golden_does_not_import_production_builder():
    source = INDEPENDENT_BUILDER.read_text(encoding="utf-8")
    assert "import modules.nomina.banorte.pag_layout" not in source
    assert "from modules.nomina.banorte.pag_layout" not in source
    assert "from modules.nomina.banorte import pag_layout" not in source
    # Runtime guard: loaded module must not expose production builder symbols as dependency.
    indie = _load_independent_builder()
    assert not hasattr(indie, "build_pag_file")
    assert hasattr(indie, "build_independent_pag")


def test_synthetic_golden_byte_identical():
    indie = _load_independent_builder()
    payments = _synthetic_payments()
    out = build_pag_file(
        layout_date=indie.LAYOUT_DATE,
        consecutive=indie.CONSECUTIVE,
        payments=payments,
    )
    expected = (FIXTURES / "synthetic_golden.pag").read_bytes()
    assert out == expected
    assert out == indie.build_independent_pag()


def test_no_trailing_crlf_and_crlf_between():
    data = build_pag_file(
        layout_date="20260115",
        consecutive="07",
        payments=_synthetic_payments(),
    )
    assert not data.endswith(b"\r\n")
    assert b"\r\n" in data
    lines = data.split(b"\r\n")
    assert all(len(line) == 165 for line in lines)
    assert all(all(32 <= b <= 126 for b in line) for line in lines)


def test_changing_consecutive_changes_header_and_filename():
    base = build_pag_file(layout_date="20260115", consecutive="07", payments=_synthetic_payments())
    other = build_pag_file(layout_date="20260115", consecutive="08", payments=_synthetic_payments())
    assert base != other
    assert base[16:18] == b"07"
    assert other[16:18] == b"08"
    assert build_filename("03") == "NI6705903.pag"
    assert build_filename("10") == "NI6705910.pag"


def test_field_sensitivity_fecha_emp_account_amount_order():
    payments = _synthetic_payments()
    a = build_pag_file(layout_date="20260115", consecutive="07", payments=payments)
    b = build_pag_file(layout_date="20260116", consecutive="07", payments=payments)
    assert a[8:16] == b"20260115"
    assert b[8:16] == b"20260116"

    swapped = list(reversed(payments))
    c = build_pag_file(layout_date="20260115", consecutive="07", payments=swapped)
    assert a != c

    first_detail = a.split(b"\r\n")[1]
    assert first_detail[9:19] == b"0000000011"
    assert first_detail[119:137] == b"000000001321431243"
    assert first_detail[99:114] == b"000000000270000"


def test_layout_limits_block_overflow():
    huge_emp = NormalizedPayment(None, "1" * 11, "1321431243", Decimal("10.00"), None)
    with pytest.raises(PagLayoutError):
        build_pag_file(layout_date="20260115", consecutive="07", payments=[huge_emp])
    huge_acct = NormalizedPayment(None, "11", "1" * 19, Decimal("10.00"), None)
    with pytest.raises(PagLayoutError):
        build_pag_file(layout_date="20260115", consecutive="07", payments=[huge_acct])


def test_sha256_hex_stable():
    data = build_pag_file(layout_date="20260115", consecutive="07", payments=_synthetic_payments())
    assert sha256_hex(data) == sha256_hex(data)


@pytest.mark.skipif(not REAL_PAG.exists(), reason="private real pag absent")
def test_optional_real_reference_hash_only():
    assert sha256_hex(REAL_PAG.read_bytes()) == REAL_HASH
