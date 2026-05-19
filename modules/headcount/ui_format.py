from __future__ import annotations

import re
from typing import Any

from modules.headcount.matching import es_warning_critico, normalize_text


def is_empty_ui_value(value: Any) -> bool:
    s = str(value or "").strip()
    if not s:
        return True
    lowered = s.casefold()
    return lowered in {"nan", "none", "null", "nat", "<na>"}


def display_cell(value: Any, *, empty: str = "—") -> str:
    if is_empty_ui_value(value):
        return empty
    s = str(value).strip()
    if s.casefold() in {"nan", "none", "null", "nat"}:
        return empty
    return s


def display_cliente(value: Any) -> str:
    if is_empty_ui_value(value):
        return "Sin cliente"
    return display_cell(value)


def display_ubicacion(value: Any) -> str:
    if is_empty_ui_value(value):
        return "Sin ubicación"
    return display_cell(value)


def display_registro_patronal(*values: Any) -> str:
    for raw in values:
        s = str(raw or "").strip()
        if not s:
            continue
        if s.casefold() in {"nan", "none", "null", "nombre", "n o m b r e"}:
            continue
        if re.search(r"\d", s) or re.search(r"[A-Z]\d{2}-\d", s, re.IGNORECASE):
            return s
        if len(s) >= 8 and "-" in s:
            return s
    return "No detectado"


def display_periodo_corte(periodo: Any, fecha_corte: Any) -> str:
    p = display_cell(periodo, empty="")
    f = display_cell(fecha_corte, empty="")
    if p and f:
        return f"{p} · {f}"
    if p:
        return p
    if f:
        return f
    return "—"


def agrupar_resumen_por_cliente(detalle: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}

    for row in detalle:
        raw_cliente = str(row.get("cliente_headcount") or "").strip()
        key = raw_cliente
        if key not in buckets:
            buckets[key] = {
                "cliente_key": key,
                "cliente_label": display_cliente(raw_cliente),
                "activos_sua": 0,
                "bajas_sua": 0,
                "match_activos": 0,
                "activos_sin_match": 0,
                "bajas_conciliadas": 0,
                "warnings": 0,
                "ubicaciones": {},
            }
        b = buckets[key]
        if row.get("sua_es_activo_al_corte"):
            b["activos_sua"] += 1
            if row.get("match_status") in {"MATCH_CURP", "MATCH_NSS", "MATCH_NOMBRE"}:
                b["match_activos"] += 1
            if "SUA_ACTIVO_SIN_MATCH_HEADCOUNT" in (row.get("warnings") or []):
                b["activos_sin_match"] += 1
        else:
            b["bajas_sua"] += 1
        if row.get("info_estado") == "BAJA_CONCILIADA":
            b["bajas_conciliadas"] += 1
        b["warnings"] += len([w for w in (row.get("warnings") or []) if es_warning_critico(w)])

        raw_ubic = str(row.get("ubicacion_headcount") or "").strip()
        ubic_map = b["ubicaciones"]
        if raw_ubic not in ubic_map:
            ubic_map[raw_ubic] = {
                "ubicacion_key": raw_ubic,
                "ubicacion_label": display_ubicacion(raw_ubic),
                "total": 0,
            }
        ubic_map[raw_ubic]["total"] += 1

    out: list[dict[str, Any]] = []
    for b in buckets.values():
        ubics = sorted(
            b["ubicaciones"].values(),
            key=lambda u: (-int(u.get("total") or 0), str(u.get("ubicacion_label", "")).casefold()),
        )
        b["ubicaciones_list"] = ubics[:12]
        out.append(b)
    return sorted(out, key=lambda x: (x["cliente_label"].casefold(), x["cliente_key"].casefold()))


def clientes_detectados_labels(detalle: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: dict[str, str] = {}
    for row in detalle:
        key = str(row.get("cliente_headcount") or "").strip()
        seen[key] = display_cliente(key)
    return [{"key": k, "label": v} for k, v in sorted(seen.items(), key=lambda item: item[1].casefold())]
