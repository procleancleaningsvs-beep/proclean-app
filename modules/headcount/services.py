from __future__ import annotations

import hashlib
import logging
import math
import re
import time
from datetime import date, datetime
from typing import Any

import numpy as np
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
    _hc_es_activo,
    _is_status_activo_operacion,
    _is_status_baja_operacion,
    _STATUS_ACTIVO_OPERACION,
    build_headcount_global_indexes,
    build_headcount_rafael_indexes,
    collect_duplicate_warnings,
    enrich_row_warnings,
    es_warning_critico,
    info_estado_label,
    match_trabajador_sua,
    normalize_nss,
    normalize_text,
    warning_label,
)
from modules.headcount.sua_parser import SuaParseResult, parse_sua_pdf_bytes
from modules.headcount.ui_format import (
    _months_before,
    agrupar_resumen_por_cliente,
    build_cliente_cards_for_ui,
    clientes_detectados_labels,
    display_ubicacion,
    is_empty_ui_value,
    parse_fecha_corte_auditoria,
    parse_fecha_ingreso,
)

_logger = logging.getLogger(__name__)

DESARROLLO_INF_PATRON = "DESARROLLO IN F"

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

_STATUS_HEADER_MARKERS = frozenset(
    {
        "STATUS OPERACIÓN",
        "STATUS OPERACION",
        "STATUS OPERATIVO",
        "ESTATUS OPERACION",
        "ESTATUS OPERACIÓN",
        "ESTATUS",
        "STATUS",
        "ESTADO",
    }
)

_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "CLIENTE": ("CLIENTE",),
    "UBICACION": ("UBICACION", "UBICACIÓN", "PLANTA"),
    "PUESTO": ("PUESTO",),
    "SUELDO DIARIO": ("SUELDO DIARIO",),
    "SUELDO SEMANAL": ("SUELDO SEMANAL",),
    "PATRON": ("PATRON", "PATRÓN"),
    "FECHA DE INGRESO": ("FECHA DE INGRESO", "FECHA INGRESO"),
    "STATUS OPERACIÓN": (
        "STATUS OPERACIÓN",
        "STATUS OPERACION",
        "STATUS OPERATIVO",
        "ESTATUS OPERACION",
        "ESTATUS OPERACIÓN",
        "ESTATUS",
        "STATUS",
        "ESTADO",
    ),
    "STATUS IMSS": ("STATUS IMSS", "ESTATUS IMSS"),
    "RFC HOMOCLAVE": ("RFC HOMOCLAVE", "RFC"),
    "CP FISCAL": ("CP FISCAL", "CODIGO POSTAL FISCAL", "C.P. FISCAL"),
    "CURP": ("CURP",),
    "NSS": ("NSS", "NO. SEGURO SOCIAL", "NO SEGURO SOCIAL", "NUMERO DE SEGURO SOCIAL"),
    "APELLIDO PATERNO": ("APELLIDO PATERNO",),
    "APELLIDO MATERNO": ("APELLIDO MATERNO",),
    "NOMBRE": ("NOMBRE",),
    "NOMBRE COMPLETO": ("NOMBRE COMPLETO", "NOMBRE COMPLETO ", "NOMBRE COMPLETO/NOMBRE"),
    "GENERO": ("GENERO", "GÉNERO"),
    "FECHA DE NACIMIENTO": ("FECHA DE NACIMIENTO", "FECHA NACIMIENTO"),
    "LUGAR DE NACIMIENTO": ("LUGAR DE NACIMIENTO", "LUGAR NACIMIENTO"),
}

_BODY_FIELD_MAP: tuple[tuple[str, str], ...] = (
    ("cliente", "CLIENTE"),
    ("ubicacion", "UBICACION"),
    ("puesto", "PUESTO"),
    ("sueldo_diario", "SUELDO DIARIO"),
    ("sueldo_semanal", "SUELDO SEMANAL"),
    ("patron", "PATRON"),
    ("fecha_ingreso", "FECHA DE INGRESO"),
    ("status_operacion_raw", "STATUS OPERACIÓN"),
    ("status_imss_raw", "STATUS IMSS"),
    ("rfc_homoclave", "RFC HOMOCLAVE"),
    ("cp_fiscal", "CP FISCAL"),
    ("curp", "CURP"),
    ("nss", "NSS"),
    ("apellido_paterno", "APELLIDO PATERNO"),
    ("apellido_materno", "APELLIDO MATERNO"),
    ("nombre", "NOMBRE"),
    ("nombre_completo_raw", "NOMBRE COMPLETO"),
    ("genero", "GENERO"),
    ("fecha_nacimiento", "FECHA DE NACIMIENTO"),
    ("lugar_nacimiento", "LUGAR DE NACIMIENTO"),
)


def normalize_status(value: Any) -> str:
    """Normaliza estatus operativo; valores inválidos o contaminados quedan vacíos."""
    if _is_empty(value):
        return ""
    s = _normalize_spaces(str(value).strip().upper())
    if is_empty_ui_value(s):
        return ""
    if s.startswith("NAN") or "NAN MATCH" in s or "MATCH_NAME" in s:
        return ""
    return s


def _sanitize_text_field(value: Any) -> str:
    if _is_empty(value):
        return ""
    s = _normalize_spaces(str(value).strip())
    if is_empty_ui_value(s):
        return ""
    return s


def _coerce_numeric_field(value: Any) -> int | float | None:
    if _is_empty(value):
        return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (AttributeError, ValueError):
            pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    return value


def _coerce_record_for_ui(record: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in record.items():
        if _is_empty(value):
            out[key] = None if key.startswith("sueldo_") or key.endswith("_score") else ""
            continue
        if hasattr(value, "item"):
            try:
                value = value.item()
            except (AttributeError, ValueError):
                pass
        if isinstance(value, (datetime, date)):
            out[key] = value.isoformat()
            continue
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            out[key] = None if key.startswith("sueldo_") else ""
            continue
        if isinstance(value, int) and not isinstance(value, bool):
            out[key] = int(value)
            continue
        out[key] = value
    return out


def _find_header(df: pd.DataFrame) -> tuple[int, dict[str, int]]:
    if df is None or getattr(df, "empty", True):
        return -1, {}
    scan_limit = min(len(df.index), 30)
    for i in range(scan_limit):
        row_values = df.iloc[i].to_numpy(copy=False)
        normalized = [_normalize_header(v) for v in row_values]
        header_map = {normalized[j]: j for j in range(len(normalized)) if normalized[j]}
        if header_map and any(marker in header_map for marker in _STATUS_HEADER_MARKERS):
            return i, header_map
        if "NOMBRE COMPLETO" in header_map and ("CLIENTE" in header_map or "NSS" in header_map):
            return i, header_map
    return -1, {}


def _col(header_map: dict[str, int], name: str) -> int | None:
    aliases = _COLUMN_ALIASES.get(name, (name,))
    for alias in aliases:
        if alias in header_map:
            return header_map[alias]
        alt = alias.replace("Ó", "O")
        if alt in header_map:
            return header_map[alt]
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


def _is_contaminated_name(value: Any) -> bool:
    s = _sanitize_text_field(value)
    if not s:
        return True
    upper = s.upper()
    if upper.startswith("NAN") or "MATCH_NAME" in upper or "MATCH NAME" in upper:
        return True
    return is_empty_ui_value(s)


def _resolve_nombre_completo(row: list[Any], header_map: dict[str, int]) -> str:
    direct = _sanitize_text_field(_cell(row, header_map, "NOMBRE COMPLETO"))
    if direct and not _is_contaminated_name(direct):
        normalized = _normalize_name(direct)
        if normalized and not _is_contaminated_name(normalized):
            return normalized
    parts = [
        _sanitize_text_field(_cell(row, header_map, "NOMBRE")),
        _sanitize_text_field(_cell(row, header_map, "APELLIDO PATERNO")),
        _sanitize_text_field(_cell(row, header_map, "APELLIDO MATERNO")),
    ]
    built = _normalize_name(" ".join(p for p in parts if p))
    if built and not _is_contaminated_name(built):
        return built
    return ""


def _status_display_label(status_op: str, status_imss: str) -> str:
    if status_op:
        if _is_status_activo_operacion(status_op):
            return status_op
        if _is_status_baja_operacion(status_op):
            return status_op
        return status_op
    if status_imss:
        return status_imss
    return "SIN ESTATUS"


def _vector_sanitize_text_series(values: Any) -> pd.Series:
    s = pd.Series(values, copy=False).fillna("")
    out = s.astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
    lowered = out.str.casefold()
    invalid = lowered.isin({"nan", "none", "null", "nat", "<na>"}) | (out == "")
    return out.where(~invalid, "")


def _vector_normalize_status_series(values: Any) -> pd.Series:
    s = _vector_sanitize_text_series(values).str.upper()
    invalid = (
        s.str.startswith("NAN", na=False)
        | s.str.contains("MATCH_NAME", na=False)
        | s.str.contains("NAN MATCH", na=False)
    )
    return s.where(~invalid, "")


def _vector_is_contaminated_name_series(values: pd.Series) -> pd.Series:
    upper = values.astype(str).str.upper()
    return (
        values.eq("")
        | upper.str.startswith("NAN", na=False)
        | upper.str.contains("MATCH_NAME", na=False)
        | upper.str.contains("MATCH NAME", na=False)
    )


def _extract_body_dataframe(df: pd.DataFrame, header_row_idx: int, header_map: dict[str, int]) -> pd.DataFrame:
    body = df.iloc[header_row_idx + 1 :]
    n_rows = len(body)
    if n_rows == 0:
        return pd.DataFrame()

    n_cols = body.shape[1]
    columns: dict[str, Any] = {}
    for field, logical in _BODY_FIELD_MAP:
        idx = _col(header_map, logical)
        if idx is not None and idx < n_cols:
            columns[field] = body.iloc[:, idx].to_numpy(copy=False)
        else:
            columns[field] = np.full(n_rows, "", dtype=object)
    return pd.DataFrame(columns)


def _prepare_body_dataframe(data: pd.DataFrame) -> pd.DataFrame:
    data = data.copy()
    data["status_op_norm"] = _vector_normalize_status_series(data["status_operacion_raw"])
    data["status_imss_norm"] = _vector_normalize_status_series(data["status_imss_raw"])

    direct_name = _vector_sanitize_text_series(data["nombre_completo_raw"]).map(_normalize_name)
    direct_name = direct_name.where(~_vector_is_contaminated_name_series(direct_name), "")

    built_name = (
        _vector_sanitize_text_series(data["nombre"])
        + " "
        + _vector_sanitize_text_series(data["apellido_paterno"])
        + " "
        + _vector_sanitize_text_series(data["apellido_materno"])
    ).str.strip().map(_normalize_name)
    built_name = built_name.where(~_vector_is_contaminated_name_series(built_name), "")

    data["nombre_completo"] = direct_name.where(direct_name != "", built_name)
    return data[data["nombre_completo"] != ""].reset_index(drop=True)


def _build_records_from_dataframe(data: pd.DataFrame) -> list[dict[str, Any]]:
    if data.empty:
        return []

    clientes = _vector_sanitize_text_series(data["cliente"]).tolist()
    ubicaciones = _vector_sanitize_text_series(data["ubicacion"]).tolist()
    puestos = _vector_sanitize_text_series(data["puesto"]).tolist()
    patrones = _vector_sanitize_text_series(data["patron"]).tolist()
    fechas_ingreso = data["fecha_ingreso"].map(_format_fecha).tolist()
    status_ops = data["status_op_norm"].tolist()
    status_imss = data["status_imss_norm"].tolist()
    rfcs = _vector_sanitize_text_series(data["rfc_homoclave"]).tolist()
    cps = _vector_sanitize_text_series(data["cp_fiscal"]).tolist()
    curps = _vector_sanitize_text_series(data["curp"]).str.upper().tolist()
    nss_list = _vector_sanitize_text_series(data["nss"]).tolist()
    ap_pat = _vector_sanitize_text_series(data["apellido_paterno"]).tolist()
    ap_mat = _vector_sanitize_text_series(data["apellido_materno"]).tolist()
    nombres = _vector_sanitize_text_series(data["nombre"]).tolist()
    nombres_completos = data["nombre_completo"].tolist()
    generos = _vector_sanitize_text_series(data["genero"]).tolist()
    fechas_nac = data["fecha_nacimiento"].map(_format_fecha).tolist()
    lugares_nac = _vector_sanitize_text_series(data["lugar_nacimiento"]).tolist()
    sueldos_diarios = [_coerce_numeric_field(v) for v in data["sueldo_diario"].tolist()]
    sueldos_semanales = [_coerce_numeric_field(v) for v in data["sueldo_semanal"].tolist()]

    registros: list[dict[str, Any]] = []
    for seq, row in enumerate(
        zip(
            clientes,
            ubicaciones,
            puestos,
            sueldos_diarios,
            sueldos_semanales,
            patrones,
            fechas_ingreso,
            status_ops,
            status_imss,
            rfcs,
            cps,
            curps,
            nss_list,
            ap_pat,
            ap_mat,
            nombres,
            nombres_completos,
            generos,
            fechas_nac,
            lugares_nac,
        ),
        start=1,
    ):
        status_op, status_imss_val = row[7], row[8]
        registros.append(
            _coerce_record_for_ui(
                {
                    "headcount_id": f"hc_{seq}",
                    "cliente": row[0],
                    "ubicacion": row[1],
                    "puesto": row[2],
                    "sueldo_diario": row[3],
                    "sueldo_semanal": row[4],
                    "patron": row[5],
                    "fecha_ingreso": row[6],
                    "status_operacion": _status_display_label(status_op, status_imss_val),
                    "status_imss": status_imss_val or "SIN ESTATUS",
                    "rfc_homoclave": row[9],
                    "cp_fiscal": row[10],
                    "curp": row[11],
                    "nss": row[12],
                    "apellido_paterno": row[13],
                    "apellido_materno": row[14],
                    "nombre": row[15],
                    "nombre_completo": row[16],
                    "genero": row[17],
                    "fecha_nacimiento": row[18],
                    "lugar_nacimiento": row[19],
                }
            )
        )
    return registros


def obtener_registros_headcount(
    *,
    solo_activos: bool = False,
    patron: str | None = None,
) -> list[dict[str, Any]]:
    t0 = time.perf_counter()
    try:
        df = obtener_df_headcount()
    except ValueError as exc:
        _logger.warning("Headcount no disponible al leer registros: %s", exc)
        return []
    t_load = time.perf_counter()

    if df is None or getattr(df, "empty", True):
        return []

    header_row_idx, header_map = _find_header(df)
    if header_row_idx < 0 or not header_map:
        _logger.warning("Headcount sin encabezado reconocible para conteo de personal.")
        return []

    body = _extract_body_dataframe(df, header_row_idx, header_map)
    t_extract = time.perf_counter()
    if body.empty:
        return []

    data = _prepare_body_dataframe(body)
    if solo_activos:
        data = data[data["status_op_norm"].isin(_STATUS_ACTIVO_OPERACION)]
    if patron:
        patron_objetivo = normalize_text(patron)
        patron_norm = _vector_sanitize_text_series(data["patron"]).map(normalize_text)
        data = data[patron_norm == patron_objetivo]

    registros = _build_records_from_dataframe(data.reset_index(drop=True))
    t_build = time.perf_counter()
    _logger.info(
        "headcount registros: load=%.3fs extract=%.3fs build=%.3fs total=%.3fs rows=%d solo_activos=%s",
        t_load - t0,
        t_extract - t_load,
        t_build - t_extract,
        t_build - t0,
        len(registros),
        solo_activos,
    )
    return registros


def listar_clientes_headcount(
    *,
    solo_activos: bool = False,
    regs: list[dict[str, Any]] | None = None,
) -> list[str]:
    if regs is None:
        regs = obtener_registros_headcount(solo_activos=solo_activos)
    clientes = sorted(
        {r["cliente"] for r in regs if r.get("cliente")},
        key=lambda x: x.casefold(),
    )
    return clientes


def listar_ubicaciones_headcount(
    cliente: str | None = None,
    *,
    solo_activos: bool = False,
    regs: list[dict[str, Any]] | None = None,
) -> list[str]:
    if regs is None:
        regs = obtener_registros_headcount(solo_activos=solo_activos)
    if cliente:
        cf = cliente.strip().casefold()
        regs = [r for r in regs if str(r.get("cliente", "")).strip().casefold() == cf]
    ubicaciones = sorted(
        {r["ubicacion"] for r in regs if r.get("ubicacion")},
        key=lambda x: x.casefold(),
    )
    return ubicaciones


def calc_metricas_desarrollo_inf(
    fecha_corte_sua: str,
    *,
    fecha_proceso_sua: str = "",
) -> dict[str, Any]:
    corte = parse_fecha_corte_auditoria(fecha_corte_sua, fecha_proceso_sua)
    if not corte:
        return {
            "desarrollo_inf_mas_6_meses": 0,
            "desarrollo_inf_mas_1_anio": 0,
            "fecha_corte_usada": "",
        }

    limite_6 = _months_before(corte, 6)
    limite_12 = _months_before(corte, 12)
    patron_objetivo = normalize_text(DESARROLLO_INF_PATRON)
    mas_6 = 0
    mas_1 = 0

    for rec in obtener_registros_headcount(solo_activos=False):
        if normalize_text(rec.get("patron")) != patron_objetivo:
            continue
        if normalize_text(rec.get("status_operacion")) != "ALTA":
            continue
        ingreso = parse_fecha_ingreso(rec.get("fecha_ingreso"))
        if not ingreso:
            continue
        if ingreso <= limite_6:
            mas_6 += 1
        if ingreso <= limite_12:
            mas_1 += 1

    return {
        "desarrollo_inf_mas_6_meses": mas_6,
        "desarrollo_inf_mas_1_anio": mas_1,
        "fecha_corte_usada": corte.isoformat(),
    }


def resumen_cliente_view(
    registros: list[dict[str, Any]],
) -> dict[str, Any]:
    activos = sum(1 for r in registros if _is_status_activo_operacion(normalize_status(r.get("status_operacion"))))
    bajas = sum(1 for r in registros if _is_status_baja_operacion(normalize_status(r.get("status_operacion"))))
    return {
        "total": len(registros),
        "activos": activos,
        "bajas": bajas,
        "sin_curp": sum(1 for r in registros if not _sanitize_text_field(r.get("curp"))),
        "sin_nss": sum(1 for r in registros if not _sanitize_text_field(r.get("nss"))),
        "sin_ubicacion": sum(1 for r in registros if not _sanitize_text_field(r.get("ubicacion"))),
    }


def _dedupe_detalle_sua(detalle: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in detalle:
        key = str(row.get("nss_normalizado") or "").strip() or str(row.get("curp") or "").strip()
        if not key:
            out.append(row)
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


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
    _, global_by_curp, global_by_nss, global_by_nombre = build_headcount_global_indexes(headcount_all)
    nombre_keys = list(by_nombre.keys())
    global_nombre_keys = list(global_by_nombre.keys())
    dup_warnings = collect_duplicate_warnings(rafael)
    dias_periodo = _dias_periodo_from_meta(parsed.metadatos)

    detalle: list[dict[str, Any]] = []
    nss_sua_todos: set[str] = set()
    for trab in parsed.trabajadores:
        row = match_trabajador_sua(
            trab,
            by_curp=by_curp,
            by_nss=by_nss,
            by_nombre=by_nombre,
            nombre_keys=nombre_keys,
            global_by_curp=global_by_curp,
            global_by_nss=global_by_nss,
            global_by_nombre=global_by_nombre,
            global_nombre_keys=global_nombre_keys,
        )
        enrich_row_warnings(row, dias_periodo=dias_periodo, dup_warnings=dup_warnings)
        detalle.append(row)
        if row.get("nss_normalizado"):
            nss_sua_todos.add(row["nss_normalizado"])

    detalle = _dedupe_detalle_sua(detalle)

    for idx, row in enumerate(detalle, start=1):
        row["registro_no"] = idx

    hc_activos_rafael = [r for r in rafael if _hc_es_activo({"status_operacion_headcount": r.get("status_operacion", "")})]
    hc_sin_sua: list[dict[str, Any]] = []
    for rec in hc_activos_rafael:
        nss_n = str(rec.get("nss_normalizado") or "")
        if nss_n and nss_n not in nss_sua_todos:
            hc_sin_sua.append(
                {
                    "headcount_id": rec.get("headcount_id"),
                    "cliente": rec.get("cliente", ""),
                    "ubicacion": rec.get("ubicacion", ""),
                    "nombre_completo": rec.get("nombre_completo", ""),
                    "nss": rec.get("nss", ""),
                    "nss_normalizado": normalize_nss(rec.get("nss", "")),
                    "curp": rec.get("curp", ""),
                    "status_operacion": rec.get("status_operacion", ""),
                    "status_imss": rec.get("status_imss", ""),
                    "warnings": ["HEADCOUNT_ACTIVO_NO_APARECE_EN_SUA"],
                }
            )

    sua_activos = [r for r in detalle if r.get("sua_es_activo_al_corte")]
    sua_bajas = [r for r in detalle if r.get("sua_tiene_baja")]
    matches_activos = sum(
        1
        for r in sua_activos
        if r["match_status"] in {"MATCH_CURP", "MATCH_NSS", "MATCH_NOMBRE"}
    )
    sua_activos_sin_match = sum(
        1 for r in sua_activos if "SUA_ACTIVO_SIN_MATCH_HEADCOUNT" in (r.get("warnings") or [])
    )
    hc_activos_baja_en_sua = sum(
        1 for r in detalle if "HEADCOUNT_ACTIVO_APARECE_BAJA_EN_SUA" in (r.get("warnings") or [])
    )
    bajas_conciliadas = sum(1 for r in detalle if r.get("info_estado") == "BAJA_CONCILIADA")
    warnings_criticos = sum(
        len([w for w in (r.get("warnings") or []) if es_warning_critico(w)]) for r in detalle
    ) + len(hc_sin_sua)

    agrupado = _agrupar_detalle(detalle)
    resumen_clientes, sin_cliente_card, otro_patron_card = build_cliente_cards_for_ui(detalle)
    clientes_opts = clientes_detectados_labels(detalle)
    metricas_desarrollo_inf = calc_metricas_desarrollo_inf(
        fecha_corte_sua,
        fecha_proceso_sua=parsed.metadatos.get("fecha_proceso", "") or "",
    )
    ubicaciones = sorted(
        {
            str(r.get("ubicacion_headcount") or "").strip()
            for r in detalle
            if str(r.get("ubicacion_headcount") or "").strip()
        },
        key=lambda x: display_ubicacion(x).casefold(),
    )

    total_activos_sua = parsed.total_sua_activos_al_corte
    total_bajas_sua = parsed.total_sua_bajas_periodo
    diff_activa = total_activos_sua - len(hc_activos_rafael)

    resumen = {
        "registro_patronal_sua": parsed.metadatos.get("registro_patronal", ""),
        "razon_social_sua": parsed.metadatos.get("razon_social", ""),
        "periodo_proceso_sua": parsed.metadatos.get("periodo_proceso", ""),
        "fecha_proceso_sua": parsed.metadatos.get("fecha_proceso", ""),
        "fecha_corte_sua": fecha_corte_sua,
        "total_cotizantes_sua": parsed.total_cotizantes,
        "total_cotizantes": parsed.total_cotizantes,
        "trabajadores_extraidos": parsed.trabajadores_extraidos,
        "total_sua_activos_al_corte": total_activos_sua,
        "total_sua_bajas_periodo": total_bajas_sua,
        "headcount_rafael_activo": len(hc_activos_rafael),
        "sua_activos_sin_match_headcount": sua_activos_sin_match,
        "headcount_activos_no_en_sua": len(hc_sin_sua),
        "headcount_activos_con_baja_en_sua": hc_activos_baja_en_sua,
        "bajas_conciliadas": bajas_conciliadas,
        "matches_correctos": matches_activos,
        "matches_activos": matches_activos,
        "diferencia_activa_sua_vs_headcount": diff_activa,
        "warnings_criticos": warnings_criticos,
        "clientes_detectados": [c["key"] for c in clientes_opts],
        "clientes_detectados_opts": clientes_opts,
        "ubicaciones_detectadas": ubicaciones,
        "patron_filtro": PATRON_AUDITORIA,
        "desarrollo_inf_mas_6_meses": metricas_desarrollo_inf.get("desarrollo_inf_mas_6_meses", 0),
        "desarrollo_inf_mas_1_anio": metricas_desarrollo_inf.get("desarrollo_inf_mas_1_anio", 0),
    }

    all_warning_codes = {w for r in detalle for w in r.get("warnings", [])}
    all_warning_codes.update(w for r in hc_sin_sua for w in r.get("warnings", []))
    all_warning_codes.discard("STATUS_IMSS_INCONSISTENTE")

    payload = {
        "resumen": resumen,
        "metadatos": parsed.metadatos,
        "detalle": detalle,
        "agrupado": agrupado,
        "resumen_clientes": resumen_clientes,
        "sin_cliente_card": sin_cliente_card,
        "otro_patron_card": otro_patron_card,
        "headcount_sin_sua": hc_sin_sua,
        "sua_activos": sua_activos,
        "sua_bajas": sua_bajas,
        "warnings_catalog": {k: warning_label(k) for k in sorted(all_warning_codes)},
        "info_catalog": {k: info_estado_label(k) for k in sorted({r.get("info_estado") for r in detalle if r.get("info_estado")})},
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
        "total_matches": matches_activos,
        "total_sin_match": sua_activos_sin_match,
        "total_warnings": warnings_criticos,
    }


def _row_warnings_list(row: dict[str, Any]) -> list[str]:
    raw = row.get("warnings")
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(w).strip() for w in raw if w and str(w).strip()]
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                import json

                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(w).strip() for w in parsed if w and str(w).strip()]
            except json.JSONDecodeError:
                pass
        return [p.strip() for p in re.split(r"[,;]+", s) if p.strip()]
    return []


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
                "activos_sua": 0,
                "bajas_sua": 0,
                "match_activos": 0,
                "activos_sin_match": 0,
                "bajas_conciliadas": 0,
                "warnings": 0,
            }
        b = buckets[key]
        if row.get("sua_es_activo_al_corte"):
            b["activos_sua"] += 1
            if row["match_status"] in {"MATCH_CURP", "MATCH_NSS", "MATCH_NOMBRE"}:
                b["match_activos"] += 1
            if "SUA_ACTIVO_SIN_MATCH_HEADCOUNT" in (row.get("warnings") or []):
                b["activos_sin_match"] += 1
        else:
            b["bajas_sua"] += 1
        if row.get("info_estado") == "BAJA_CONCILIADA":
            b["bajas_conciliadas"] += 1
        b["warnings"] += len([w for w in (row.get("warnings") or []) if es_warning_critico(w)])
    return sorted(buckets.values(), key=lambda x: (x["cliente"].casefold(), x["ubicacion"].casefold()))


def filtrar_detalle(
    detalle: list[dict[str, Any]],
    *,
    cliente: str = "",
    ubicacion: str = "",
    ubicacion_provided: bool = False,
    match_status: str = "",
    warning: str = "",
    movimiento: str = "",
    status_operacion: str = "",
    status_imss: str = "",
    estado_sua: str = "",
    conciliacion: str = "",
    busqueda: str = "",
) -> list[dict[str, Any]]:
    out = detalle
    if cliente:
        cf = cliente.strip().casefold()
        out = [r for r in out if str(r.get("cliente_headcount", "")).strip().casefold() == cf]
    if ubicacion_provided:
        uf = ubicacion.strip().casefold()
        if uf:
            out = [r for r in out if str(r.get("ubicacion_headcount", "")).strip().casefold() == uf]
        else:
            out = [r for r in out if not str(r.get("ubicacion_headcount") or "").strip()]
    if match_status:
        out = [r for r in out if r.get("match_status") == match_status]
    if warning:
        wf = warning.strip()
        out = [r for r in out if wf in _row_warnings_list(r)]
    if movimiento:
        from modules.headcount.matching import normalize_movimiento_clave

        mf = normalize_movimiento_clave(movimiento)
        out = [r for r in out if r.get("sua_movimiento_clave") == mf]
    if status_operacion:
        sf = normalize_text(status_operacion)
        out = [r for r in out if normalize_text(r.get("status_operacion_headcount")) == sf]
    if status_imss:
        sf = normalize_text(status_imss)
        out = [r for r in out if normalize_text(r.get("status_imss_headcount")) == sf]
    if estado_sua == "activo":
        out = [r for r in out if r.get("sua_es_activo_al_corte")]
    elif estado_sua == "baja":
        out = [r for r in out if r.get("sua_tiene_baja")]
    if conciliacion == "activos_sin_match":
        out = [r for r in out if "SUA_ACTIVO_SIN_MATCH_HEADCOUNT" in (r.get("warnings") or [])]
    elif conciliacion == "hc_activo_no_sua":
        out = []
    elif conciliacion == "hc_activo_baja_sua":
        out = [r for r in out if "HEADCOUNT_ACTIVO_APARECE_BAJA_EN_SUA" in (r.get("warnings") or [])]
    elif conciliacion == "hc_baja_activo_sua":
        out = [r for r in out if "HEADCOUNT_BAJA_APARECE_ACTIVO_EN_SUA" in (r.get("warnings") or [])]
    elif conciliacion == "baja_conciliada":
        out = [r for r in out if r.get("info_estado") == "BAJA_CONCILIADA"]
    if busqueda:
        q = normalize_text(busqueda)
        q_digits = normalize_nss(busqueda)
        out = [
            r
            for r in out
            if q in normalize_text(r.get("nombre_sua_original"))
            or q in normalize_text(r.get("nss_sua_original"))
            or (q_digits and q_digits in str(r.get("nss_normalizado") or ""))
            or q in normalize_text(r.get("curp"))
            or q in normalize_text(r.get("nombre_headcount"))
        ]
    return out
