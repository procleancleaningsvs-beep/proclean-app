"""Utilidades de sanitización, status y match para Vacaciones."""

from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd

MATCH_OK = "MATCH_OK"
MATCH_AMBIGUO = "MATCH_AMBIGUO"
SIN_MATCH = "SIN_MATCH"
PENDIENTE_REVISION = "PENDIENTE_REVISION"

MATCH_METHOD_NSS = "nss"
MATCH_METHOD_NOMBRE = "nombre_completo"
MATCH_METHOD_SIN_MATCH = "sin_match"
MATCH_METHOD_MANUAL = "manual"


def sanitize_display_value(value: Any, *, empty_label: str = "") -> str:
    """Convierte NaN/None/null a etiqueta controlada; nunca muestra 'nan' al usuario."""
    if value is None:
        return empty_label
    try:
        if pd.isna(value):
            return empty_label
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return empty_label
    s = str(value).strip()
    if not s or s.lower() in {"none", "null", "nan", "nat", "<na>"}:
        return empty_label
    return s


def resolve_status_headcount(hc_row: dict[str, Any] | None) -> str:
    """STATUS operativo del trabajador — solo desde Headcount."""
    if not hc_row:
        return "SIN STATUS HEADCOUNT"
    status_op = sanitize_display_value(hc_row.get("status_operacion")).upper()
    status_imss = sanitize_display_value(hc_row.get("status_imss")).upper()
    raw = status_op or status_imss
    if not raw or raw in {"DESCONOCIDO", "NAN", "NONE", "NULL"}:
        return "SIN STATUS HEADCOUNT"
    if "BAJA" in raw:
        return "BAJA"
    if raw == "ALTA" or "ACTIV" in raw:
        return "ACTIVO"
    if "INACTIV" in raw:
        return "INACTIVO"
    return raw


def normalize_match_status_legacy(value: str | None) -> str:
    """Mapea valores legacy a los nuevos estados de match."""
    legacy = sanitize_display_value(value).upper()
    mapping = {
        "EXACT_NSS": MATCH_OK,
        "MATCH_NAME": MATCH_OK,
        "MATCH_NOMBRE": MATCH_OK,
        "INACTIVE_MATCH": MATCH_OK,
        "POSSIBLE_REENTRY": MATCH_OK,
        "PROBABLE_MATCH": MATCH_AMBIGUO,
        "PENDING_REVIEW": PENDIENTE_REVISION,
        "NO_MATCH": SIN_MATCH,
    }
    if legacy in mapping:
        return mapping[legacy]
    if legacy in {MATCH_OK, MATCH_AMBIGUO, SIN_MATCH, PENDIENTE_REVISION}:
        return legacy
    return legacy or PENDIENTE_REVISION


def enrich_vacaciones_row_for_display(row: dict[str, Any]) -> dict[str, Any]:
    """Enriquece fila para UI con campos separados y sanitizados."""
    out = dict(row)
    out["status_headcount"] = sanitize_display_value(
        row.get("status_headcount") or row.get("estatus_headcount"),
        empty_label="SIN STATUS HEADCOUNT",
    ) or "SIN STATUS HEADCOUNT"
    out["match_status_display"] = normalize_match_status_legacy(row.get("match_status"))
    out["match_method_display"] = sanitize_display_value(row.get("match_method"), empty_label="—") or "—"
    out["match_notes_display"] = sanitize_display_value(row.get("match_notes"), empty_label="")
    out["excel_nombre_original"] = sanitize_display_value(row.get("excel_nombre_original") or row.get("nombre_historico"))
    out["headcount_nombre_original"] = sanitize_display_value(row.get("headcount_nombre_original") or row.get("nombre_headcount"))
    editable = dict(row.get("editable_json") or {})
    out["desglose_semanal"] = editable.get("desglose_semanal") or []
    out["excel_resumen"] = editable.get("excel_resumen") or {}
    out["warnings_detalle"] = editable.get("warnings_detalle") or []
    out["prima_historica_detalle"] = editable.get("prima_historica_detalle") or []
    out["prima_pagada"] = bool(row.get("prima_2025_pagada") or row.get("prima_2026_pagada"))
    out["nombre_normalizado"] = sanitize_display_value(row.get("nombre_normalizado"), empty_label="")
    out["clasificacion_conciliacion"] = sanitize_display_value(
        row.get("clasificacion_conciliacion") or out["excel_resumen"].get("clasificacion_conciliacion"),
        empty_label="",
    )
    out["diferencia_detectada"] = row.get("diferencia_detectada")
    out["fuente_fecha_ingreso"] = sanitize_display_value(
        row.get("fuente_fecha_ingreso") or out["excel_resumen"].get("fuente_fecha_ingreso"),
        empty_label="EXCEL",
    ) or "EXCEL"
    out["fuente_salario"] = sanitize_display_value(
        row.get("fuente_salario") or out["excel_resumen"].get("fuente_salario"),
        empty_label="EXCEL",
    ) or "EXCEL"
    out["salario_parametros_nomina"] = row.get("salario_parametros_nomina") or out["excel_resumen"].get("salario_parametros_nomina")
    return out
