"""Triple match Headcount + Nómina actual + CONTPAQ para parámetros base.

Microfase 4.0. No descartar registros sin match: dejarlos como pendientes.
Precheck para motor 4.1+: warnings estables y flags en editable_json.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from modules.nomina.config import (
    WARN_BLOCK_CALC_MISSING_SALARY,
    WARN_BLOCK_CALC_MISSING_VALOR_HE,
    WARN_FRONTERA_EXCEL_VS_LEARNED,
    WARN_HEADCOUNT_UNAVAILABLE,
    WARN_LOCALIDAD_FRONTERA_DEMOTION_BLOCKED,
    WARN_REVIEW_NO_CONFIDENT_MATCH,
    WARN_SAME_NSS_MULTIPLE_CLIENTS,
    get_exento_he_for_year,
    get_smg_for_year,
)
from modules.nomina.db import localidad_is_frontera


@dataclass
class HeadcountIndex:
    by_nss: dict[str, dict]
    by_name: dict[str, list[dict]]
    source: str = "headcount"
    unavailable_reason: str | None = None

    @property
    def is_matchable(self) -> bool:
        return self.unavailable_reason is None


def _norm_name(value: Any) -> str:
    raw = " ".join(str(value or "").replace("\u00a0", " ").replace("\n", " ").split()).strip().upper()
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = "".join(ch if (ch.isalnum() or ch == " ") else " " for ch in raw)
    return " ".join(raw.split())


def build_headcount_index(
    headcount_rows: list[dict],
    *,
    unavailable_reason: str | None = None,
) -> HeadcountIndex:
    """Si ``unavailable_reason`` está definido (p. ej. OneDrive no configurado),
    el índice queda vacío y ``match_to_headcount`` devuelve ``pending_headcount_unavailable``.
    """
    if unavailable_reason:
        return HeadcountIndex(
            by_nss={},
            by_name={},
            source="unavailable",
            unavailable_reason=unavailable_reason,
        )
    by_nss: dict[str, dict] = {}
    by_name: dict[str, list[dict]] = {}
    for item in headcount_rows:
        nss = str(item.get("nss") or "").strip()
        nombre = _norm_name(item.get("nombre_completo"))
        if nss:
            by_nss[nss] = item
        if nombre:
            by_name.setdefault(nombre, []).append(item)
    return HeadcountIndex(by_nss=by_nss, by_name=by_name, source="headcount", unavailable_reason=None)


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
    """Return (match_status, hc_record, score)."""
    if index.unavailable_reason:
        return "pending_headcount_unavailable", None, 0.0

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
    """Resolve (es_frontera, smg, exento_he, warnings).

    No degradar frontera aprendida si un Excel nuevo trae FRONTERA=FALSO: se conserva
    frontera y se emite warning (catálogo ``nomina_localidades_frontera``).
    """
    warnings: list[str] = []
    learned: bool | None = None
    if localidad_normalizada:
        learned = localidad_is_frontera(db_path, cliente or "", localidad_normalizada)

    hint = es_frontera_hint
    is_frontera: bool

    if hint is True:
        is_frontera = True
    elif hint is False and learned is True:
        warnings.append(
            f"{WARN_FRONTERA_EXCEL_VS_LEARNED}: Excel indica GENERAL pero la localidad "
            f"está catalogada como fronteriza; se mantiene FRONTERA para cálculo."
        )
        is_frontera = True
    elif hint is False:
        is_frontera = False
    elif learned is not None:
        is_frontera = bool(learned)
    else:
        if (cliente or "").strip().lower() == "pepsi":
            warnings.append(
                "Localidad no clasificada como frontera/general; aplicando GENERAL por defecto (Pepsi)."
            )
        else:
            warnings.append(
                "Localidad no clasificada como frontera/general; aplicando GENERAL por defecto."
            )
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


def append_parametro_precheck_warnings(row: dict[str, Any]) -> None:
    """Añade códigos de warning y flags para el futuro motor (4.1+), sin bloquear UI."""
    warnings: list[str] = list(row.get("warnings") or [])
    ed: dict[str, Any] = dict(row.get("editable_json") or {})

    sal = row.get("salario_operativo")
    if sal is None or (isinstance(sal, (int, float)) and float(sal) <= 0):
        if WARN_BLOCK_CALC_MISSING_SALARY not in warnings:
            warnings.append(WARN_BLOCK_CALC_MISSING_SALARY)
        ed["block_calc_missing_salary_operativo"] = True
    else:
        ed["block_calc_missing_salary_operativo"] = False

    he_qty = row.get("horas_extra_periodo")
    valor_he = row.get("valor_x_he")
    if he_qty is not None and float(he_qty) > 0:
        if valor_he is None or (isinstance(valor_he, (int, float)) and float(valor_he) <= 0):
            if WARN_BLOCK_CALC_MISSING_VALOR_HE not in warnings:
                warnings.append(WARN_BLOCK_CALC_MISSING_VALOR_HE)
            ed["block_calc_missing_valor_x_he_when_he"] = True
        else:
            ed["block_calc_missing_valor_x_he_when_he"] = False
    elif valor_he is not None and (isinstance(valor_he, (int, float)) and float(valor_he) > 0):
        # Horas extra desconocidas en este import pero ya hay tarifa HE válida.
        ed["block_calc_missing_valor_x_he_when_he"] = False

    ms = str(row.get("headcount_match_status") or "")
    if ms in {
        "pending_headcount_unavailable",
        "no_match_headcount",
        "probable_match",
        "multiple_candidates",
    }:
        if WARN_REVIEW_NO_CONFIDENT_MATCH not in warnings:
            warnings.append(WARN_REVIEW_NO_CONFIDENT_MATCH)
        ed["review_no_confident_headcount_match"] = True
    elif ms not in {"exact_nss", "exact_name"}:
        ed.pop("review_no_confident_headcount_match", None)
    else:
        ed.pop("review_no_confident_headcount_match", None)

    if ms == "pending_headcount_unavailable":
        if WARN_HEADCOUNT_UNAVAILABLE not in warnings:
            warnings.append(WARN_HEADCOUNT_UNAVAILABLE)
        ed["headcount_unavailable_pending_match"] = True
    else:
        ed.pop("headcount_unavailable_pending_match", None)

    row["warnings"] = warnings
    row["editable_json"] = ed


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

    row = {
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
        "horas_extra_periodo": parsed_row.get("horas_extra_periodo"),
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
    append_parametro_precheck_warnings(row)
    return row


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

    row = {
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
        "horas_extra_periodo": None,
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
    append_parametro_precheck_warnings(row)
    return row
