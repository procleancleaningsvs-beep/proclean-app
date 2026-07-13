from __future__ import annotations

import re
from dataclasses import dataclass, field

from modules.nomina.banorte.money import MoneyParseResult, parse_money
from modules.nomina.banorte.validators import normalize_header

NAME_HEADERS = {
    "NOMBRE",
    "NOMBRES",
    "TRABAJADOR",
    "NOMBRE DE EMPLEADO",
    "EMPLEADO",
}
AMOUNT_HEADERS = {
    "IMPORTE",
    "MONTO",
    "TOTAL",
    "NETO",
    "NETO A PAGAR",
    "NETO SIMPLE",
}


@dataclass
class PasteLine:
    position: int
    raw_name: str | None
    raw_amount: str | None
    amount_result: MoneyParseResult | None
    incomplete: bool
    is_header_name: bool = False
    is_header_amount: bool = False


@dataclass
class PasteParseResult:
    rows: list[PasteLine]
    name_headers_detected: list[str] = field(default_factory=list)
    amount_headers_detected: list[str] = field(default_factory=list)
    length_mismatch: bool = False
    warning: str | None = None


def _split_lines(text: str) -> list[str]:
    # Keep empty lines; normalize newlines only.
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")


def parse_paste_lists(names_text: str, amounts_text: str) -> PasteParseResult:
    names = _split_lines(names_text)
    amounts = _split_lines(amounts_text)
    name_headers: list[str] = []
    amount_headers: list[str] = []

    # Strip leading header lines only when exact header match.
    while names and normalize_header(names[0]) in NAME_HEADERS:
        name_headers.append(names[0])
        names = names[1:]
    while amounts and normalize_header(amounts[0]) in AMOUNT_HEADERS:
        amount_headers.append(amounts[0])
        amounts = amounts[1:]

    n = max(len(names), len(amounts))
    rows: list[PasteLine] = []
    for i in range(n):
        raw_name = names[i] if i < len(names) else None
        raw_amount = amounts[i] if i < len(amounts) else None
        # Preserve empty string as present empty line (not None) when within original length
        if i < len(names):
            raw_name = names[i]
        if i < len(amounts):
            raw_amount = amounts[i]
        amt_res = parse_money(raw_amount) if raw_amount is not None and str(raw_amount).strip() != "" else None
        incomplete = (
            raw_name is None
            or raw_amount is None
            or str(raw_name).strip() == ""
            or str(raw_amount).strip() == ""
            or amt_res is None
            or not amt_res.ok
        )
        rows.append(
            PasteLine(
                position=i + 1,
                raw_name=raw_name,
                raw_amount=raw_amount,
                amount_result=amt_res,
                incomplete=incomplete,
            )
        )
    mismatch = len(names) != len(amounts)
    warning = None
    if mismatch:
        warning = f"length_mismatch names={len(names)} amounts={len(amounts)}"
    return PasteParseResult(
        rows=rows,
        name_headers_detected=name_headers,
        amount_headers_detected=amount_headers,
        length_mismatch=mismatch,
        warning=warning,
    )
