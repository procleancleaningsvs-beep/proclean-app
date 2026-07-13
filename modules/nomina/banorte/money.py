from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable

_TWO_PLACES = Decimal("0.01")


@dataclass(frozen=True)
class MoneyParseResult:
    ok: bool
    amount: Decimal | None
    ambiguous: bool
    error: str | None
    rounded: bool = False


def parse_money(raw: str | None) -> MoneyParseResult:
    if raw is None:
        return MoneyParseResult(False, None, False, "empty")
    text = str(raw).strip()
    if not text:
        return MoneyParseResult(False, None, False, "empty")
    lowered = text.lower()
    if any(tok in lowered for tok in ("=", "nan", "inf", "#ref", "#value")):
        return MoneyParseResult(False, None, False, "formula_or_non_numeric")

    cleaned = text
    for sym in ("$", "€", "£", "¥", "MXN", "USD", "EUR"):
        cleaned = cleaned.replace(sym, "")
    cleaned = cleaned.replace("\u00a0", " ").strip()
    cleaned = re.sub(r"\s+", "", cleaned)
    if not cleaned or cleaned in {"-", "+", ".", ",", "-.", ",."}:
        return MoneyParseResult(False, None, False, "empty")

    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative = True
        cleaned = cleaned[1:-1]
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    if cleaned.startswith("-"):
        negative = True
        cleaned = cleaned[1:]

    if not cleaned:
        return MoneyParseResult(False, None, False, "empty")

    # Ambiguous: both separators with unclear roles, e.g. 1.234.567 or 1,234,567.89 is OK
    # but 1.234,567.89 or mixed without clear pattern is ambiguous.
    if not re.fullmatch(r"[0-9.,]+", cleaned):
        return MoneyParseResult(False, None, True, "invalid_chars")

    normalized = _normalize_decimal_string(cleaned)
    if normalized is None:
        return MoneyParseResult(False, None, True, "ambiguous_format")

    try:
        value = Decimal(normalized)
    except (InvalidOperation, ValueError):
        return MoneyParseResult(False, None, True, "unparseable")

    if not value.is_finite():
        return MoneyParseResult(False, None, False, "non_finite")
    if negative:
        value = -value
    if value < 0:
        return MoneyParseResult(False, None, False, "negative")
    if value == 0:
        return MoneyParseResult(False, None, False, "zero")

    quantized = value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    rounded = quantized != value
    # After ROUND_HALF_UP to 2 places, amount always has <= 2 decimals.
    return MoneyParseResult(True, quantized, False, None, rounded=rounded)


def _normalize_decimal_string(cleaned: str) -> str | None:
    """Return a Decimal-friendly string, or None if ambiguous."""
    if "," in cleaned and "." in cleaned:
        last_comma = cleaned.rfind(",")
        last_dot = cleaned.rfind(".")
        if last_comma > last_dot:
            # European: 1.234,56
            intpart = cleaned[:last_comma].replace(".", "")
            frac = cleaned[last_comma + 1 :]
            if not frac.isdigit() or not intpart.replace("-", "").isdigit():
                return None
            return f"{intpart}.{frac}"
        # US: 1,234.56
        intpart = cleaned[:last_dot].replace(",", "")
        frac = cleaned[last_dot + 1 :]
        if not frac.isdigit() or not intpart.isdigit():
            return None
        return f"{intpart}.{frac}"

    if "," in cleaned:
        parts = cleaned.split(",")
        if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) <= 2:
            # 2300,50 → decimal comma
            left = parts[0].replace(".", "")
            if not left.isdigit():
                return None
            return f"{left}.{parts[1]}"
        if all(p.isdigit() for p in parts) and all(len(p) == 3 for p in parts[1:]):
            # thousands separators only: 2,300,500 (no cents) — treat as integer
            return "".join(parts)
        # Ambiguous: 1,234 could be thousand or 1 + 234/1000 in some locales;
        # with exactly 3 digits after one comma and no other commas, US thousands is common.
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit() and len(parts[1]) == 3:
            return parts[0] + parts[1]
        return None

    if "." in cleaned:
        parts = cleaned.split(".")
        if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) <= 2:
            if not parts[0].isdigit() and parts[0] != "":
                return None
            return cleaned if parts[0] != "" else f"0.{parts[1]}"
        if all(p.isdigit() for p in parts) and len(parts) > 2 and all(len(p) == 3 for p in parts[1:]):
            # 1.234.567 European thousands
            return "".join(parts)
        if (
            len(parts) == 2
            and parts[0].isdigit()
            and parts[1].isdigit()
            and len(parts[1]) == 3
            and len(parts[0]) <= 3
        ):
            # Ambiguous 1.234 — thousand grouping vs three-decimal fraction.
            return None
        if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) > 2:
            # Excess fractional digits with a single decimal point — accept for rounding.
            if parts[0] == "" or parts[0].isdigit():
                return cleaned if parts[0] else f"0.{parts[1]}"
            return None
        return None

    if cleaned.isdigit():
        return cleaned
    return None


def to_cents(amount: Decimal) -> int:
    if not isinstance(amount, Decimal):
        raise TypeError("amount must be Decimal")
    q = amount.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    return int((q * 100).to_integral_value(rounding=ROUND_HALF_UP))


def sum_amounts(amounts: Iterable[Decimal]) -> Decimal:
    total = Decimal("0.00")
    for a in amounts:
        if not isinstance(a, Decimal):
            raise TypeError("amounts must be Decimal")
        total += a.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    return total.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def format_pesos_from_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    whole, frac = divmod(cents, 100)
    return f"{sign}{whole}.{frac:02d}"
