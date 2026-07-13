from __future__ import annotations

from decimal import Decimal

import pytest

from modules.nomina.banorte.money import (
    format_pesos_from_cents,
    parse_money,
    sum_amounts,
    to_cents,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2300", Decimal("2300.00")),
        ("2300.5", Decimal("2300.50")),
        ("2300.50", Decimal("2300.50")),
        ("2,300", Decimal("2300.00")),
        ("2,300.50", Decimal("2300.50")),
        ("$2,300.00", Decimal("2300.00")),
        ("2300,50", Decimal("2300.50")),
        ("€2.300,50", Decimal("2300.50")),
        ("2 300,50 €", Decimal("2300.50")),
    ],
)
def test_accepted_money_formats(raw, expected):
    result = parse_money(raw)
    assert result.ok is True
    assert result.amount == expected
    assert result.ambiguous is False


def test_euro_symbol_is_not_fx_conversion():
    result = parse_money("€100.00")
    assert result.ok and result.amount == Decimal("100.00")


@pytest.mark.parametrize(
    "raw,expected,rounded",
    [
        ("2300.41123210", Decimal("2300.41"), True),
        ("2300.66231130", Decimal("2300.66"), True),
        ("2300.66631130", Decimal("2300.67"), True),
        ("2300.50", Decimal("2300.50"), False),
    ],
)
def test_round_half_up_accepts_excess_decimals(raw, expected, rounded):
    result = parse_money(raw)
    assert result.ok is True
    assert result.amount == expected
    assert result.rounded is rounded


@pytest.mark.parametrize("raw", ["0", "0.00", "0,00", "$0"])
def test_zero_blocked(raw):
    result = parse_money(raw)
    assert result.ok is False
    assert result.error == "zero"


@pytest.mark.parametrize("raw", ["-1", "-10.5", "(100)", "-€5"])
def test_negatives_blocked(raw):
    result = parse_money(raw)
    assert result.ok is False
    assert result.error == "negative"


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_empty_blocked(raw):
    result = parse_money(raw)
    assert result.ok is False
    assert result.error == "empty"


@pytest.mark.parametrize("raw", ["1.234", "abc", "=A1+B1", "NaN", "Infinity"])
def test_ambiguous_or_invalid_blocked(raw):
    result = parse_money(raw)
    assert result.ok is False


def test_to_cents_and_sum():
    a = parse_money("2300.41123210").amount
    b = parse_money("100.0051").amount
    assert to_cents(a) == 230041
    assert to_cents(b) == 10001
    assert sum_amounts([a, b]) == Decimal("2400.42")
    assert format_pesos_from_cents(29863880) == "298638.80"


def test_rejects_float_type_for_to_cents():
    with pytest.raises(TypeError):
        to_cents(1.5)  # type: ignore[arg-type]
