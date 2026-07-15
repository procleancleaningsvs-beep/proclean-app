from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from typing import Any


def normalize_header(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text).strip().upper()
    return text


def normalize_name(value: Any) -> str:
    return normalize_header(value)


def normalize_comment_for_match(value: Any) -> str:
    text = normalize_header(value)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


BANORTE_SUBSTITUTION_PHRASE = normalize_comment_for_match(
    "El numero de empleado ya existia se asigno el numero de cuenta como tu numero de Empleado"
)


def is_banorte_employee_substituted_comment(text: Any) -> bool:
    if text is None:
        return False
    norm = normalize_comment_for_match(text)
    if not norm:
        return False
    # High-confidence: phrase containment after normalization (not broad fuzzy).
    return BANORTE_SUBSTITUTION_PHRASE in norm or norm in BANORTE_SUBSTITUTION_PHRASE


def extract_identifier_cell(value: Any, *, number_format: str | None = None) -> tuple[str | None, str | None]:
    """Return (digits_or_text, error_code).

    error_code PRECISION_RISK when Excel numeric precision may be unsafe.
    """
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, "invalid_identifier"
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None, None
        digits = "".join(ch for ch in raw if ch.isdigit())
        return (digits if digits else raw), None
    if isinstance(value, int):
        digits = str(value)
        fmt = str(number_format or "")
        # Apply zero mask only when format is a simple zero mask of equal/greater width.
        m = re.fullmatch(r"0+", fmt.strip())
        if m and len(fmt.strip()) >= len(digits):
            digits = digits.zfill(len(fmt.strip()))
        if len(digits) > 15:
            return None, "PRECISION_RISK"
        return digits, None
    if isinstance(value, float):
        # Floats from Excel are unsafe for long identifiers.
        as_int = int(value)
        if float(as_int) != value:
            return None, "PRECISION_RISK"
        digits = str(as_int)
        if len(digits) > 15:
            return None, "PRECISION_RISK"
        fmt = str(number_format or "")
        m = re.fullmatch(r"0+", fmt.strip())
        if m and len(fmt.strip()) >= len(digits):
            digits = digits.zfill(len(fmt.strip()))
        return digits, None
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            return None, "PRECISION_RISK"
        digits = str(int(value))
        if len(digits) > 15:
            return None, "PRECISION_RISK"
        return digits, None
    return None, "invalid_identifier"


def safe_upload_filename(filename: str) -> str:
    name = (filename or "").replace("\\", "/").split("/")[-1].strip()
    if not name or name in {".", ".."}:
        raise ValueError("invalid_filename")
    if ".." in name:
        raise ValueError("path_traversal")
    return name


def digits_only(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def is_valid_employee_number(value: Any) -> bool:
    """Canonical: digits only, 1–10 positions (matches pag_layout / alta)."""
    digits = digits_only(value)
    return 1 <= len(digits) <= 10


def is_valid_account_number(value: Any) -> bool:
    """Canonical: digits only, 1–18 positions (matches pag_layout / alta)."""
    digits = digits_only(value)
    return 1 <= len(digits) <= 18


def normalize_banco(value: Any) -> str:
    return str(value or "").strip().casefold()


def is_exact_banorte_bank(value: Any) -> bool:
    return normalize_banco(value) == "banorte"
