from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from typing import Any

import pandas as pd

from modules.comparativo.headcount_service import (
    _format_fecha,
    _is_empty,
    _normalize_header,
    _normalize_name,
    _normalize_spaces,
    obtener_df_headcount,
)
from modules.headcount.matching import (
    PATRON_AUDITORIA,
    build_headcount_rafael_indexes,
    collect_duplicate_warnings,
    enrich_row_warnings,
    match_trabajador_sua,
    normalize_text,
    patron_es_rafael,
    warning_label,
)
from modules.headcount.sua_parser import SuaParseResult, parse_sua_pdf_bytes

_HEADCOUNT_COLUMNS = [
    "CLIENTE",
    "UBICACION",
    "PUESTO",
    "SUELDO DIARIO",
    "SUELDO SEMANAL",
    "PATRON",
    "FECHA DE INGRESO",
    "STATUS OPERACIÓN",
    "STATUS IMSS",
    "RFC HOMOCLAVE",
    "CP FISCAL",
    "CURP",
    "NSS",
    "APELLIDO PATERNO",
    "APELLIDO MATERNO",
    "NOMBRE",
    "NOMBRE COMPLETO",
    "GENERO",
    "FECHA DE NACIMIENTO",
    "LUGAR DE NACIMIENTO",
]


def _find_header(df: pd.DataFrame) -> tuple[int, dict[str, int]]:
    for i in range(len(df.index)):
        normalized = [_normalize_header(v) for v in df.iloc[i].tolist()]
        if "STATUS OPERACIÓN" in normalized or "STATUS OPERACION" in normalized:
            header_map = {normalized[j]: j for j in range(len(normalized))}
            return i, header_map
    raise ValueError("No se encontró encabezado STATUS OPERACIÓN en Headcount.")


def _col(header_map: dict[str, int], name: str) -> int | None:
    if name in header_map:
        return header_map[name]
    alt = name.replace("Ó", "O")
    if alt in header_map:
        return header_map[alt]
    return None


def _cell(row: list[Any], header_map: dict[str, int], name: str) -> Any:
    idx = _col(header_map, name)
    if idx is None:
        return ""
    return row[idx] if idx < len(row) else ""


def obtener_registros_headcount(
    *,
    solo_activos: bool = False,
    patron: str | None = None,
) -> list[dict[str, Any]]:
    df = obtener_df_headcount()
    header_row_idx, header_map = _find_header(df)
    registros: list[dict[str, Any]] = []
    seq = 0
    for i in range(header_row_idx + 1, len(df.index)):
        row = df.iloc[i].tolist()
        nombre_completo = _normalize_name(_cell(row, header_map, "NOMBRE COMPLETO"))
        if not nombre_completo:
            continue
        status_op = _normalize_spaces(
            str(_cell(row, header_map, "STATUS OPERACIÓN") or "").strip().upper()
        )
        status_imss = _normalize_spaces(str(_cell(row, header_map, "STATUS IMSS") or "").strip().upper())
        if solo_activos and status_op != "ALTA":
            continue
        patron_val = _normalize_spaces(str(_cell(row, header_map, "PATRON") or "").strip())
        if patron and normalize_text(patron_val) != normalize_text(patron):
            continue
        seq += 1
        sueldo_diario = _cell(row, header_map, "SUELDO DIARIO")
        sueldo_semanal = _cell(row, header_map, "SUELDO SEMANAL")
        registros.append(
            {
                "headcount_id": f"hc_{seq}",
                "cliente": _normalize_spaces(str(_cell(row, header_map, "CLIENTE") or "").strip()),
                "ubicacion": _normalize_spaces(str(_cell(row, header_map, "UBICACION") or "").strip()),
                "puesto": _normalize_spaces(str(_cell(row, header_map, "PUESTO") or "").strip()),
                "sueldo_diario": None if _is_empty(sueldo_diario) else sueldo_diario,
                "sueldo_semanal": None if _is_empty(sueldo_semanal) else sueldo_semanal,
                "patron": patron_val,
                "fecha_ingreso": _format_fecha(_cell(row, header_map, "FECHA DE INGRESO")),
                "status_operacion": status_op or "DESCONOCIDO",
                "status_imss": status_imss or "DESCONOCIDO",
                "rfc_homoclave": _normalize_spaces(str(_cell(row, header_map, "RFC HOMOCLAVE") or "").strip()),
                "cp_fiscal": _normalize_spaces(str(_cell(row, header_map, "CP FISCAL") or "").strip()),
                "curp": _normalize_spaces(str(_cell(row, header_map, "CURP") or "").strip()).upper(),
                "nss": _normalize_spaces(str(_cell(row, header_map, "NSS") or "").strip()),
                "apellido_paterno": _normalize_spaces(str(_cell(row, header_map, "APELLIDO PATERNO") or "").strip()),
                "apellido_materno": _normalize_spaces(str(_cell(row, header_map, "APELLIDO MATERNO") or "").strip()),
                "nombre": _normalize_spaces(str(_cell(row, header_map, "NOMBRE") or "").strip()),
                "nombre_completo": nombre_completo,
                "genero": _normalize_spaces(str(_cell(row, header_map, "GENERO") or "").strip()),
                "fecha_nacimiento": _format_fecha(_cell(row, header_map, "FECHA DE NACIMIENTO")),
                "lugar_nacimiento": _normalize_spaces(
                    str(_cell(row, header_map, "LUGAR DE NACIMIENTO") or "").strip()
                ),
            }
        )
    return registros


def listar_clientes_headcount(*, solo_activos: bool = False) -> list[str]:
    clientes = sorted(
        {r["cliente"] for r in obtener_registros_headcount(solo_activos=solo_activos) if r.get("cliente")},
        key=lambda x: x.casefold(),
    )
    return clientes


def listar_ubicaciones_headcount(cliente: str | None = None, *, solo_activos: bool = False) -> list[str]:
    regs = obtener_registros_headcount(solo_activos=solo_activos)
    if cliente:
        cf = cliente.strip().casefold()
        regs = [r for r in regs if str(r.get("cliente", "")).strip().casefold() == cf]
    ubicaciones = sorted(
        {r["ubicacion"] for r in regs if r.get("ubicacion")},
        key=lambda x: x.casefold(),
    )
    return ubicaciones


def resumen_cliente_view(
    registros: list[dict[str, Any]],
) -> dict[str, Any]:
    activos = sum(1 for r in registros if normalize_text(r.get("status_operacion")) == "ALTA")
    bajas = len(registros) - activos
    return {
        "total": len(registros),
        "activos": activos,
        "bajas": bajas,
        "sin_curp": sum(1 for r in registros if not str(r.get("curp") or "").strip()),
        "sin_nss": sum(1 for r in registros if not str(r.get("nss") or "").strip()),
        "sin_ubicacion": sum(1 for r in registros if not str(r.get("ubicacion") or "").strip()),
    }


def _dias_periodo_from_meta(meta: dict[str, Any]) -> int | None:
    periodo = str(meta.get("periodo_proceso") or "")
    m = re.search(r"(\d{1,2})\s*[-/]\s*(\d{1,2})", periodo)
    if not m:
        return None
    try:
        d1, d2 = int(m.group(1)), int(m.group(2))
        return max(d2 - d1 + 1, 1) if d2 >= d1 else None
    except ValueError:
        return None


def ejecutar_auditoria_sua(
    pdf_bytes: bytes,
    *,
    fecha_corte_sua: str,
    archivo_nombre: str,
) -> dict[str, Any]:
    parsed: SuaParseResult = parse_sua_pdf_bytes(pdf_bytes)
    if not parsed.es_sua:
        return {"ok": False, "fase": "validacion", "error": parsed.error or "Documento SUA no reconocido."}
    if not parsed.ok:
        return {
            "ok": False,
            "fase": "conteo",
            "error": parsed.error,
            "metadatos": parsed.metadatos,
            "diagnostico": parsed.diagnostico,
            "es_sua": True,
        }

    headcount_all = obtener_registros_headcount()
    rafael, by_curp, by_nss, by_nombre = build_headcount_rafael_indexes(headcount_all)
    nombre_keys = list(by_nombre.keys())
    dup_warnings = collect_duplicate_warnings(rafael)
    dias_periodo = _dias_periodo_from_meta(parsed.metadatos)

    detalle: list[dict[str, Any]] = []
    nss_sua_matched: set[str] = set()
    for trab in parsed.trabajadores:
        row = match_trabajador_sua(
            trab,
            by_curp=by_curp,
            by_nss=by_nss,
            by_nombre=by_nombre,
            nombre_keys=nombre_keys,
        )
        enrich_row_warnings(row, dias_periodo=dias_periodo, dup_warnings=dup_warnings)
        detalle.append(row)
        if row.get("nss_normalizado"):
            nss_sua_matched.add(row["nss_normalizado"])

    hc_activos_rafael = [
        r
        for r in rafael
        if normalize_text(r.get("status_operacion")) == "ALTA"
    ]
    hc_sin_sua: list[dict[str, Any]] = []
    for rec in hc_activos_rafael:
        nss_n = str(rec.get("nss_normalizado") or "")
        if nss_n and nss_n not in nss_sua_matched:
            hc_sin_sua.append(
                {
                    "headcount_id": rec.get("headcount_id"),
                    "cliente": rec.get("cliente", ""),
                    "ubicacion": rec.get("ubicacion", ""),
                    "nombre_completo": rec.get("nombre_completo", ""),
                    "nss": rec.get("nss", ""),
                    "curp": rec.get("curp", ""),
                    "status_operacion": rec.get("status_operacion", ""),
                    "status_imss": rec.get("status_imss", ""),
                    "warnings": ["HEADCOUNT_ACTIVO_NO_APARECE_EN_SUA"],
                }
            )

    matches_ok = sum(1 for r in detalle if r["match_status"] in {"MATCH_CURP", "MATCH_NSS", "MATCH_NOMBRE"})
    sin_match = sum(1 for r in detalle if r["match_status"] == "SIN_MATCH")
    warnings_count = sum(len(r.get("warnings") or []) for r in detalle) + len(hc_sin_sua)

    agrupado = _agrupar_detalle(detalle)
    clientes = sorted({r.get("cliente_headcount") or "SIN CLIENTE" for r in detalle if r.get("cliente_headcount")})
    ubicaciones = sorted(
        {r.get("ubicacion_headcount") or "SIN UBICACION" for r in detalle if r.get("ubicacion_headcount")}
    )

    resumen = {
        "registro_patronal_sua": parsed.metadatos.get("registro_patronal", ""),
        "razon_social_sua": parsed.metadatos.get("razon_social", ""),
        "periodo_proceso_sua": parsed.metadatos.get("periodo_proceso", ""),
        "fecha_proceso_sua": parsed.metadatos.get("fecha_proceso", ""),
        "fecha_corte_sua": fecha_corte_sua,
        "total_cotizantes": parsed.total_cotizantes,
        "trabajadores_extraidos": parsed.trabajadores_extraidos,
        "headcount_rafael_activo": len(hc_activos_rafael),
        "matches_correctos": matches_ok,
        "sin_match": sin_match,
        "warnings_criticos": warnings_count,
        "clientes_detectados": clientes,
        "ubicaciones_detectadas": ubicaciones,
        "patron_filtro": PATRON_AUDITORIA,
    }

    payload = {
        "resumen": resumen,
        "metadatos": parsed.metadatos,
        "detalle": detalle,
        "agrupado": agrupado,
        "headcount_sin_sua": hc_sin_sua,
        "warnings_catalog": {k: warning_label(k) for k in sorted({w for r in detalle for w in r.get("warnings", [])})},
    }
    file_hash = hashlib.sha256(pdf_bytes).hexdigest()
    return {
        "ok": True,
        "payload": payload,
        "resumen": resumen,
        "archivo_original_nombre": archivo_nombre,
        "hash_archivo": file_hash,
        "rfc_patronal_sua": parsed.metadatos.get("rfc_patronal", ""),
        "registro_patronal_sua": parsed.metadatos.get("registro_patronal", ""),
        "razon_social_sua": parsed.metadatos.get("razon_social", ""),
        "periodo_proceso_sua": parsed.metadatos.get("periodo_proceso", ""),
        "fecha_proceso_sua": parsed.metadatos.get("fecha_proceso", ""),
        "total_cotizantes": parsed.total_cotizantes,
        "trabajadores_extraidos": parsed.trabajadores_extraidos,
        "total_matches": matches_ok,
        "total_sin_match": sin_match,
        "total_warnings": warnings_count,
    }


def _agrupar_detalle(detalle: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for row in detalle:
        cliente = row.get("cliente_headcount") or "SIN CLIENTE"
        ubic = row.get("ubicacion_headcount") or "SIN UBICACION"
        key = (cliente, ubic)
        if key not in buckets:
            buckets[key] = {
                "cliente": cliente,
                "ubicacion": ubic,
                "total_sua": 0,
                "match_hc": 0,
                "bajas_hc": 0,
                "sin_match": 0,
                "warnings": 0,
            }
        b = buckets[key]
        b["total_sua"] += 1
        if row["match_status"] in {"MATCH_CURP", "MATCH_NSS", "MATCH_NOMBRE"}:
            b["match_hc"] += 1
        elif row["match_status"] == "SIN_MATCH":
            b["sin_match"] += 1
        if "HEADCOUNT_BAJA_APARECE_EN_SUA" in (row.get("warnings") or []):
            b["bajas_hc"] += 1
        b["warnings"] += len(row.get("warnings") or [])
    return sorted(buckets.values(), key=lambda x: (x["cliente"].casefold(), x["ubicacion"].casefold()))


def filtrar_detalle(
    detalle: list[dict[str, Any]],
    *,
    cliente: str = "",
    ubicacion: str = "",
    match_status: str = "",
    warning: str = "",
    movimiento: str = "",
    status_operacion: str = "",
    status_imss: str = "",
    solo_bajas_hc: bool = False,
    solo_sin_match: bool = False,
    busqueda: str = "",
) -> list[dict[str, Any]]:
    out = detalle
    if cliente:
        cf = cliente.strip().casefold()
        out = [r for r in out if str(r.get("cliente_headcount", "")).strip().casefold() == cf]
    if ubicacion:
        uf = ubicacion.strip().casefold()
        out = [r for r in out if str(r.get("ubicacion_headcount", "")).strip().casefold() == uf]
    if match_status:
        out = [r for r in out if r.get("match_status") == match_status]
    if warning:
        out = [r for r in out if warning in (r.get("warnings") or [])]
    if movimiento:
        mf = normalize_text(movimiento)
        out = [r for r in out if normalize_text(r.get("movimiento_clave")) == mf]
    if status_operacion:
        sf = normalize_text(status_operacion)
        out = [r for r in out if normalize_text(r.get("status_operacion_headcount")) == sf]
    if status_imss:
        sf = normalize_text(status_imss)
        out = [r for r in out if normalize_text(r.get("status_imss_headcount")) == sf]
    if solo_bajas_hc:
        out = [r for r in out if "HEADCOUNT_BAJA_APARECE_EN_SUA" in (r.get("warnings") or [])]
    if solo_sin_match:
        out = [r for r in out if r.get("match_status") == "SIN_MATCH"]
    if busqueda:
        q = normalize_text(busqueda)
        out = [
            r
            for r in out
            if q in normalize_text(r.get("nombre_sua_original"))
            or q in normalize_text(r.get("nss_sua_original"))
            or q in normalize_text(r.get("curp"))
            or q in normalize_text(r.get("nombre_headcount"))
        ]
    return out
