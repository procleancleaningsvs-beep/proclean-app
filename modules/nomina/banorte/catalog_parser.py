from __future__ import annotations

import hashlib
import re
import struct
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path


CATALOG_PARSER_VERSION = 1
CATALOG_NORMALIZATION_VERSION = 1
CATALOG_PROJECTION_VERSION = 1
CATALOG_MAX_FILE_BYTES = 10 * 1024 * 1024

CATALOG_HEADER_V1: tuple[str, ...] = (
    "No Empleado",
    "Nombre",
    "Fecha Alta",
    "Fecha Últ. Modificación",
    "Última Modificación",
    "Fecha Nacimiento",
    "RFC",
    "Sueldo Bruto Mensual",
    "Sueldo Neto",
    "Entidad de Nacimiento",
    "Fecha Ingreso",
    "Frecuencia Pago",
    "Entidad",
    "Tipo de Cuenta",
    "No. de Cuenta",
    "Dependencias",
    "Tipo de Operación",
    "Tipo de Transmisión",
    "Estatus Interno",
    "Resultado",
    "Ejecutó",
    "Fecha Ejecucion",
    "Coejecutó",
    "Fecha Coejecucion",
)

CATALOG_ROW_FIELD_NAMES: tuple[str, ...] = (
    "employee_number_original",
    "name_original",
    "record_created_date_original",
    "last_modified_date_original",
    "last_modified_by_original",
    "birth_date_original",
    "rfc_original",
    "gross_salary_original",
    "net_salary_original",
    "birth_state_original",
    "employment_start_date_original",
    "pay_frequency_original",
    "entity_original",
    "account_type_original",
    "account_number_original",
    "dependencies_original",
    "operation_type_original",
    "transmission_type_original",
    "internal_status_original",
    "result_original",
    "executed_by_original",
    "execution_date_original",
    "coexecuted_by_original",
    "coexecution_date_original",
)

_SPANISH_MONTHS = {
    "ENE": 1,
    "FEB": 2,
    "MAR": 3,
    "ABR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AGO": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DIC": 12,
}


class CatalogParseError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ParsedCatalogRow:
    source_position: int
    original_fields: tuple[str, ...]
    row_content_sha256: str
    employee_number_normalized: str | None
    name_normalized: str
    name_controlled_key: str
    record_created_date_iso: str
    last_modified_date_iso: str
    birth_date_iso: str
    rfc_normalized: str
    account_number_normalized: str
    internal_status_normalized: str
    result_normalized: str
    eligibility: str
    eligibility_reason: str | None

    def originals_by_name(self) -> dict[str, str]:
        return dict(zip(CATALOG_ROW_FIELD_NAMES, self.original_fields, strict=True))


@dataclass(frozen=True)
class ParsedCatalog:
    source_filename: str
    file_sha256: str
    file_size_bytes: int
    encoding: str
    delimiter: str
    report_date: str
    issuer_original: str
    issuer_normalized: str
    source_line_count: int
    data_row_count: int
    useful_column_count: int
    rows: tuple[ParsedCatalogRow, ...]


def catalog_name_normalized_v1(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    out: list[str] = []
    for char in decomposed:
        category = unicodedata.category(char)
        if category.startswith("M"):
            continue
        if category[0] in {"P", "Z", "C"}:
            out.append(" ")
        else:
            out.append(char.upper())
    return " ".join("".join(out).split())


def catalog_name_key_v1(value: str) -> str:
    tokens = catalog_name_normalized_v1(value).split()
    return " ".join("MARIA" if token == "MA" else token for token in tokens)


def _normalized_header(value: str) -> str:
    return catalog_name_normalized_v1(value)


def _parse_spanish_date(value: str, *, code: str, required: bool = True) -> str:
    raw = str(value or "").strip()
    if not raw and not required:
        return ""
    numeric_match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
    if numeric_match is not None:
        try:
            return date(
                int(numeric_match.group(3)),
                int(numeric_match.group(2)),
                int(numeric_match.group(1)),
            ).isoformat()
        except ValueError as exc:
            raise CatalogParseError(code) from exc
    match = re.fullmatch(r"(\d{1,2})/([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{3})\.?/(\d{4})", raw)
    if match is None:
        raise CatalogParseError(code)
    month_key = catalog_name_normalized_v1(match.group(2))
    month = _SPANISH_MONTHS.get(month_key)
    if month is None:
        raise CatalogParseError(code)
    try:
        return date(int(match.group(3)), month, int(match.group(1))).isoformat()
    except ValueError as exc:
        raise CatalogParseError(code) from exc


def _digits_or_none(value: str) -> str | None:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    return digits or None


def _row_content_hash(fields: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    digest.update(b"BRC1\0")
    for field in fields:
        encoded = field.encode("utf-8")
        digest.update(struct.pack(">Q", len(encoded)))
        digest.update(encoded)
    return digest.hexdigest()


def _parse_row(tokens: list[str], source_position: int) -> ParsedCatalogRow:
    if len(tokens) != 25 or tokens[-1] != "":
        raise CatalogParseError("data_trailing_delimiter_required")
    fields = tuple(tokens[:-1])
    employee, name, created, modified, _, birth, rfc = fields[:7]
    account = fields[14]
    status = catalog_name_normalized_v1(fields[18])
    result = catalog_name_normalized_v1(fields[19])
    if not name.strip():
        raise CatalogParseError("name_required")
    if not rfc.strip():
        raise CatalogParseError("rfc_required")
    if not account.strip():
        raise CatalogParseError("account_required")
    employee_normalized = _digits_or_none(employee)
    account_normalized = _digits_or_none(account)
    if account_normalized is None:
        raise CatalogParseError("account_invalid")
    created_iso = _parse_spanish_date(created, code="record_created_date_invalid")
    modified_iso = _parse_spanish_date(modified, code="last_modified_date_invalid")
    birth_iso = _parse_spanish_date(birth, code="birth_date_invalid")
    for field_index, code in (
        (10, "employment_start_date_invalid"),
        (21, "execution_date_invalid"),
        (23, "coexecution_date_invalid"),
    ):
        _parse_spanish_date(fields[field_index], code=code, required=False)

    if status != "APLICADO":
        eligibility = "BLOCKED"
        reason = "STATUS_NOT_APLICADO"
    elif result != "REGISTRO ACEPTADO":
        eligibility = "BLOCKED"
        reason = "RESULT_NOT_REGISTRO_ACEPTADO"
    else:
        eligibility = "ELIGIBLE"
        reason = None
    return ParsedCatalogRow(
        source_position=source_position,
        original_fields=fields,
        row_content_sha256=_row_content_hash(fields),
        employee_number_normalized=employee_normalized,
        name_normalized=catalog_name_normalized_v1(name),
        name_controlled_key=catalog_name_key_v1(name),
        record_created_date_iso=created_iso,
        last_modified_date_iso=modified_iso,
        birth_date_iso=birth_iso,
        rfc_normalized=catalog_name_normalized_v1(rfc).replace(" ", ""),
        account_number_normalized=account_normalized,
        internal_status_normalized=status,
        result_normalized=result,
        eligibility=eligibility,
        eligibility_reason=reason,
    )


def parse_catalog_txt(raw: bytes, *, filename: str) -> ParsedCatalog:
    if Path(filename).suffix.lower() != ".txt":
        raise CatalogParseError("extension_invalid")
    if not raw:
        raise CatalogParseError("file_empty")
    if len(raw) > CATALOG_MAX_FILE_BYTES:
        raise CatalogParseError("file_too_large")
    encoding = "UTF-8-BOM" if raw.startswith(b"\xef\xbb\xbf") else "UTF-8"
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CatalogParseError("encoding_invalid") from exc
    lines = text.splitlines()
    if len(lines) < 4:
        raise CatalogParseError("preamble_incomplete")
    report_match = re.fullmatch(r"FECHA:\s*(.+)", lines[0].strip(), re.IGNORECASE)
    issuer_match = re.fullmatch(
        r"EMISORA:\s*(\d{5})(?:\s+.+)?", lines[1].strip(), re.IGNORECASE
    )
    if report_match is None:
        raise CatalogParseError("report_date_missing")
    if issuer_match is None:
        raise CatalogParseError("issuer_invalid")
    if lines[2] != "":
        raise CatalogParseError("preamble_blank_line_required")
    if "|" not in lines[3]:
        raise CatalogParseError("header_delimiter_invalid")
    header_tokens = lines[3].split("|")
    if len(header_tokens) != 25 or header_tokens[-1] != "":
        raise CatalogParseError("header_trailing_delimiter_required")
    if tuple(map(_normalized_header, header_tokens[:-1])) != tuple(
        map(_normalized_header, CATALOG_HEADER_V1)
    ):
        raise CatalogParseError("header_invalid")
    rows: list[ParsedCatalogRow] = []
    for source_position, line in enumerate(lines[4:], start=1):
        if line == "":
            raise CatalogParseError("blank_data_line")
        if "|" not in line:
            raise CatalogParseError("data_delimiter_invalid")
        rows.append(_parse_row(line.split("|"), source_position))
    return ParsedCatalog(
        source_filename=Path(filename).name,
        file_sha256=hashlib.sha256(raw).hexdigest(),
        file_size_bytes=len(raw),
        encoding=encoding,
        delimiter="|",
        report_date=_parse_spanish_date(report_match.group(1), code="report_date_invalid"),
        issuer_original=lines[1].split(":", 1)[1].strip(),
        issuer_normalized=issuer_match.group(1),
        source_line_count=len(lines),
        data_row_count=len(rows),
        useful_column_count=24,
        rows=tuple(rows),
    )
