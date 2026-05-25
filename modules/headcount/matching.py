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
    "STATUS_OPERACION_INCONSISTENTE": "STATUS OPERACIÓN no activo en Headcount",
    "PATRON_DIFERENTE": (
        "El trabajador aparece en SUA RAFAEL, pero en Headcount está registrado bajo otro patrón."
    ),
    "MATCH_INCONSISTENTE_REVISAR_LOGICA": (
        "SIN_MATCH con CURP/NSS presente en Headcount; revisar lógica de matching."
    ),
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

_STATUS_ACTIVO_OPERACION = frozenset({"ALTA", "ACTIVO", "ACTIVA", "VIGENTE", "OPERATIVO", "OPERATIVA"})
_STATUS_BAJA_OPERACION = frozenset({"BAJA", "INACTIVO", "INACTIVA", "SUSPENDIDO", "SUSPENDIDA"})
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


def _hc_es_activo(row: dict[str, Any]) -> bool:
    st_op = row.get("status_operacion_headcount", "")
    if _is_status_baja_operacion(st_op):
        return False
    return _is_status_activo_operacion(st_op)


def _hc_es_baja(row: dict[str, Any]) -> bool:
    return _is_status_baja_operacion(row.get("status_operacion_headcount", ""))


def patron_es_rafael(patron: Any) -> bool:
    return patron_matches_auditoria(patron)


def _prepare_headcount_record(rec: dict[str, Any], seq: int) -> dict[str, Any]:
    out = dict(rec)
    out["headcount_id"] = out.get("headcount_id") or f"hc_{seq}"
    out["nombre_hc_sua_like"] = nombre_hc_sua_like(out)
    out["nombre_hc_normalizado"] = normalize_text(out.get("nombre_completo"))
    out["nombre_hc_sua_like_normalizado"] = normalize_text(out["nombre_hc_sua_like"])
    out["curp_normalizado"] = normalize_curp(out.get("curp"))
    out["nss_normalizado"] = normalize_nss(out.get("nss"))
    return out


def build_headcount_indexes(
    registros: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict]], dict[str, list[dict]], dict[str, list[dict]]]:
    prepared: list[dict[str, Any]] = []
    by_curp: dict[str, list[dict]] = {}
    by_nss: dict[str, list[dict]] = {}
    by_nombre: dict[str, list[dict]] = {}
    for i, rec in enumerate(registros):
        row = _prepare_headcount_record(rec, i)
        prepared.append(row)
        if row["curp_normalizado"]:
            by_curp.setdefault(row["curp_normalizado"], []).append(row)
        if row["nss_normalizado"]:
            by_nss.setdefault(row["nss_normalizado"], []).append(row)
        nombre_key = row["nombre_hc_normalizado"] or row["nombre_hc_sua_like_normalizado"]
        if nombre_key:
            by_nombre.setdefault(nombre_key, []).append(row)
    return prepared, by_curp, by_nss, by_nombre


def build_headcount_rafael_indexes(
    registros: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict]], dict[str, list[dict]], dict[str, list[dict]]]:
    rafael_regs = [r for r in registros if patron_es_rafael(r.get("patron"))]
    prepared, by_curp, by_nss, by_nombre = build_headcount_indexes(rafael_regs)
    return prepared, by_curp, by_nss, by_nombre


def build_headcount_global_indexes(
    registros: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict]], dict[str, list[dict]], dict[str, list[dict]]]:
    return build_headcount_indexes(registros)


def _lookup_in_indexes(
    curp_n: str,
    nss_n: str,
    nombre_n: str,
    *,
    by_curp: dict[str, list[dict]],
    by_nss: dict[str, list[dict]],
    by_nombre: dict[str, list[dict]],
    nombre_keys: list[str],
    fuzzy_name: bool,
) -> tuple[dict[str, Any] | None, str]:
    if curp_n and curp_n in by_curp:
        return by_curp[curp_n][0], "CURP"
    if nss_n and nss_n in by_nss:
        return by_nss[nss_n][0], "NSS"
    if nombre_n and nombre_n in by_nombre:
        return by_nombre[nombre_n][0], "NOMBRE"
    if fuzzy_name and nombre_n:
        best_ratio = 0.0
        best_rec = None
        for key in nombre_keys:
            ratio = _name_similarity(nombre_n, key)
            if ratio > best_ratio:
                best_ratio = ratio
                best_rec = by_nombre.get(key, [None])[0]
        if best_rec and best_ratio >= 0.88:
            return best_rec, "NOMBRE"
    return None, "NONE"


def _apply_hc_match_to_row(row: dict[str, Any], hc_match: dict[str, Any]) -> None:
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


def _build_matching_debug(
    *,
    curp_n: str,
    nss_n: str,
    nombre_n: str,
    encontrado_rafael: bool,
    metodo_rafael: str,
    encontrado_global: bool,
    metodo_global: str,
    hc_global: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "curp_sua_normalizado": curp_n,
        "nss_sua_normalizado": nss_n,
        "nombre_sua_normalizado": nombre_n,
        "busco_en_patron_objetivo": PATRON_AUDITORIA,
        "encontrado_en_patron_objetivo": encontrado_rafael,
        "metodo_match_patron_objetivo": metodo_rafael,
        "encontrado_en_headcount_global": encontrado_global,
        "metodo_match_global": metodo_global,
        "patron_global_encontrado": (hc_global or {}).get("patron", ""),
        "cliente_global_encontrado": (hc_global or {}).get("cliente", ""),
        "ubicacion_global_encontrada": (hc_global or {}).get("ubicacion", ""),
        "status_operacion_global": (hc_global or {}).get("status_operacion", ""),
    }


def _exists_in_global(curp_n: str, nss_n: str, global_by_curp: dict, global_by_nss: dict) -> bool:
    return bool((curp_n and curp_n in global_by_curp) or (nss_n and nss_n in global_by_nss))


def match_trabajador_sua(
    trabajador: dict[str, Any],
    *,
    by_curp: dict[str, list[dict]],
    by_nss: dict[str, list[dict]],
    by_nombre: dict[str, list[dict]],
    nombre_keys: list[str],
    global_by_curp: dict[str, list[dict]] | None = None,
    global_by_nss: dict[str, list[dict]] | None = None,
    global_by_nombre: dict[str, list[dict]] | None = None,
    global_nombre_keys: list[str] | None = None,
) -> dict[str, Any]:
    trab = enrich_sua_worker_fields(trabajador)
    curp_n = normalize_curp(trab.get("curp"))
    nss_n = normalize_nss(trab.get("nss_normalizado") or trab.get("nss_sua_original"))
    nombre_n = normalize_text(trab.get("nombre_normalizado") or trab.get("nombre_sua_original"))
    mov = trab["sua_movimiento_clave"]

    g_curp = global_by_curp if global_by_curp is not None else by_curp
    g_nss = global_by_nss if global_by_nss is not None else by_nss
    g_nombre = global_by_nombre if global_by_nombre is not None else by_nombre
    g_nombre_keys = global_nombre_keys if global_nombre_keys is not None else nombre_keys

    hc_match: dict[str, Any] | None = None
    hc_global: dict[str, Any] | None = None
    match_status = "SIN_MATCH"
    match_por = ""
    metodo_rafael = "NONE"
    metodo_global = "NONE"

    hc_match, metodo_rafael = _lookup_in_indexes(
        curp_n,
        nss_n,
        nombre_n,
        by_curp=by_curp,
        by_nss=by_nss,
        by_nombre=by_nombre,
        nombre_keys=nombre_keys,
        fuzzy_name=True,
    )
    if hc_match:
        if metodo_rafael == "CURP":
            match_status, match_por = "MATCH_CURP", "CURP"
        elif metodo_rafael == "NSS":
            match_status, match_por = "MATCH_NSS", "NSS"
        elif metodo_rafael == "NOMBRE":
            if nombre_n in by_nombre:
                match_status, match_por = "MATCH_NOMBRE", "Nombre"
            else:
                match_status, match_por = "POSIBLE_MATCH", "Nombre (~88%+)"
    else:
        hc_global, metodo_global = _lookup_in_indexes(
            curp_n,
            nss_n,
            nombre_n,
            by_curp=g_curp,
            by_nss=g_nss,
            by_nombre=g_nombre,
            nombre_keys=g_nombre_keys,
            fuzzy_name=False,
        )
        if hc_global:
            hc_match = hc_global
            match_status = "MATCH_OTRO_PATRON"
            if metodo_global == "CURP":
                match_por = "CURP (otro patrón)"
            elif metodo_global == "NSS":
                match_por = "NSS (otro patrón)"
            else:
                match_por = "Nombre (otro patrón)"

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
        "matching_debug": {},
    }
    if hc_match:
        _apply_hc_match_to_row(row, hc_match)

    encontrado_rafael = bool(metodo_rafael != "NONE")
    encontrado_global = bool(metodo_global != "NONE")
    row["matching_debug"] = _build_matching_debug(
        curp_n=curp_n,
        nss_n=nss_n,
        nombre_n=nombre_n,
        encontrado_rafael=encontrado_rafael,
        metodo_rafael=metodo_rafael,
        encontrado_global=encontrado_global,
        metodo_global=metodo_global,
        hc_global=hc_global if match_status == "MATCH_OTRO_PATRON" else hc_match,
    )

    if match_status == "SIN_MATCH" and _exists_in_global(curp_n, nss_n, g_curp, g_nss):
        if curp_n and curp_n in g_curp:
            hc_fix = g_curp[curp_n][0]
            metodo_fix = "CURP"
        else:
            hc_fix = g_nss[nss_n][0]
            metodo_fix = "NSS"
        if patron_es_rafael(hc_fix.get("patron")):
            _apply_hc_match_to_row(row, hc_fix)
            if metodo_fix == "CURP":
                row["match_status"], row["match_por"] = "MATCH_CURP", "CURP"
            else:
                row["match_status"], row["match_por"] = "MATCH_NSS", "NSS"
            row["matching_debug"]["corregido_por_seguridad"] = True
        else:
            _apply_hc_match_to_row(row, hc_fix)
            row["match_status"] = "MATCH_OTRO_PATRON"
            row["match_por"] = f"{metodo_fix} (otro patrón)"
            row["matching_debug"]["corregido_por_seguridad"] = True
            if "MATCH_INCONSISTENTE_REVISAR_LOGICA" not in row["warnings"]:
                row["warnings"].append("MATCH_INCONSISTENTE_REVISAR_LOGICA")

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
    has_match = row["match_status"] in {
        "MATCH_CURP",
        "MATCH_NSS",
        "MATCH_NOMBRE",
        "MATCH_OTRO_PATRON",
        "POSIBLE_MATCH",
    }

    if row["match_status"] == "MATCH_OTRO_PATRON":
        if "PATRON_DIFERENTE" not in warnings:
            warnings.append("PATRON_DIFERENTE")

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
        if (
            row["match_status"] != "MATCH_OTRO_PATRON"
            and not patron_es_rafael(row.get("patron_headcount"))
        ):
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
