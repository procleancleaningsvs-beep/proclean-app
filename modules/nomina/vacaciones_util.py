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
    return out
