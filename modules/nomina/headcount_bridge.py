from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd

from modules.comparativo.headcount_service import (
    _is_empty as hc_is_empty,
    _normalize_header as hc_normalize_header,
    _normalize_name as hc_normalize_name,
    _normalize_spaces as hc_normalize_spaces,
    obtener_df_headcount,
)
from modules.nomina.vacaciones_util import sanitize_display_value


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


def obtener_headcount_completo() -> list[dict[str, Any]]:
    df = obtener_df_headcount()
    header_row_idx = None
    header_map: dict[str, int] = {}
    for i in range(len(df.index)):
        normalized = [hc_normalize_header(v) for v in df.iloc[i].tolist()]
        if "STATUS OPERACIÓN" in normalized or "STATUS OPERACION" in normalized:
            header_row_idx = i
            header_map = {normalized[j]: j for j in range(len(normalized))}
            break
    if header_row_idx is None:
        raise ValueError("No se encontró encabezado STATUS OPERACIÓN en Headcount.")

    def col(name: str) -> int | None:
        if name in header_map:
            return header_map[name]
        alt = name.replace("Ó", "O")
        if alt in header_map:
            return header_map[alt]
        return None

    registros: list[dict[str, Any]] = []
    for i in range(header_row_idx + 1, len(df.index)):
        row = df.iloc[i].tolist()
        nombre = hc_normalize_name(row[col("NOMBRE COMPLETO")] if col("NOMBRE COMPLETO") is not None else "")
        if not nombre:
            continue
        status_op = hc_normalize_spaces(
            sanitize_display_value(row[col("STATUS OPERACIÓN")] if col("STATUS OPERACIÓN") is not None else "")
        ).upper()
        status_imss = hc_normalize_spaces(
            sanitize_display_value(row[col("STATUS IMSS")] if col("STATUS IMSS") is not None else "")
        ).upper()
        registros.append(
            {
                "nombre_completo": nombre,
                "cliente": hc_normalize_spaces(str(row[col("CLIENTE")] if col("CLIENTE") is not None else "").strip()),
                "patron": hc_normalize_spaces(str(row[col("PATRON")] if col("PATRON") is not None else "").strip()),
                "fecha_ingreso": _format_fecha(row[col("FECHA DE INGRESO")] if col("FECHA DE INGRESO") is not None else ""),
                "sueldo_diario": None if hc_is_empty(row[col("SUELDO DIARIO")] if col("SUELDO DIARIO") is not None else None) else row[col("SUELDO DIARIO")],
                "puesto": hc_normalize_spaces(str(row[col("PUESTO")] if col("PUESTO") is not None else "").strip()),
                "nss": hc_normalize_spaces(str(row[col("NSS")] if col("NSS") is not None else "").strip()),
                "status_operacion": status_op or "DESCONOCIDO",
                "status_imss": status_imss or "DESCONOCIDO",
            }
        )
    return registros

