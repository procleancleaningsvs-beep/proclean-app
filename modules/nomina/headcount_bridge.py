from __future__ import annotations

from datetime import date, datetime
from typing import Any

from modules.comparativo.headcount_service import (
    _is_empty as hc_is_empty,
    _normalize_header as hc_normalize_header,
    _normalize_name as hc_normalize_name,
    _normalize_spaces as hc_normalize_spaces,
    obtener_df_headcount,
)
from modules.nomina.vacaciones_util import sanitize_display_value

_HEADER_MARKERS = frozenset({"STATUS OPERACIÓN", "STATUS OPERACION"})
_COLS = (
    ("nombre_completo", "NOMBRE COMPLETO"),
    ("cliente", "CLIENTE"),
    ("patron", "PATRON"),
    ("fecha_ingreso", "FECHA DE INGRESO"),
    ("sueldo_diario", "SUELDO DIARIO"),
    ("puesto", "PUESTO"),
    ("nss", "NSS"),
    ("status_operacion", "STATUS OPERACIÓN"),
    ("status_imss", "STATUS IMSS"),
)


def _format_fecha(value: Any) -> str:
    if hc_is_empty(value):
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[:10], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def _find_header(df) -> tuple[int, dict[str, int]]:
    for i, row_vals in enumerate(df.itertuples(index=False, name=None)):
        normalized = [hc_normalize_header(v) for v in row_vals]
        if _HEADER_MARKERS & set(normalized):
            return i, {normalized[j]: j for j in range(len(normalized))}
    raise ValueError("No se encontró encabezado STATUS OPERACIÓN en Headcount.")


def _col_idx(header_map: dict[str, int], name: str) -> int | None:
    if name in header_map:
        return header_map[name]
    alt = name.replace("Ó", "O")
    if alt in header_map:
        return header_map[alt]
    return None


def _cell(row: tuple[Any, ...] | list[Any], idx: int | None) -> Any:
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def obtener_headcount_completo() -> list[dict[str, Any]]:
    df = obtener_df_headcount()
    if df is None or getattr(df, "empty", True):
        return []

    header_row_idx, header_map = _find_header(df)
    col_idx = {field: _col_idx(header_map, header) for field, header in _COLS}

    registros: list[dict[str, Any]] = []
    body = df.iloc[header_row_idx + 1 :]
    for row_vals in body.itertuples(index=False, name=None):
        row = tuple(row_vals)
        nombre = hc_normalize_name(_cell(row, col_idx["nombre_completo"]) or "")
        if not nombre:
            continue
        status_op = hc_normalize_spaces(
            sanitize_display_value(_cell(row, col_idx["status_operacion"]) or "")
        ).upper()
        status_imss = hc_normalize_spaces(
            sanitize_display_value(_cell(row, col_idx["status_imss"]) or "")
        ).upper()
        sueldo_raw = _cell(row, col_idx["sueldo_diario"])
        registros.append(
            {
                "nombre_completo": nombre,
                "cliente": hc_normalize_spaces(str(_cell(row, col_idx["cliente"]) or "").strip()),
                "patron": hc_normalize_spaces(str(_cell(row, col_idx["patron"]) or "").strip()),
                "fecha_ingreso": _format_fecha(_cell(row, col_idx["fecha_ingreso"])),
                "sueldo_diario": None if hc_is_empty(sueldo_raw) else sueldo_raw,
                "puesto": hc_normalize_spaces(str(_cell(row, col_idx["puesto"]) or "").strip()),
                "nss": hc_normalize_spaces(str(_cell(row, col_idx["nss"]) or "").strip()),
                "status_operacion": status_op or "DESCONOCIDO",
                "status_imss": status_imss or "DESCONOCIDO",
            }
        )
    return registros
