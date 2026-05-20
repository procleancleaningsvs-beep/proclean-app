from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from modules.headcount.matching import es_warning_critico, normalize_text

SIN_CLIENTE_CARD_KEY = "__SIN_CLIENTE__"
_SIN_CLIENTE_BUCKET_KEYS = frozenset(
    {
        "",
        "SIN CLIENTE",
        "SIN CLIENTE HC",
        "S O",
        "N A",
        "NA",
        "NAN",
        "NONE",
        "NULL",
        "NAT",
    }
)


def es_cliente_bucket_invalido(cliente_key: str) -> bool:
    """True si el bucket no debe mostrarse como tarjeta de cliente (va en Sin cliente / SIN_MATCH)."""
    if cliente_key == SIN_CLIENTE_CARD_KEY:
        return True
    raw = str(cliente_key or "").strip()
    if is_empty_ui_value(raw):
        return True
    norm = normalize_text(raw)
    if norm in _SIN_CLIENTE_BUCKET_KEYS:
        return True
    return normalize_text(display_cliente(raw)) in _SIN_CLIENTE_BUCKET_KEYS


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


def parse_fecha_ingreso(value: Any) -> date | None:
    if is_empty_ui_value(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        import pandas as pd

        if isinstance(value, pd.Timestamp):
            if pd.isna(value):
                return None
            return value.date()
    except ImportError:
        pass
    if isinstance(value, (int, float)):
        try:
            serial = float(value)
            if 1 < serial < 100000:
                base = datetime(1899, 12, 30)
                return (base + timedelta(days=serial)).date()
        except (ValueError, OverflowError, TypeError):
            pass
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def display_fecha_ingreso(value: Any) -> str:
    parsed = parse_fecha_ingreso(value)
    if not parsed:
        return "—"
    return parsed.strftime("%d/%m/%Y")


def sort_value_date(value: Any) -> str:
    parsed = parse_fecha_ingreso(value)
    return parsed.isoformat() if parsed else ""


def sort_value_number(value: Any) -> str:
    if is_empty_ui_value(value):
        return ""
    try:
        return f"{float(value):015.4f}"
    except (TypeError, ValueError):
        return ""


def parse_fecha_corte_auditoria(fecha_corte: Any, fecha_proceso: Any = "") -> date | None:
    return parse_fecha_ingreso(fecha_corte) or parse_fecha_ingreso(fecha_proceso)


def _months_before(corte: date, months: int) -> date:
    year = corte.year
    month = corte.month - months
    while month <= 0:
        month += 12
        year -= 1
    days_in_month = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    day = min(corte.day, days_in_month[month - 1])
    return date(year, month, day)


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


def _accumulate_cliente_bucket(buckets: dict[str, dict[str, Any]], row: dict[str, Any], *, key: str) -> None:
    raw_cliente = key
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
            "es_sin_cliente_virtual": False,
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


def resumen_otro_patron_card(detalle: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [r for r in detalle if r.get("match_status") == "MATCH_OTRO_PATRON"]
    card = {
        "cliente_key": "__OTRO_PATRON__",
        "cliente_label": "En otro patrón",
        "activos_sua": 0,
        "bajas_sua": 0,
        "match_activos": 0,
        "activos_sin_match": 0,
        "bajas_conciliadas": 0,
        "warnings": 0,
        "ubicaciones_list": [],
        "es_otro_patron_virtual": True,
        "total_registros": len(rows),
    }
    for row in rows:
        if row.get("sua_es_activo_al_corte"):
            card["activos_sua"] += 1
            card["match_activos"] += 1
        else:
            card["bajas_sua"] += 1
        if row.get("info_estado") == "BAJA_CONCILIADA":
            card["bajas_conciliadas"] += 1
        card["warnings"] += len([w for w in (row.get("warnings") or []) if es_warning_critico(w)])
    return card


def resumen_sin_cliente_card(detalle: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [r for r in detalle if r.get("match_status") == "SIN_MATCH"]
    card = {
        "cliente_key": SIN_CLIENTE_CARD_KEY,
        "cliente_label": "Sin cliente",
        "activos_sua": 0,
        "bajas_sua": 0,
        "match_activos": 0,
        "activos_sin_match": 0,
        "bajas_conciliadas": 0,
        "warnings": 0,
        "ubicaciones_list": [],
        "es_sin_cliente_virtual": True,
        "total_registros": len(rows),
    }
    for row in rows:
        if row.get("sua_es_activo_al_corte"):
            card["activos_sua"] += 1
            card["activos_sin_match"] += 1
        else:
            card["bajas_sua"] += 1
        if row.get("info_estado") == "BAJA_CONCILIADA":
            card["bajas_conciliadas"] += 1
        card["warnings"] += len([w for w in (row.get("warnings") or []) if es_warning_critico(w)])
    return card


def agrupar_resumen_por_cliente(detalle: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}

    for row in detalle:
        if row.get("match_status") == "SIN_MATCH":
            continue
        raw_cliente = str(row.get("cliente_headcount") or "").strip()
        if es_cliente_bucket_invalido(raw_cliente):
            continue
        key = raw_cliente
        _accumulate_cliente_bucket(buckets, row, key=key)

    out: list[dict[str, Any]] = []
    for b in buckets.values():
        ubics = sorted(
            b["ubicaciones"].values(),
            key=lambda u: (-int(u.get("total") or 0), str(u.get("ubicacion_label", "")).casefold()),
        )
        b["ubicaciones_list"] = ubics[:12]
        out.append(b)
    return sorted(out, key=lambda x: (x["cliente_label"].casefold(), x["cliente_key"].casefold()))


def build_cliente_cards_for_ui(
    detalle: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    clientes = [
        c
        for c in agrupar_resumen_por_cliente(detalle)
        if not c.get("es_sin_cliente_virtual") and not es_cliente_bucket_invalido(c.get("cliente_key", ""))
    ]
    sin_cliente = resumen_sin_cliente_card(detalle)
    otro_patron = resumen_otro_patron_card(detalle)
    return clientes, sin_cliente, otro_patron


def clientes_detectados_labels(detalle: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: dict[str, str] = {}
    for row in detalle:
        if row.get("match_status") == "SIN_MATCH":
            continue
        key = str(row.get("cliente_headcount") or "").strip()
        if es_cliente_bucket_invalido(key):
            continue
        seen[key] = display_cliente(key)
    return [{"key": k, "label": v} for k, v in sorted(seen.items(), key=lambda item: item[1].casefold())]
