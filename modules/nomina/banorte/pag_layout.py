from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from modules.nomina.banorte.models import NormalizedPayment
from modules.nomina.banorte.money import to_cents

LINE_WIDTH = 165
CRLF = b"\r\n"
EMISORA = "67059"
BANCO_RECEPTOR = "072"
TIPO_CUENTA = "01"
CLAVE_SERVICIO = "NE"


@dataclass(frozen=True)
class PagField:
    name: str
    record_type: str  # H | D
    start: int
    width: int
    align: str  # left | right
    pad: str
    source: str
    transform: str
    validation: str

    @property
    def end(self) -> int:
        return self.start + self.width


HEADER_FIELDS: list[PagField] = [
    PagField("tipo_registro", "H", 0, 1, "left", " ", "constant", "H", "exact H"),
    PagField("clave_servicio", "H", 1, 2, "left", " ", "constant", "NE", "exact NE"),
    PagField("emisora", "H", 3, 5, "right", "0", "constant", "67059", "digits"),
    PagField("fecha", "H", 8, 8, "right", "0", "layout_date", "YYYYMMDD", "8 digits"),
    PagField("consecutivo", "H", 16, 2, "right", "0", "consecutive", "01-99", "2 digits"),
    PagField("num_registros", "H", 18, 6, "right", "0", "count", "zero-pad", "<=999999"),
    PagField("importe_total", "H", 24, 15, "right", "0", "total_cents", "zero-pad", "<=15 digits"),
    PagField("num_registros_alt", "H", 39, 6, "right", "0", "constant", "000000", "zeros"),
    PagField("importe_alt", "H", 45, 15, "right", "0", "constant", "zeros", "zeros"),
    PagField("num_bajas", "H", 60, 6, "right", "0", "constant", "zeros", "zeros"),
    PagField("importe_bajas", "H", 66, 15, "right", "0", "constant", "zeros", "zeros"),
    PagField("num_verificacion", "H", 81, 6, "right", "0", "constant", "zeros", "zeros"),
    PagField("accion", "H", 87, 1, "left", " ", "constant", "0", "exact 0"),
    PagField("filler_spaces", "H", 88, 77, "left", " ", "constant", "spaces", "spaces"),
]

DETAIL_FIELDS: list[PagField] = [
    PagField("tipo_registro", "D", 0, 1, "left", " ", "constant", "D", "exact D"),
    PagField("fecha", "D", 1, 8, "right", "0", "layout_date", "YYYYMMDD", "8 digits"),
    PagField("num_empleado", "D", 9, 10, "right", "0", "employee_number", "zero-pad", "<=10"),
    PagField("referencia_servicio", "D", 19, 40, "left", " ", "constant", "spaces", "spaces"),
    PagField("campo_secundario", "D", 59, 40, "left", " ", "constant", "spaces", "spaces"),
    PagField("importe", "D", 99, 15, "right", "0", "amount_cents", "zero-pad", "<=15"),
    PagField("banco_receptor", "D", 114, 3, "right", "0", "constant", "072", "exact 072"),
    PagField("tipo_cuenta", "D", 117, 2, "right", "0", "constant", "01", "exact 01"),
    PagField("numero_cuenta", "D", 119, 18, "right", "0", "account_number", "zero-pad", "<=18"),
    PagField("tipo_movimiento", "D", 137, 1, "left", " ", "constant", "0", "exact 0"),
    PagField("accion", "D", 138, 1, "left", " ", "constant", "space", "space"),
    PagField("iva", "D", 139, 8, "right", "0", "constant", "00000000", "zeros"),
    PagField("filler_spaces", "D", 147, 18, "left", " ", "constant", "spaces", "spaces"),
]


class PagLayoutError(ValueError):
    pass


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_filename(consecutive: str) -> str:
    cc = _validate_consecutive(consecutive)
    return f"NI{EMISORA}{cc}.pag"


def _validate_consecutive(consecutive: str) -> str:
    cc = str(consecutive).strip()
    if len(cc) != 2 or not cc.isdigit() or not ("01" <= cc <= "99"):
        raise PagLayoutError("consecutive must be 01-99")
    return cc


def _pad(value: str, width: int, *, align: str, fill: str) -> str:
    if len(value) > width:
        raise PagLayoutError(f"value exceeds width {width}: {len(value)}")
    if align == "right":
        return value.rjust(width, fill)
    return value.ljust(width, fill)


def validate_layout_limits(*, payment_count: int, total_cents: int, employee_number: str, account_number: str) -> None:
    if payment_count < 0 or payment_count > 999_999:
        raise PagLayoutError("payment_count exceeds 6-digit layout field")
    if total_cents < 0 or total_cents > 10**15 - 1:
        raise PagLayoutError("total_cents exceeds 15-digit layout field")
    emp = "".join(ch for ch in str(employee_number) if ch.isdigit())
    acct = "".join(ch for ch in str(account_number) if ch.isdigit())
    if not emp or len(emp) > 10:
        raise PagLayoutError("employee_number incompatible with 10-char field")
    if not acct or len(acct) > 18:
        raise PagLayoutError("account_number incompatible with 18-char field")


def _digits_only(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit())


def build_pag_file(
    *,
    layout_date: str,
    consecutive: str,
    payments: Sequence[NormalizedPayment],
) -> bytes:
    date = str(layout_date).strip()
    if len(date) != 8 or not date.isdigit():
        raise PagLayoutError("layout_date must be YYYYMMDD")
    cc = _validate_consecutive(consecutive)
    if not payments:
        raise PagLayoutError("at least one payment required")

    cents_list: list[int] = []
    for p in payments:
        if not isinstance(p.amount, Decimal):
            raise PagLayoutError("amount must be Decimal")
        cents = to_cents(p.amount)
        if cents <= 0:
            raise PagLayoutError("amount_cents must be > 0")
        emp = _digits_only(p.employee_number)
        acct = _digits_only(p.account_number)
        validate_layout_limits(
            payment_count=len(payments),
            total_cents=0,
            employee_number=emp,
            account_number=acct,
        )
        cents_list.append(cents)

    total_cents = sum(cents_list)
    validate_layout_limits(
        payment_count=len(payments),
        total_cents=total_cents,
        employee_number="1",
        account_number="1",
    )

    header = "".join(
        [
            "H",
            CLAVE_SERVICIO,
            _pad(EMISORA, 5, align="right", fill="0"),
            _pad(date, 8, align="right", fill="0"),
            _pad(cc, 2, align="right", fill="0"),
            _pad(str(len(payments)), 6, align="right", fill="0"),
            _pad(str(total_cents), 15, align="right", fill="0"),
            "000000",
            "000000000000000",
            "000000",
            "000000000000000",
            "000000",
            "0",
            " " * 77,
        ]
    )
    if len(header) != LINE_WIDTH:
        raise PagLayoutError("header width invalid")

    detail_lines: list[str] = []
    for p, cents in zip(payments, cents_list, strict=True):
        emp = _digits_only(p.employee_number)
        acct = _digits_only(p.account_number)
        detail = "".join(
            [
                "D",
                _pad(date, 8, align="right", fill="0"),
                _pad(emp, 10, align="right", fill="0"),
                " " * 40,
                " " * 40,
                _pad(str(cents), 15, align="right", fill="0"),
                BANCO_RECEPTOR,
                TIPO_CUENTA,
                _pad(acct, 18, align="right", fill="0"),
                "0",
                " ",
                "00000000",
                " " * 18,
            ]
        )
        if len(detail) != LINE_WIDTH:
            raise PagLayoutError("detail width invalid")
        if not all(32 <= ord(ch) <= 126 for ch in detail):
            raise PagLayoutError("non-ascii detail")
        detail_lines.append(detail)

    lines = [header, *detail_lines]
    for line in lines:
        if len(line) != LINE_WIDTH:
            raise PagLayoutError("line width must be 165")
        if not all(32 <= ord(ch) <= 126 for ch in line):
            raise PagLayoutError("non-ascii line")

    return CRLF.join(line.encode("ascii") for line in lines)
