from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from modules.headcount.config import patron_matches_auditoria

PATRON_AUDITORIA = "RAFAEL"

_WARNING_LABELS: dict[str, str] = {
    "SUA_ACTIVO_SIN_MATCH_HEADCOUNT": "Activo en SUA al corte sin registro en Headcount RAFAEL",
    "SUA_BAJA_SIN_MATCH_HEADCOUNT": "Baja en SUA del periodo sin registro en Headcount RAFAEL",
    "HEADCOUNT_ACTIVO_NO_APARECE_EN_SUA": "Activo en Headcount RAFAEL pero no aparece en el SUA",
    "HEADCOUNT_ACTIVO_APARECE_BAJA_EN_SUA": (
        "El trabajador aparece con Baja en SUA, pero sigue activo en Headcount. "
        "Revisar actualización de estatus."
    ),
    "HEADCOUNT_BAJA_APARECE_ACTIVO_EN_SUA": (
        "El trabajador aparece activo/cotizando en SUA, pero en Headcount está marcado como baja."
    ),
    "STATUS_IMSS_INCONSISTENTE": "STATUS IMSS no activo en Headcount",
    "STATUS_OPERACION_INCONSISTENTE": "STATUS OPERACIÓN no activo en Headcount",
    "PATRON_DIFERENTE": "Match en Headcount con patrón distinto de RAFAEL",
    "CLIENTE_VACIO": "CLIENTE vacío en Headcount",
    "UBICACION_VACIA": "UBICACIÓN vacía en Headcount",
    "CURP_DUPLICADO_HEADCOUNT": "CURP duplicada en Headcount RAFAEL",
    "NSS_DUPLICADO_HEADCOUNT": "NSS duplicado en Headcount RAFAEL",
    "NOMBRE_DUPLICADO_HEADCOUNT": "Posible nombre duplicado en Headcount RAFAEL",
    "DIAS_MENORES_PERIODO": "Días SUA menores al periodo completo",
    "MOVIMIENTO_EN_SUA": "Movimiento Alta/Rein detectado en SUA",
    "PENSIONADO_CV_IV": "Registro P/CV o P/IV en SUA",
    "DATOS_CLAVE_INCOMPLETOS": "Faltan CURP, NSS o nombre para comparación sólida",
}

_INFO_LABELS: dict[str, str] = {
    "BAJA_CONCILIADA": "Baja conciliada (SUA y Headcount en baja)",
}

_WARNINGS_NO_CRITICOS = frozenset({"SUA_BAJA_SIN_MATCH_HEADCOUNT"})
_INFO_ESTADOS = frozenset({"BAJA_CONCILIADA"})

_STATUS_ACTIVO_OPERACION = frozenset({"ALTA", "ACTIVO", "ACTIVA", "OPERATIVO", "OPERATIVA"})
_STATUS_BAJA_OPERACION = frozenset({"BAJA", "INACTIVO", "INACTIVA", "SUSPENDIDO", "SUSPENDIDA"})
_STATUS_ACTIVO_IMSS = frozenset({"ALTA", "ACTIVO", "ACTIVA", "COTIZANDO", "COTIZANTE"})
_STATUS_BAJA_IMSS = frozenset({"BAJA", "INACTIVO", "INACTIVA", "NO COTIZA", "SIN IMSS"})


def normalize_text(value: Any) -> str:
    s = str(value or "").strip().upper()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_nss(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def normalize_curp(value: Any) -> str:
    s = normalize_text(value).replace(" ", "")
    return s[:18] if len(s) >= 18 else s


def normalize_movimiento_clave(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return ""
    n = normalize_text(raw).replace(" ", "")
    if n in {"PCV", "P CV"}:
        return "P/CV"
    if n in {"PIV", "P IV"}:
        return "P/IV"
    if n in {"ALTA", "BAJA", "REIN"}:
        return n
    if "P/CV" in raw or "PCV" in n:
        return "P/CV"
    if "P/IV" in raw or "PIV" in n:
        return "P/IV"
    return normalize_text(raw)


def sua_tiene_baja(movimiento_clave: Any) -> bool:
    return normalize_movimiento_clave(movimiento_clave) == "BAJA"


def sua_es_activo_al_corte(movimiento_clave: Any) -> bool:
    return not sua_tiene_baja(movimiento_clave)


def estado_sua_al_corte(movimiento_clave: Any) -> str:
    mov = normalize_movimiento_clave(movimiento_clave)
    if mov == "BAJA":
        return "Baja SUA"
    if mov == "ALTA":
        return "Alta periodo"
    if mov == "REIN":
        return "Reingreso periodo"
    if mov in {"P/CV", "P/IV"}:
        return "Pensionado / Otro"
    return "Activo SUA"


def enrich_sua_worker_fields(trabajador: dict[str, Any]) -> dict[str, Any]:
    mov = normalize_movimiento_clave(trabajador.get("movimiento_clave"))
    tiene_baja = mov == "BAJA"
    out = dict(trabajador)
    out["movimiento_clave"] = mov
    out["sua_movimiento_clave"] = mov
    out["sua_tiene_baja"] = tiene_baja
    out["sua_es_activo_al_corte"] = not tiene_baja
    out["estado_sua_al_corte"] = estado_sua_al_corte(mov)
    return out


def nombre_hc_sua_like(record: dict[str, Any]) -> str:
    parts = [
        str(record.get("apellido_paterno") or "").strip(),
        str(record.get("apellido_materno") or "").strip(),
        str(record.get("nombre") or "").strip(),
    ]
    built = " ".join(p for p in parts if p)
    if built:
        return built
    return str(record.get("nombre_completo") or "").strip()


def _name_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _is_status_activo_operacion(status: str) -> bool:
    return normalize_text(status) in _STATUS_ACTIVO_OPERACION


def _is_status_baja_operacion(status: str) -> bool:
    n = normalize_text(status)
    return n in _STATUS_BAJA_OPERACION or (n and n not in _STATUS_ACTIVO_OPERACION and "BAJA" in n)


def _is_status_activo_imss(status: str) -> bool:
    return normalize_text(status) in _STATUS_ACTIVO_IMSS


def _is_status_baja_imss(status: str) -> bool:
    n = normalize_text(status)
    return n in _STATUS_BAJA_IMSS or (n and "BAJA" in n)


def _hc_es_activo(row: dict[str, Any]) -> bool:
    st_op = row.get("status_operacion_headcount", "")
    st_imss = row.get("status_imss_headcount", "")
    if _is_status_baja_operacion(st_op) or _is_status_baja_imss(st_imss):
        return False
    return _is_status_activo_operacion(st_op) or _is_status_activo_imss(st_imss)


def _hc_es_baja(row: dict[str, Any]) -> bool:
    return _is_status_baja_operacion(row.get("status_operacion_headcount", "")) or _is_status_baja_imss(
        row.get("status_imss_headcount", "")
    )


def patron_es_rafael(patron: Any) -> bool:
    return patron_matches_auditoria(patron)


def build_headcount_rafael_indexes(
    registros: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict]], dict[str, list[dict]], dict[str, list[dict]]]:
    rafael: list[dict[str, Any]] = []
    by_curp: dict[str, list[dict]] = {}
    by_nss: dict[str, list[dict]] = {}
    by_nombre: dict[str, list[dict]] = {}
    for rec in registros:
        if not patron_es_rafael(rec.get("patron")):
            continue
        rec = dict(rec)
        rec["headcount_id"] = rec.get("headcount_id") or f"hc_{len(rafael)}"
        rec["nombre_hc_sua_like"] = nombre_hc_sua_like(rec)
        rec["nombre_hc_normalizado"] = normalize_text(rec.get("nombre_completo"))
        rec["nombre_hc_sua_like_normalizado"] = normalize_text(rec["nombre_hc_sua_like"])
        rec["curp_normalizado"] = normalize_curp(rec.get("curp"))
        rec["nss_normalizado"] = normalize_nss(rec.get("nss"))
        rafael.append(rec)
        if rec["curp_normalizado"]:
            by_curp.setdefault(rec["curp_normalizado"], []).append(rec)
        if rec["nss_normalizado"]:
            by_nss.setdefault(rec["nss_normalizado"], []).append(rec)
        nombre_key = rec["nombre_hc_normalizado"] or rec["nombre_hc_sua_like_normalizado"]
        if nombre_key:
            by_nombre.setdefault(nombre_key, []).append(rec)
    return rafael, by_curp, by_nss, by_nombre


def match_trabajador_sua(
    trabajador: dict[str, Any],
    *,
    by_curp: dict[str, list[dict]],
    by_nss: dict[str, list[dict]],
    by_nombre: dict[str, list[dict]],
    nombre_keys: list[str],
) -> dict[str, Any]:
    trab = enrich_sua_worker_fields(trabajador)
    curp_n = normalize_curp(trab.get("curp"))
    nss_n = normalize_nss(trab.get("nss_normalizado") or trab.get("nss_sua_original"))
    nombre_n = normalize_text(trab.get("nombre_normalizado") or trab.get("nombre_sua_original"))
    mov = trab["sua_movimiento_clave"]

    hc_match: dict[str, Any] | None = None
    match_status = "SIN_MATCH"
    match_por = ""

    if curp_n and curp_n in by_curp:
        hc_match = by_curp[curp_n][0]
        match_status = "MATCH_CURP"
        match_por = "CURP"
    elif nss_n and nss_n in by_nss:
        hc_match = by_nss[nss_n][0]
        match_status = "MATCH_NSS"
        match_por = "NSS"
    elif nombre_n and nombre_n in by_nombre:
        hc_match = by_nombre[nombre_n][0]
        match_status = "MATCH_NOMBRE"
        match_por = "Nombre"
    else:
        best_ratio = 0.0
        best_rec = None
        for key in nombre_keys:
            ratio = _name_similarity(nombre_n, key)
            if ratio > best_ratio:
                best_ratio = ratio
                best_rec = by_nombre.get(key, [None])[0]
        if best_rec and best_ratio >= 0.88:
            hc_match = best_rec
            match_status = "POSIBLE_MATCH"
            match_por = f"Nombre (~{int(best_ratio * 100)}%)"

    row: dict[str, Any] = {
        "nss_sua_original": trab.get("nss_sua_original", ""),
        "nss_normalizado": nss_n,
        "nombre_sua_original": trab.get("nombre_sua_original", ""),
        "nombre_normalizado": nombre_n,
        "curp": curp_n,
        "movimiento_clave": mov,
        "sua_movimiento_clave": mov,
        "movimiento_fecha": trab.get("movimiento_fecha", ""),
        "sua_tiene_baja": trab["sua_tiene_baja"],
        "sua_es_activo_al_corte": trab["sua_es_activo_al_corte"],
        "estado_sua_al_corte": trab["estado_sua_al_corte"],
        "es_activo_al_corte_label": "Sí" if trab["sua_es_activo_al_corte"] else "No",
        "dias": trab.get("dias"),
        "sdi": trab.get("sdi"),
        "pagina_origen": trab.get("pagina_origen"),
        "match_status": match_status,
        "match_por": match_por,
        "headcount_id": None,
        "cliente_headcount": "",
        "ubicacion_headcount": "",
        "puesto_headcount": "",
        "patron_headcount": "",
        "status_operacion_headcount": "",
        "status_imss_headcount": "",
        "fecha_ingreso_headcount": "",
        "rfc_headcount": "",
        "curp_headcount": "",
        "nss_headcount": "",
        "nombre_headcount": "",
        "warnings": [],
        "info_estado": "",
    }
    if hc_match:
        row.update(
            {
                "headcount_id": hc_match.get("headcount_id"),
                "cliente_headcount": hc_match.get("cliente", ""),
                "ubicacion_headcount": hc_match.get("ubicacion", ""),
                "puesto_headcount": hc_match.get("puesto", ""),
                "patron_headcount": hc_match.get("patron", ""),
                "status_operacion_headcount": hc_match.get("status_operacion", ""),
                "status_imss_headcount": hc_match.get("status_imss", ""),
                "fecha_ingreso_headcount": hc_match.get("fecha_ingreso", ""),
                "rfc_headcount": hc_match.get("rfc_homoclave", ""),
                "curp_headcount": hc_match.get("curp", ""),
                "nss_headcount": hc_match.get("nss", ""),
                "nombre_headcount": hc_match.get("nombre_completo", ""),
            }
        )
    return row


def collect_duplicate_warnings(
    rafael: list[dict[str, Any]],
) -> dict[str, list[str]]:
    warnings_by_hc_id: dict[str, list[str]] = {}
    curp_seen: dict[str, list[str]] = {}
    nss_seen: dict[str, list[str]] = {}
    nombre_seen: dict[str, list[str]] = {}
    for rec in rafael:
        hid = str(rec.get("headcount_id") or "")
        if rec.get("curp_normalizado"):
            curp_seen.setdefault(rec["curp_normalizado"], []).append(hid)
        if rec.get("nss_normalizado"):
            nss_seen.setdefault(rec["nss_normalizado"], []).append(hid)
        nk = rec.get("nombre_hc_normalizado") or rec.get("nombre_hc_sua_like_normalizado")
        if nk:
            nombre_seen.setdefault(nk, []).append(hid)
    for ids in curp_seen.values():
        if len(ids) > 1:
            for hid in ids:
                warnings_by_hc_id.setdefault(hid, []).append("CURP_DUPLICADO_HEADCOUNT")
    for ids in nss_seen.values():
        if len(ids) > 1:
            for hid in ids:
                warnings_by_hc_id.setdefault(hid, []).append("NSS_DUPLICADO_HEADCOUNT")
    for ids in nombre_seen.values():
        if len(ids) > 1:
            for hid in ids:
                warnings_by_hc_id.setdefault(hid, []).append("NOMBRE_DUPLICADO_HEADCOUNT")
    return warnings_by_hc_id


def enrich_row_warnings(
    row: dict[str, Any],
    *,
    dias_periodo: int | None,
    dup_warnings: dict[str, list[str]],
) -> list[str]:
    warnings: list[str] = []
    tiene_baja = bool(row.get("sua_tiene_baja"))
    es_activo_sua = bool(row.get("sua_es_activo_al_corte"))
    mov = row.get("sua_movimiento_clave") or ""
    has_match = row["match_status"] in {"MATCH_CURP", "MATCH_NSS", "MATCH_NOMBRE", "POSIBLE_MATCH"}

    if row["match_status"] == "SIN_MATCH":
        if tiene_baja:
            warnings.append("SUA_BAJA_SIN_MATCH_HEADCOUNT")
        elif es_activo_sua:
            warnings.append("SUA_ACTIVO_SIN_MATCH_HEADCOUNT")
    elif not row.get("curp") and not row.get("nss_normalizado") and not row.get("nombre_normalizado"):
        warnings.append("DATOS_CLAVE_INCOMPLETOS")

    if mov in {"ALTA", "REIN"}:
        warnings.append("MOVIMIENTO_EN_SUA")
    if mov in {"P/CV", "P/IV"}:
        warnings.append("PENSIONADO_CV_IV")

    if has_match and row.get("headcount_id"):
        hid = str(row["headcount_id"])
        for w in dup_warnings.get(hid, []):
            if w not in warnings:
                warnings.append(w)
        if not normalize_text(row.get("cliente_headcount")):
            warnings.append("CLIENTE_VACIO")
        if not normalize_text(row.get("ubicacion_headcount")):
            warnings.append("UBICACION_VACIA")
        if not patron_es_rafael(row.get("patron_headcount")):
            warnings.append("PATRON_DIFERENTE")

        hc_activo = _hc_es_activo(row)
        hc_baja = _hc_es_baja(row)

        if tiene_baja and hc_activo:
            warnings.append("HEADCOUNT_ACTIVO_APARECE_BAJA_EN_SUA")
        elif tiene_baja and hc_baja:
            row["info_estado"] = "BAJA_CONCILIADA"
        elif es_activo_sua and hc_baja:
            warnings.append("HEADCOUNT_BAJA_APARECE_ACTIVO_EN_SUA")
        elif es_activo_sua:
            if not _is_status_activo_operacion(row.get("status_operacion_headcount", "")) and not hc_baja:
                warnings.append("STATUS_OPERACION_INCONSISTENTE")
            st_imss = row.get("status_imss_headcount", "")
            if (
                not _is_status_activo_imss(st_imss)
                and not _is_status_baja_imss(st_imss)
            ):
                warnings.append("STATUS_IMSS_INCONSISTENTE")

    try:
        dias_sua = int(row.get("dias") or 0)
        if dias_periodo and dias_sua > 0 and dias_sua < dias_periodo:
            warnings.append("DIAS_MENORES_PERIODO")
    except (TypeError, ValueError):
        pass

    row["warnings"] = warnings
    return warnings


def es_warning_critico(code: str) -> bool:
    return code not in _WARNINGS_NO_CRITICOS


def warning_label(code: str) -> str:
    return _WARNING_LABELS.get(code, code)


def info_estado_label(code: str) -> str:
    return _INFO_LABELS.get(code, code)
