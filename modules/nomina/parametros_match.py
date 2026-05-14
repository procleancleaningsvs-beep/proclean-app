"""Triple match Headcount + Nómina actual + CONTPAQ para parámetros base.

Microfase 4.0. No descartar registros sin match: dejarlos como pendientes.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from modules.nomina.config import (
    get_exento_he_for_year,
    get_smg_for_year,
)
from modules.nomina.db import localidad_is_frontera


@dataclass
class HeadcountIndex:
    by_nss: dict[str, dict]
    by_name: dict[str, list[dict]]
    source: str


def _norm_name(value: Any) -> str:
    raw = " ".join(str(value or "").replace("\u00a0", " ").replace("\n", " ").split()).strip().upper()
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = "".join(ch if (ch.isalnum() or ch == " ") else " " for ch in raw)
    return " ".join(raw.split())


def build_headcount_index(headcount_rows: list[dict]) -> HeadcountIndex:
    by_nss: dict[str, dict] = {}
    by_name: dict[str, list[dict]] = {}
    for item in headcount_rows:
        nss = str(item.get("nss") or "").strip()
        nombre = _norm_name(item.get("nombre_completo"))
        if nss:
            by_nss[nss] = item
        if nombre:
            by_name.setdefault(nombre, []).append(item)
    return HeadcountIndex(by_nss=by_nss, by_name=by_name, source="headcount")


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def match_to_headcount(
    *,
    nombre: str | None,
    nss: str | None,
    cliente: str | None,
    index: HeadcountIndex,
) -> tuple[str, dict | None, float]:
    """Return (match_status, hc_record, score). Statuses follow the spec:
    exact_nss, exact_name, probable_match, multiple_candidates, no_match_headcount."""
    nombre_norm = _norm_name(nombre)
    if nss and nss in index.by_nss:
        return "exact_nss", index.by_nss[nss], 1.0
    if nombre_norm and nombre_norm in index.by_name:
        candidates = index.by_name[nombre_norm]
        if cliente:
            target_cliente = _norm_name(cliente)
            matches = [c for c in candidates if _norm_name(c.get("cliente")) == target_cliente]
            if len(matches) == 1:
                return "exact_name", matches[0], 1.0
        if len(candidates) == 1:
            return "exact_name", candidates[0], 1.0
        return "multiple_candidates", candidates[0], 0.95
    if nombre_norm:
        best_score = 0.0
        best_record: dict | None = None
        ambiguous: list[dict] = []
        for name_key, items in index.by_name.items():
            score = _similar(nombre_norm, name_key)
            if score > best_score:
                best_score = score
                best_record = items[0]
                ambiguous = items
            elif score >= 0.92 and score >= best_score - 0.02:
                ambiguous.extend(items)
        if best_score >= 0.92 and best_record is not None:
            if len({_norm_name(r.get("nombre_completo")) for r in ambiguous}) > 1:
                return "multiple_candidates", best_record, best_score
            return "probable_match", best_record, best_score
    return "no_match_headcount", None, 0.0


def derive_smg_from_locality(
    *,
    cliente: str,
    localidad: str | None,
    localidad_normalizada: str | None,
    es_frontera_hint: bool | None,
    year: int,
    db_path: str,
) -> tuple[bool, float | None, float | None, list[str]]:
    """Resolve (es_frontera, smg, exento_he, warnings) using saved localidades + hint."""
    warnings: list[str] = []
    is_frontera: bool | None = None
    if es_frontera_hint is not None:
        is_frontera = bool(es_frontera_hint)
    elif localidad_normalizada:
        flag = localidad_is_frontera(db_path, cliente or "", localidad_normalizada)
        if flag is not None:
            is_frontera = flag
        else:
            warnings.append(
                "Localidad no clasificada como frontera/general; aplicando GENERAL por defecto."
            )
            is_frontera = False
    else:
        if (cliente or "").strip().lower() == "pepsi":
            warnings.append("Cliente Pepsi sin localidad: no se puede inferir frontera.")
        is_frontera = False

    zona = "FRONTERA" if is_frontera else "GENERAL"
    smg = get_smg_for_year(year, zona)
    exento = get_exento_he_for_year(year, zona)
    return (
        bool(is_frontera),
        float(smg) if smg is not None else None,
        float(exento) if exento is not None else None,
        warnings,
    )


def build_parametro_row_from_nomina(
    parsed_row: dict,
    *,
    hc_index: HeadcountIndex,
    db_path: str,
    year: int,
    source_filename: str,
) -> dict:
    """Translate a parsed nomina_actual row into nomina_empleado_parametros payload."""
    match_status, hc, score = match_to_headcount(
        nombre=parsed_row.get("nombre"),
        nss=parsed_row.get("nss"),
        cliente=parsed_row.get("cliente"),
        index=hc_index,
    )

    is_frontera, smg, exento, frontera_warnings = derive_smg_from_locality(
        cliente=parsed_row.get("cliente") or "",
        localidad=parsed_row.get("localidad"),
        localidad_normalizada=parsed_row.get("localidad_normalizada"),
        es_frontera_hint=parsed_row.get("es_frontera"),
        year=year,
        db_path=db_path,
    )

    warnings = list(parsed_row.get("warnings") or [])
    warnings.extend(frontera_warnings)

    nss = parsed_row.get("nss")
    if hc is not None:
        hc_nss = str(hc.get("nss") or "").strip()
        if hc_nss and nss and hc_nss != nss:
            warnings.append(f"NSS distinto entre Headcount ({hc_nss}) y nómina ({nss}).")
        if not nss and hc_nss:
            nss = hc_nss

    cliente = parsed_row.get("cliente") or (hc.get("cliente") if hc else None)

    return {
        "nombre": parsed_row.get("nombre"),
        "nombre_normalizado": parsed_row.get("nombre_normalizado"),
        "nss": nss,
        "numero_empleado": parsed_row.get("numero_empleado"),
        "codigo_contpaq": None,
        "cliente": cliente,
        "planta": parsed_row.get("planta"),
        "puesto": parsed_row.get("puesto") or (hc.get("puesto") if hc else None),
        "banco": parsed_row.get("banco"),
        "cuenta": parsed_row.get("cuenta"),
        "localidad": parsed_row.get("localidad"),
        "localidad_normalizada": parsed_row.get("localidad_normalizada"),
        "salario_operativo": parsed_row.get("salario_operativo"),
        "valor_x_he": parsed_row.get("valor_x_he"),
        "zona_salario_raw": None,
        "es_frontera": is_frontera,
        "salario_minimo_usado": smg,
        "exento_he_usado": exento,
        "fuente_salario_operativo": source_filename if parsed_row.get("salario_operativo") is not None else None,
        "fuente_valor_x_he": source_filename if parsed_row.get("valor_x_he") is not None else None,
        "fuente_numero_empleado": source_filename if parsed_row.get("numero_empleado") else None,
        "fuente_nss": source_filename if parsed_row.get("nss") else None,
        "headcount_match_status": match_status,
        "contpaq_match_status": None,
        "nomina_match_status": "imported",
        "warnings": warnings,
        "editable_json": {
            "match_score": score,
            "source_filename": source_filename,
        },
    }


def build_parametro_row_from_contpaq(
    parsed_row: dict,
    *,
    hc_index: HeadcountIndex,
    source_filename: str,
) -> dict:
    match_status, hc, score = match_to_headcount(
        nombre=parsed_row.get("nombre"),
        nss=parsed_row.get("nss"),
        cliente=None,
        index=hc_index,
    )

    warnings = list(parsed_row.get("warnings") or [])
    nss = parsed_row.get("nss")
    if hc is not None:
        hc_nss = str(hc.get("nss") or "").strip()
        if hc_nss and nss and hc_nss != nss:
            warnings.append(f"NSS distinto entre Headcount ({hc_nss}) y CONTPAQ ({nss}).")
        if not nss and hc_nss:
            nss = hc_nss

    cliente = hc.get("cliente") if hc else None

    return {
        "nombre": parsed_row.get("nombre"),
        "nombre_normalizado": parsed_row.get("nombre_normalizado"),
        "nss": nss,
        "numero_empleado": parsed_row.get("codigo_contpaq"),
        "codigo_contpaq": parsed_row.get("codigo_contpaq"),
        "cliente": cliente,
        "planta": None,
        "puesto": parsed_row.get("puesto") or (hc.get("puesto") if hc else None),
        "banco": None,
        "cuenta": None,
        "localidad": None,
        "localidad_normalizada": None,
        "salario_operativo": None,
        "valor_x_he": None,
        "zona_salario_raw": parsed_row.get("zona_salario_raw"),
        "es_frontera": None,
        "salario_minimo_usado": None,
        "exento_he_usado": None,
        "fuente_salario_operativo": None,
        "fuente_valor_x_he": None,
        "fuente_numero_empleado": source_filename if parsed_row.get("codigo_contpaq") else None,
        "fuente_nss": source_filename if parsed_row.get("nss") else None,
        "headcount_match_status": match_status,
        "contpaq_match_status": "imported",
        "nomina_match_status": None,
        "warnings": warnings,
        "editable_json": {
            "match_score": score,
            "source_filename": source_filename,
            "contpaq_extra": {
                "fecha_alta": parsed_row.get("fecha_alta"),
                "fecha_baja": parsed_row.get("fecha_baja"),
                "fecha_reingreso": parsed_row.get("fecha_reingreso"),
                "estatus": parsed_row.get("estatus"),
                "departamento": parsed_row.get("departamento"),
                "registro_patronal": parsed_row.get("registro_patronal"),
                "rfc": parsed_row.get("rfc"),
                "curp": parsed_row.get("curp"),
                "salario_diario": parsed_row.get("salario_diario"),
            },
        },
    }
