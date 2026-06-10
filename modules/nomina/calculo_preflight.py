"""Prevalidacion para flujo de entrada de calculo preliminar de nomina."""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from typing import Any

from modules.nomina.calc_service import (
    _match_parametro,
    _norm_key,
    _norm_name_param,
    _param_index,
    build_calculo_payload,
)
from modules.nomina.config import (
    MSG_HEADCOUNT_UNAVAILABLE_CALCULO,
    WARN_BLOCK_CALC_MISSING_SALARY,
    WARN_BLOCK_CALC_MISSING_VALOR_HE,
    WARN_SAME_NSS_MULTIPLE_CLIENTS,
)
from modules.nomina.db import (
    get_asistencia_import,
    get_empleado_parametro,
    get_latest_vacaciones_import_id,
    list_empleado_parametros,
    list_vacaciones_empleados,
    upsert_empleado_parametros,
)
from modules.nomina.validators import _norm_header
from modules.nomina.vacaciones_util import MATCH_OK, normalize_match_status_legacy

PREFLIGHT_VERSION = "2"
PARAM_ROWS_LIMIT = 5000  # Debe coincidir con calc_service.build_calculo_payload

MSG_PARAM_SAVE_OK = "Parametros guardados correctamente. Preflight actualizado."
MSG_PARAM_SAVE_FAIL = "No se pudieron guardar los parametros. Revisa los datos capturados."
MSG_PARAM_SAVE_AMBIGUOUS = (
    "El salario se guardo, pero el calculo sigue encontrando un registro ambiguo en Parametros. "
    "Revisa duplicados o vuelve a seleccionar el registro correcto."
)
CODE_PARAMETROS_DUPLICADOS = "parametros_duplicados_ambiguos"

CALCULO_DEFAULT_POLICY = {
    "domingo_opcion": "proporcional",
    "dias_tarifa_isr": 7,
    "dias_tarifa_subs": 7,
    "es_fin_de_mes": False,
    "permitir_negativo_isr": False,
}

NIVEL_CRITICAL = "critical"
NIVEL_REVIEW = "review"
NIVEL_INFO = "info"

_WARNING_CATALOG: dict[str, dict[str, str]] = {
    "prima_vacacional_sin_datos_vacaciones": {
        "detalle": "Prima vacacional solicitada sin datos confirmados en Vacaciones.",
        "origen": "vacaciones",
        "impacto": "Puede afectar el pago de prima vacacional",
        "accion": "Revisar modulo Vacaciones o asistencia antes de generar nomina",
        "nivel_default": NIVEL_REVIEW,
    },
    "prima_vacacional_ya_cubierta": {
        "detalle": "Asistencia sugiere prima vacacional ya cubierta.",
        "origen": "vacaciones",
        "impacto": "Asistencia solicita prima pero Vacaciones indica cobertura previa",
        "accion": "Validar saldo y pagos en Vacaciones",
        "nivel_default": NIVEL_REVIEW,
    },
    "vacaciones_laboradas_saldo_insuficiente": {
        "detalle": "Vacaciones laboradas superan el saldo disponible.",
        "origen": "vacaciones",
        "impacto": "Puede afectar dias de vacaciones laboradas",
        "accion": "Revisar saldo en modulo Vacaciones",
        "nivel_default": NIVEL_REVIEW,
    },
    "bono_manual_no_numerico_revisar": {
        "detalle": "Bono manual con formato no numerico estandar.",
        "origen": "asistencia",
        "impacto": "El bono manual no se interpretara automaticamente",
        "accion": "Corregir bono en asistencia o revisar manualmente en borrador",
        "nivel_default": NIVEL_REVIEW,
    },
    "deduccion_manual_no_numerica_revisar": {
        "detalle": "Deduccion manual con formato no numerico estandar.",
        "origen": "asistencia",
        "impacto": "La deduccion manual no se interpretara automaticamente",
        "accion": "Corregir deduccion en asistencia o revisar manualmente en borrador",
        "nivel_default": NIVEL_REVIEW,
    },
    "review_no_confident_headcount_match": {
        "detalle": "Match de asistencia con Headcount no es confiable.",
        "origen": "headcount",
        "impacto": "Match de asistencia con Headcount no es confiable",
        "accion": "Revisar identificacion del trabajador en asistencia o Headcount",
        "nivel_default": NIVEL_REVIEW,
    },
    "nuevo_ingreso_ni_no_computa": {
        "detalle": "Dia NI detectado: no computa en dias de pago.",
        "origen": "asistencia",
        "impacto": "Dia NI no computa en dias de pago",
        "accion": "Verificar registro de nuevo ingreso",
        "nivel_default": NIVEL_INFO,
    },
    "retardo_r_computa_posible_deduccion_manual": {
        "detalle": "Retardo (R) detectado en asistencia.",
        "origen": "asistencia",
        "impacto": "Retardo computa; puede requerir deduccion manual",
        "accion": "Validar si aplica deduccion",
        "nivel_default": NIVEL_INFO,
    },
    WARN_BLOCK_CALC_MISSING_SALARY: {
        "detalle": "Falta salario operativo en Parametros de Nomina.",
        "origen": "parametros",
        "impacto": "No se pueden calcular percepciones sin salario operativo.",
        "accion": "Captura el salario operativo semanal y guarda en Parametros.",
        "nivel_default": NIVEL_CRITICAL,
    },
    WARN_BLOCK_CALC_MISSING_VALOR_HE: {
        "detalle": "Falta Valor x HE en Parametros de Nomina.",
        "origen": "parametros",
        "impacto": "Horas extra detectadas sin valor operativo para calcular HE.",
        "accion": "Captura Valor x HE y guarda en Parametros.",
        "nivel_default": NIVEL_CRITICAL,
    },
    "infonavit_sin_registro_para_nss": {
        "detalle": "Sin registro INFONAVIT para NSS.",
        "origen": "infonavit",
        "impacto": "",
        "accion": "",
        "nivel_default": NIVEL_INFO,
    },
}

_PREFLIGHT_SKIP_WARNING_CODES = frozenset({"infonavit_sin_registro_para_nss"})

_NIVEL_ORDER = {NIVEL_CRITICAL: 0, NIVEL_REVIEW: 1, NIVEL_INFO: 2}


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _rows_validas(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if not (row.get("errors") or [])]


def _trabajador_key(row: dict[str, Any]) -> str | None:
    nss = str(row.get("nss") or "").strip()
    if nss:
        return f"nss:{nss}"
    nombre = " ".join(str(row.get("nombre_empleado") or "").strip().lower().split())
    cliente = " ".join(str(row.get("cliente") or "").strip().lower().split())
    if not nombre:
        return None
    return f"nombre:{nombre}|cliente:{cliente}"


def _unique_ordered(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item = str(raw or "").strip()
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _norm_nss(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _norm_name(value: Any) -> str:
    s = " ".join(str(value or "").replace("\u00a0", " ").upper().split()).strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return " ".join(s.split()).strip()


def _vacaciones_row_name(vac_row: dict[str, Any]) -> str:
    for key in ("nombre_normalizado", "excel_nombre_original", "nombre_historico", "nombre_headcount"):
        nm = _norm_name(vac_row.get(key))
        if nm:
            return nm
    return ""


def _make_observacion(
    *,
    nivel: str,
    origen: str,
    trabajador: str,
    detalle: str,
    impacto: str,
    accion_sugerida: str,
    fila: int | None = None,
    codigo: str | None = None,
    accion_parametros: dict[str, Any] | None = None,
    trabajador_datos: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "nivel": nivel,
        "origen": origen,
        "trabajador": trabajador,
        "trabajador_datos": trabajador_datos or {},
        "fila": fila,
        "detalle": detalle,
        "impacto": impacto,
        "accion_sugerida": accion_sugerida,
        "codigo": codigo or "",
        "accion_parametros": accion_parametros,
    }


def _observacion_key(obs: dict[str, Any]) -> str:
    return "|".join(
        [
            str(obs.get("nivel") or ""),
            str(obs.get("codigo") or ""),
            str(obs.get("trabajador") or ""),
            str(obs.get("fila") or ""),
            str(obs.get("detalle") or ""),
        ]
    )


def _append_observacion(observaciones: list[dict[str, Any]], seen: set[str], obs: dict[str, Any]) -> None:
    key = _observacion_key(obs)
    if key in seen:
        return
    seen.add(key)
    observaciones.append(obs)


def _build_vacaciones_index(
    db_path: str,
) -> tuple[int | None, dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    vac_imp = get_latest_vacaciones_import_id(db_path)
    by_nss: dict[str, dict[str, Any]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    if not vac_imp:
        return None, by_nss, by_name
    for vr in list_vacaciones_empleados(db_path, import_id=vac_imp, limit=8000):
        nss = _norm_nss(vr.get("nss"))
        if nss and nss not in by_nss:
            by_nss[nss] = vr
        nm = _vacaciones_row_name(vr)
        if nm:
            by_name.setdefault(nm, []).append(vr)
    return vac_imp, by_nss, by_name


def _match_vacaciones_for_asistencia(
    row: dict[str, Any],
    *,
    by_nss: dict[str, dict[str, Any]],
    by_name: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any] | None, str, bool]:
    nss = _norm_nss(row.get("nss"))
    if nss and nss in by_nss:
        vac_row = by_nss[nss]
        ms = normalize_match_status_legacy(vac_row.get("match_status"))
        return vac_row, "nss", ms == MATCH_OK

    nm = _norm_name(row.get("nombre_empleado"))
    candidates = by_name.get(nm) or []
    if len(candidates) == 1:
        vac_row = candidates[0]
        ms = normalize_match_status_legacy(vac_row.get("match_status"))
        return vac_row, "nombre", ms == MATCH_OK
    if len(candidates) > 1:
        return None, "nombre_ambiguo", False
    return None, "sin_match", False


def _vacaciones_prima_estatus(vac_row: dict[str, Any]) -> tuple[str, str]:
    prima_2026 = bool(int(vac_row.get("prima_2026_pagada") or 0))
    saldo = _float_or_zero(vac_row.get("saldo_calculado") or vac_row.get("dias_restantes_calculado"))
    dias_pag = _float_or_zero(vac_row.get("dias_pagados"))
    if prima_2026:
        return "pagada", "Prima vacacional 2026 marcada como pagada en Vacaciones"
    if saldo > 0:
        return "pendiente", f"Saldo pendiente en Vacaciones: {saldo:.2f} dias"
    if dias_pag > 0:
        return "parcial", f"Dias pagados registrados en Vacaciones: {dias_pag:.2f}"
    return "sin_pendiente", "Vacaciones no indica prima pendiente"


def _asistencia_prima_solicita(raw_row: dict[str, Any] | None) -> bool:
    if not raw_row:
        return False
    return _norm_header(str(raw_row.get("prima_vacacional") or "")) == "SOLICITA"


def _clean_display(value: Any) -> str:
    s = str(value or "").strip()
    if not s or s.lower() in {"none", "null", "nan", "n/a", "—"}:
        return ""
    return s


def _build_trabajador_datos(
    raw_row: dict[str, Any],
    payload_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    src = payload_row if payload_row else raw_row
    nombre = _clean_display(src.get("nombre_empleado") or raw_row.get("nombre_empleado")) or "Trabajador sin nombre"
    return {
        "nombre": nombre,
        "cliente": _clean_display(raw_row.get("cliente") or src.get("cliente")),
        "planta": _clean_display(raw_row.get("planta") or src.get("planta")),
        "puesto": _clean_display(raw_row.get("puesto") or src.get("puesto")),
        "fila": int(raw_row.get("row_number") or src.get("asistencia_row_id") or raw_row.get("id") or 0) or None,
        "nombre_normalizado": _norm_name(nombre),
        "nss": _norm_nss(raw_row.get("nss") or src.get("nss")),
        "numero_empleado": _clean_display(src.get("numero_empleado")),
    }


def _trabajador_label(row: dict[str, Any], payload_row: dict[str, Any] | None = None) -> str:
    return _build_trabajador_datos(row, payload_row)["nombre"]


def _cliente_planta_label(row: dict[str, Any], vac_row: dict[str, Any] | None = None) -> str:
    cliente = str(row.get("cliente") or (vac_row or {}).get("cliente") or "").strip()
    planta = str(row.get("planta") or (vac_row or {}).get("planta_headcount") or (vac_row or {}).get("ubicacion_headcount") or "").strip()
    if cliente and planta:
        return f"{cliente} / {planta}"
    return cliente or planta or "—"


def _humanize_unknown_code(code: str) -> str:
    return "Validacion interna requiere revision antes del calculo."


def _should_skip_preflight_warning(code: str) -> bool:
    base = str(code or "").strip()
    if not base:
        return True
    if base in _PREFLIGHT_SKIP_WARNING_CODES:
        return True
    return False


def _infonavit_conflict_message(code: str) -> tuple[str, str, str] | None:
    if not code.startswith("infonavit_no_aplicado_automaticamente:"):
        return None
    st = code.split(":", 1)[-1].strip()
    if st == "suspendido":
        return (
            NIVEL_INFO,
            "Credito INFONAVIT suspendido; no se aplicara descuento automatico.",
            "Validar estatus en modulo INFONAVIT si esperabas descuento activo.",
        )
    if st.startswith("match_no_confiable"):
        return (
            NIVEL_REVIEW,
            "Credito INFONAVIT detectado pero el match no es confiable.",
            "Revisar registro INFONAVIT y conciliacion del trabajador.",
        )
    if st.startswith("estatus_no_activo"):
        return (
            NIVEL_REVIEW,
            f"Credito INFONAVIT con estatus no activo ({st.split(':', 1)[-1]}).",
            "Revisar aviso/credito en modulo INFONAVIT.",
        )
    if st == "umi_no_disponible":
        return (
            NIVEL_REVIEW,
            "Credito INFONAVIT detectado pero falta UMI para calcular descuento.",
            "Verificar parametros fiscales del periodo o registro INFONAVIT.",
        )
    if st == "sin_monto_aplicable":
        return (
            NIVEL_REVIEW,
            "Credito INFONAVIT activo sin monto aplicable en el registro importado.",
            "Completar datos del credito en modulo INFONAVIT.",
        )
    return (
        NIVEL_REVIEW,
        "Credito INFONAVIT detectado pero no se pudo aplicar automaticamente.",
        "Revisar registro INFONAVIT del trabajador.",
    )


def _param_action_payload(
    payload_row: dict[str, Any],
    raw_row: dict[str, Any],
    *,
    needs_salario: bool = False,
    needs_valor_he: bool = False,
    calc_match: dict[str, Any] | None = None,
) -> dict[str, Any]:
    datos = _build_trabajador_datos(raw_row, payload_row)
    calc_id = int((calc_match or {}).get("id") or payload_row.get("parametro_empleado_id") or 0) or None
    return {
        "asistencia_row_id": int(payload_row.get("asistencia_row_id") or raw_row.get("id") or 0),
        "parametro_empleado_id": calc_id,
        "calc_parametro_id": calc_id,
        "nss": datos["nss"],
        "nombre_empleado": datos["nombre"],
        "nombre_normalizado": datos["nombre_normalizado"],
        "numero_empleado": datos["numero_empleado"] or str(payload_row.get("numero_empleado") or "").strip(),
        "cliente": datos["cliente"],
        "planta": datos["planta"],
        "puesto": datos["puesto"],
        "fila": datos["fila"],
        "needs_salario": bool(needs_salario),
        "needs_valor_he": bool(needs_valor_he),
    }


def _catalog_observacion(
    code: str,
    *,
    trabajador: str,
    detalle: str | None = None,
    fila: int | None = None,
    nivel: str | None = None,
    origen: str | None = None,
    accion_parametros: dict[str, Any] | None = None,
    trabajador_datos: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = _WARNING_CATALOG.get(code, {})
    return _make_observacion(
        nivel=nivel or meta.get("nivel_default") or NIVEL_REVIEW,
        origen=origen or meta.get("origen") or "sistema",
        trabajador=trabajador,
        fila=fila,
        detalle=detalle or meta.get("detalle") or _humanize_unknown_code(code),
        impacto=meta.get("impacto") or "Revisar antes de generar borrador",
        accion_sugerida=meta.get("accion") or "Revisar detalle en asistencia o modulos relacionados",
        codigo=code,
        accion_parametros=accion_parametros,
        trabajador_datos=trabajador_datos,
    )


def _observacion_from_run_warning(warning: str) -> dict[str, Any]:
    if warning == MSG_HEADCOUNT_UNAVAILABLE_CALCULO:
        return _make_observacion(
            nivel=NIVEL_REVIEW,
            origen="headcount",
            trabajador="—",
            detalle=warning,
            impacto="Matches de asistencia no validados contra Headcount en vivo",
            accion_sugerida="Verificar identificacion de trabajadores antes de confirmar borrador",
            codigo="headcount_unavailable",
        )
    if warning.startswith(f"{WARN_SAME_NSS_MULTIPLE_CLIENTS}:"):
        return _make_observacion(
            nivel=NIVEL_REVIEW,
            origen="asistencia",
            trabajador="Registro duplicado",
            trabajador_datos={
                "nombre": "Registro con NSS duplicado",
                "cliente": "",
                "planta": "",
                "puesto": "",
                "fila": None,
            },
            detalle="Mismo NSS asociado a multiples clientes en la asistencia importada",
            impacto="Puede generar cruces incorrectos en parametros o vacaciones",
            accion_sugerida="Revisar clientes duplicados para el NSS en asistencia",
            codigo=WARN_SAME_NSS_MULTIPLE_CLIENTS,
        )
    return _make_observacion(
        nivel=NIVEL_INFO,
        origen="sistema",
        trabajador="—",
        detalle=warning,
        impacto="Validacion interna del preflight",
        accion_sugerida="Revisar detalle antes de generar borrador",
    )


def _build_prima_vacacional_context(
    preview_rows: list[dict[str, Any]],
    rows_by_id: dict[int, dict[str, Any]],
    *,
    by_nss: dict[str, dict[str, Any]],
    by_name: dict[str, list[dict[str, Any]]],
    observaciones: list[dict[str, Any]],
    seen: set[str],
) -> list[dict[str, Any]]:
    informativa: list[dict[str, Any]] = []
    for payload_row in preview_rows:
        aid = int(payload_row.get("asistencia_row_id") or 0)
        raw_row = rows_by_id.get(aid)
        if raw_row is None:
            continue

        trabajador = _trabajador_label(raw_row, payload_row)
        fila = int(raw_row.get("row_number") or aid or 0) or None
        asistencia_sol = _asistencia_prima_solicita(raw_row)
        vac_row, match_method, match_confident = _match_vacaciones_for_asistencia(
            raw_row, by_nss=by_nss, by_name=by_name
        )

        payload_aplica = int(payload_row.get("prima_vacacional_aplicada") or 0) == 1
        dias_pend = _float_or_zero(payload_row.get("dias_prima_vacacional_pendientes"))
        importe_est = _float_or_zero(payload_row.get("importe_prima_vacacional"))
        warns = {str(w or "") for w in (payload_row.get("warnings_json") or [])}

        tiene_senal = (
            asistencia_sol
            or payload_aplica
            or dias_pend > 0
            or "prima_vacacional_sin_datos_vacaciones" in warns
            or "prima_vacacional_ya_cubierta" in warns
        )
        if not tiene_senal:
            continue

        if vac_row and match_confident:
            estatus_key, estatus_detalle = _vacaciones_prima_estatus(vac_row)
            vac_pendiente = estatus_key in {"pendiente", "parcial"}
            vac_pagada = estatus_key == "pagada"

            if vac_pendiente or (payload_aplica and not vac_pagada):
                impacto = (
                    f"Importe estimado en borrador: ${importe_est:,.2f}"
                    if importe_est > 0
                    else "Se evaluara conforme a saldo en Vacaciones"
                )
                if dias_pend > 0:
                    impacto = f"{dias_pend:.2f} dias pendientes · {impacto}"
                informativa.append(
                    {
                        "trabajador": trabajador,
                        "cliente_planta": _cliente_planta_label(raw_row, vac_row),
                        "origen": "Vacaciones",
                        "estatus": estatus_detalle,
                        "impacto": impacto,
                        "accion": "Se considerara en el borrador conforme a Vacaciones.",
                        "match_method": match_method,
                    }
                )

            if vac_pagada and (asistencia_sol or payload_aplica or "prima_vacacional_ya_cubierta" in warns):
                _append_observacion(
                    observaciones,
                    seen,
                    _make_observacion(
                        nivel=NIVEL_REVIEW,
                        origen="vacaciones",
                        trabajador=trabajador,
                        fila=fila,
                        detalle="Posible duplicidad: Vacaciones indica prima ya pagada.",
                        impacto="Puede afectar el pago de prima vacacional",
                        accion_sugerida="Revisar modulo Vacaciones o asistencia antes de generar nomina",
                        codigo="prima_vacacional_posible_duplicidad",
                    ),
                )
            elif not asistencia_sol and vac_pendiente:
                _append_observacion(
                    observaciones,
                    seen,
                    _make_observacion(
                        nivel=NIVEL_REVIEW,
                        origen="vacaciones",
                        trabajador=trabajador,
                        fila=fila,
                        detalle=(
                            "Vacaciones indica prima pendiente pero asistencia no marca SOLICITA "
                            f"({estatus_detalle})"
                        ),
                        impacto="El borrador puede no reflejar prima vacacional esperada",
                        accion_sugerida="Alinear asistencia con Vacaciones o validar antes de calcular",
                        codigo="prima_vacacional_pendiente_sin_solicitud_asistencia",
                    ),
                )
            continue

        if asistencia_sol or "prima_vacacional_sin_datos_vacaciones" in warns:
            detalle = "Prima vacacional sin match confiable en Vacaciones"
            if match_method == "nombre_ambiguo":
                detalle = "Prima vacacional solicitada con match ambiguo por nombre en Vacaciones"
            elif vac_row and not match_confident:
                detalle = (
                    "Prima vacacional solicitada pero el match en Vacaciones requiere revision "
                    f"({vac_row.get('match_status') or 'sin estatus'})"
                )
            _append_observacion(
                observaciones,
                seen,
                _make_observacion(
                    nivel=NIVEL_REVIEW,
                    origen="vacaciones",
                    trabajador=trabajador,
                    fila=fila,
                    detalle=detalle,
                    impacto="Puede afectar el pago de prima vacacional",
                    accion_sugerida="Revisar modulo Vacaciones o asistencia antes de generar nomina",
                    codigo="prima_vacacional_sin_match_confiable",
                ),
            )
        elif "prima_vacacional_ya_cubierta" in warns:
            _append_observacion(
                observaciones,
                seen,
                _catalog_observacion(
                    "prima_vacacional_ya_cubierta",
                    trabajador=trabajador,
                    fila=fila,
                    detalle="Asistencia o calculo previo sugiere prima ya cubierta sin confirmacion en Vacaciones",
                ),
            )

    return informativa


def _build_observaciones_from_payload(
    payload: dict[str, Any],
    rows_by_id: dict[int, dict[str, Any]],
    *,
    by_nss: dict[str, dict[str, Any]],
    by_name: dict[str, list[dict[str, Any]]],
    db_path: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    observaciones: list[dict[str, Any]] = []
    seen: set[str] = set()
    param_rows = _param_rows_for_calc(db_path) if db_path else []

    for warning in (payload.get("raw_json") or {}).get("run_warnings") or []:
        w = str(warning or "").strip()
        if w:
            _append_observacion(observaciones, seen, _observacion_from_run_warning(w))

    preview_rows = list(payload.get("rows") or [])
    for payload_row in preview_rows:
        aid = int(payload_row.get("asistencia_row_id") or 0)
        raw_row = rows_by_id.get(aid) or {}
        datos = _build_trabajador_datos(raw_row, payload_row)
        trabajador = datos["nombre"]
        fila = datos["fila"]

        blocks = {str(block or "").strip() for block in (payload_row.get("blocks_json") or []) if str(block or "").strip()}
        calc_match = _param_row_used_by_calc_from_rows(param_rows, raw_row) if param_rows else None

        if WARN_BLOCK_CALC_MISSING_SALARY in blocks and param_rows:
            _append_param_ambiguity_observation(
                observaciones,
                seen,
                raw_row=raw_row,
                payload_row=payload_row,
                datos=datos,
                fila=fila,
                trabajador=trabajador,
                calc_match=calc_match,
                param_rows=param_rows,
            )

        for code in blocks:
            param_action = None
            if code == WARN_BLOCK_CALC_MISSING_SALARY:
                param_action = _param_action_payload(
                    payload_row, raw_row, needs_salario=True, calc_match=calc_match
                )
            elif code == WARN_BLOCK_CALC_MISSING_VALOR_HE:
                param_action = _param_action_payload(
                    payload_row,
                    raw_row,
                    needs_valor_he=True,
                    needs_salario=WARN_BLOCK_CALC_MISSING_SALARY in blocks,
                    calc_match=calc_match,
                )
            _append_observacion(
                observaciones,
                seen,
                _catalog_observacion(
                    code,
                    trabajador=trabajador,
                    fila=fila,
                    nivel=NIVEL_CRITICAL,
                    accion_parametros=param_action,
                    trabajador_datos=datos,
                ),
            )

        for warn in payload_row.get("warnings_json") or []:
            code = str(warn or "").strip()
            if not code or _should_skip_preflight_warning(code):
                continue
            inf_msg = _infonavit_conflict_message(code)
            if inf_msg is not None:
                nivel, detalle, accion = inf_msg
                _append_observacion(
                    observaciones,
                    seen,
                    _make_observacion(
                        nivel=nivel,
                        origen="infonavit",
                        trabajador=trabajador,
                        fila=fila,
                        detalle=detalle,
                        impacto="Puede afectar deduccion INFONAVIT y neto",
                        accion_sugerida=accion,
                        codigo=code.split(":", 1)[0],
                        trabajador_datos=datos,
                    ),
                )
                continue
            if code in {"prima_vacacional_sin_datos_vacaciones", "prima_vacacional_ya_cubierta"}:
                continue
            _append_observacion(
                observaciones,
                seen,
                _catalog_observacion(
                    code,
                    trabajador=trabajador,
                    fila=fila,
                    trabajador_datos=datos,
                ),
            )

        for warn in raw_row.get("warnings") or []:
            txt = str(warn or "").strip()
            if not txt:
                continue
            if "PRIMA VACACIONAL" in txt.upper():
                continue
            _append_observacion(
                observaciones,
                seen,
                _make_observacion(
                    nivel=NIVEL_INFO,
                    origen="asistencia",
                    trabajador=trabajador,
                    fila=fila,
                    detalle=txt,
                    impacto="Advertencia en fila importada",
                    accion_sugerida="Revisar asistencia importada",
                    codigo="asistencia_warning",
                    trabajador_datos=datos,
                ),
            )

    prima_info = _build_prima_vacacional_context(
        preview_rows,
        rows_by_id,
        by_nss=by_nss,
        by_name=by_name,
        observaciones=observaciones,
        seen=seen,
    )
    return observaciones, prima_info


def _param_rows_for_calc(db_path: str) -> list[dict[str, Any]]:
    return list_empleado_parametros(db_path, limit=PARAM_ROWS_LIMIT)


def _param_row_used_by_calc_from_rows(
    param_rows: list[dict[str, Any]],
    raw_row: dict[str, Any],
) -> dict[str, Any] | None:
    by_nss, by_name_cliente = _param_index(param_rows)
    return _match_parametro(raw_row, by_nss, by_name_cliente)


def _param_row_used_by_calc(db_path: str, raw_row: dict[str, Any]) -> dict[str, Any] | None:
    return _param_row_used_by_calc_from_rows(_param_rows_for_calc(db_path), raw_row)


def _param_candidates_for_asistencia(
    param_rows: list[dict[str, Any]],
    raw_row: dict[str, Any],
) -> list[dict[str, Any]]:
    nss = _norm_nss(raw_row.get("nss"))
    nm = _norm_name_param(str(raw_row.get("nombre_empleado") or ""))
    cl = _norm_key(str(raw_row.get("cliente") or ""))
    out: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for p in param_rows:
        pid = int(p.get("id") or 0)
        if not pid or pid in seen_ids:
            continue
        p_nss = _norm_nss(p.get("nss"))
        p_nm = _norm_name_param(str(p.get("nombre") or p.get("nombre_normalizado") or ""))
        p_cl = _norm_key(str(p.get("cliente") or ""))
        matched = False
        if nss and p_nss and p_nss == nss:
            matched = True
        elif nm and p_nm == nm and (not cl or not p_cl or p_cl == cl):
            matched = True
        if matched:
            out.append(p)
            seen_ids.add(pid)
    return out


def _param_has_valid_salario(row: dict[str, Any] | None) -> bool:
    return _float_or_zero((row or {}).get("salario_operativo")) > 0


def _param_has_valid_valor_he(row: dict[str, Any] | None) -> bool:
    val = (row or {}).get("valor_x_he")
    if val in (None, ""):
        return False
    return _float_or_zero(val) >= 0 and _float_or_zero(val) > 0


def _append_param_ambiguity_observation(
    observaciones: list[dict[str, Any]],
    seen: set[str],
    *,
    raw_row: dict[str, Any],
    payload_row: dict[str, Any],
    datos: dict[str, Any],
    fila: int | None,
    trabajador: str,
    calc_match: dict[str, Any] | None,
    param_rows: list[dict[str, Any]],
) -> None:
    candidates = _param_candidates_for_asistencia(param_rows, raw_row)
    if len(candidates) <= 1:
        return
    calc_id = int((calc_match or {}).get("id") or 0)
    calc_sal_ok = _param_has_valid_salario(calc_match)
    others_with_sal = [
        c for c in candidates
        if int(c.get("id") or 0) != calc_id and _param_has_valid_salario(c)
    ]
    if calc_sal_ok or not others_with_sal:
        return
    clientes = sorted({str(c.get("cliente") or "").strip() for c in candidates if str(c.get("cliente") or "").strip()})
    detalle = "Parametros duplicados o ambiguos para este trabajador. Revisa Parametros de Nomina."
    if len(clientes) > 1:
        detalle = (
            f"{detalle} Se detectaron {len(candidates)} registros candidatos "
            f"en clientes: {', '.join(clientes)}."
        )
    else:
        detalle = f"{detalle} Se detectaron {len(candidates)} registros candidatos."
    _append_observacion(
        observaciones,
        seen,
        _make_observacion(
            nivel=NIVEL_REVIEW,
            origen="parametros",
            trabajador=trabajador,
            fila=fila,
            detalle=detalle,
            impacto="El calculo puede seguir leyendo un registro distinto al actualizado",
            accion_sugerida="Consolida o corrige duplicados en Parametros de Nomina",
            codigo=CODE_PARAMETROS_DUPLICADOS,
            trabajador_datos=datos,
            accion_parametros=_param_action_payload(
                payload_row, raw_row, needs_salario=True, calc_match=calc_match
            ),
        ),
    )


def _resolve_parametro_target_id(
    db_path: str,
    raw_row: dict[str, Any],
    *,
    parametro_empleado_id: int | None = None,
) -> tuple[int | None, dict[str, Any] | None]:
    calc_match = _param_row_used_by_calc(db_path, raw_row)
    if parametro_empleado_id:
        explicit = get_empleado_parametro(db_path, int(parametro_empleado_id))
        if explicit:
            return int(parametro_empleado_id), explicit
    if calc_match:
        return int(calc_match["id"]), calc_match
    return None, None


def _verify_parametro_post_save(
    db_path: str,
    raw_row: dict[str, Any],
    *,
    need_salario: bool,
    need_valor_he: bool,
    saved_param_id: int | None,
) -> tuple[str, dict[str, Any] | None]:
    calc_match = _param_row_used_by_calc(db_path, raw_row)
    if calc_match is None:
        if need_salario:
            return "ambiguous", None
        return "ok", None

    calc_id = int(calc_match.get("id") or 0)
    sal_ok = _param_has_valid_salario(calc_match)
    he_ok = not need_valor_he or _param_has_valid_valor_he(calc_match)

    if need_salario and not sal_ok:
        if saved_param_id and calc_id != int(saved_param_id):
            return "ambiguous", calc_match
        return "ambiguous", calc_match
    if not he_ok:
        return "ambiguous", calc_match
    return "ok", calc_match


def _split_observaciones(observaciones: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accionables = [o for o in observaciones if str(o.get("nivel") or "") in {NIVEL_CRITICAL, NIVEL_REVIEW}]
    informativas = [o for o in observaciones if str(o.get("nivel") or "") == NIVEL_INFO]
    accionables.sort(
        key=lambda o: (
            _NIVEL_ORDER.get(str(o.get("nivel") or ""), 9),
            str((o.get("trabajador_datos") or {}).get("nombre") or o.get("trabajador") or ""),
        )
    )
    informativas.sort(
        key=lambda o: (
            str((o.get("trabajador_datos") or {}).get("nombre") or o.get("trabajador") or ""),
            str(o.get("detalle") or ""),
        )
    )
    return accionables, informativas


def _apply_parametro_preflight_patch(
    db_path: str,
    param_id: int,
    *,
    salario: float | None,
    valor_he: float | None,
    nss: str,
    editable_json: dict[str, Any],
    now_iso: str,
    updated_by: int | None,
) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        existing = conn.execute(
            "SELECT nss, salario_operativo, valor_x_he FROM nomina_empleado_parametros WHERE id = ?",
            (int(param_id),),
        ).fetchone()
        if existing is None:
            return False
        sets: list[str] = []
        params: list[Any] = []
        if salario is not None:
            sets.extend(["salario_operativo = ?", "fuente_salario_operativo = ?"])
            params.extend([salario, "preflight_calculo_nomina"])
        if valor_he is not None:
            sets.extend(["valor_x_he = ?", "fuente_valor_x_he = ?"])
            params.extend([valor_he, "preflight_calculo_nomina"])
        if nss and not _norm_nss(existing[0]):
            sets.append("nss = ?")
            params.append(nss)
        sets.append("editable_json = ?")
        params.append(json.dumps(editable_json, ensure_ascii=False))
        sets.append("updated_at = ?")
        params.append(now_iso)
        sets.append("updated_by = ?")
        params.append(updated_by)
        params.append(int(param_id))
        conn.execute(
            f"UPDATE nomina_empleado_parametros SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        conn.commit()
        verify = conn.execute(
            "SELECT salario_operativo, valor_x_he FROM nomina_empleado_parametros WHERE id = ?",
            (int(param_id),),
        ).fetchone()
        if verify is None:
            return False
        if salario is not None and _float_or_zero(verify[0]) <= 0:
            return False
        if valor_he is not None and _float_or_zero(verify[1]) < 0:
            return False
        return True
    finally:
        conn.close()


def save_parametro_from_preflight(
    db_path: str,
    *,
    asistencia_import_id: int,
    asistencia_row_id: int,
    salario_operativo: Any = None,
    valor_x_he: Any = None,
    comentario: str = "",
    parametro_empleado_id: int | None = None,
    updated_by: int | None = None,
    now_iso: str,
) -> tuple[str, str]:
    """Retorna (estado, mensaje) donde estado es ok | ambiguous | error."""
    imp = get_asistencia_import(db_path, int(asistencia_import_id))
    if imp is None:
        return "error", MSG_PARAM_SAVE_FAIL
    raw_row = next((r for r in (imp.get("rows") or []) if int(r.get("id") or 0) == int(asistencia_row_id)), None)
    if raw_row is None:
        return "error", MSG_PARAM_SAVE_FAIL

    salario: float | None = None
    valor_he: float | None = None
    if str(salario_operativo or "").strip() != "":
        try:
            salario = float(salario_operativo)
        except (TypeError, ValueError):
            return "error", MSG_PARAM_SAVE_FAIL
        if salario <= 0:
            return "error", MSG_PARAM_SAVE_FAIL
    if str(valor_x_he or "").strip() != "":
        try:
            valor_he = float(valor_x_he)
        except (TypeError, ValueError):
            return "error", MSG_PARAM_SAVE_FAIL
        if valor_he < 0:
            return "error", MSG_PARAM_SAVE_FAIL
    if salario is None and valor_he is None:
        return "error", MSG_PARAM_SAVE_FAIL

    datos = _build_trabajador_datos(raw_row)
    nss = datos["nss"]
    target_id, existing = _resolve_parametro_target_id(
        db_path,
        raw_row,
        parametro_empleado_id=parametro_empleado_id,
    )

    if existing:
        cur_sal = _float_or_zero(existing.get("salario_operativo"))
        cur_he = existing.get("valor_x_he")
        cur_he_f = _float_or_zero(cur_he) if cur_he not in (None, "") else None
        if salario is not None and cur_sal > 0 and abs(cur_sal - salario) > 0.009:
            return "error", MSG_PARAM_SAVE_FAIL
        if valor_he is not None and cur_he_f is not None and cur_he_f > 0 and abs(cur_he_f - valor_he) > 0.009:
            return "error", MSG_PARAM_SAVE_FAIL

    trace_entry = {
        "at": now_iso,
        "by": updated_by,
        "origen": "preflight_calculo_nomina",
        "asistencia_import_id": int(asistencia_import_id),
        "asistencia_row_id": int(asistencia_row_id),
        "parametro_empleado_id": target_id,
        "comentario": str(comentario or "").strip(),
        "campos": {
            k: v
            for k, v in {
                "salario_operativo": salario,
                "valor_x_he": valor_he,
            }.items()
            if v is not None
        },
    }
    editable = dict((existing or {}).get("editable_json") or {})
    editable.setdefault("preflight_capturas", []).append(trace_entry)

    saved_id: int | None = None
    if target_id and existing:
        ok = _apply_parametro_preflight_patch(
            db_path,
            int(target_id),
            salario=salario,
            valor_he=valor_he,
            nss=nss,
            editable_json=editable,
            now_iso=now_iso,
            updated_by=updated_by,
        )
        if not ok:
            return "error", MSG_PARAM_SAVE_FAIL
        saved_id = int(target_id)
    else:
        payload_row: dict[str, Any] = {
            "nombre": datos["nombre"],
            "nombre_normalizado": _norm_name_param(datos["nombre"]),
            "nss": nss or None,
            "numero_empleado": datos["numero_empleado"] or None,
            "cliente": datos["cliente"] or None,
            "planta": datos["planta"] or None,
            "puesto": datos["puesto"] or None,
            "banco": str(raw_row.get("banco") or "").strip() or None,
            "cuenta": str(raw_row.get("cuenta") or "").strip() or None,
            "record_kind": "preflight_manual",
            "editable_json": editable,
        }
        if salario is not None:
            payload_row["salario_operativo"] = salario
            payload_row["fuente_salario_operativo"] = "preflight_calculo_nomina"
        if valor_he is not None:
            payload_row["valor_x_he"] = valor_he
            payload_row["fuente_valor_x_he"] = "preflight_calculo_nomina"

        inserted, updated = upsert_empleado_parametros(
            db_path,
            [payload_row],
            import_id=0,
            now_iso=now_iso,
            overwrite_keys={"salario_operativo", "valor_x_he", "nss", "fuente_salario_operativo", "fuente_valor_x_he"},
        )
        if inserted <= 0 and updated <= 0:
            return "error", MSG_PARAM_SAVE_FAIL
        refreshed = _param_row_used_by_calc(db_path, raw_row)
        saved_id = int(refreshed["id"]) if refreshed else None

    verify_status, _ = _verify_parametro_post_save(
        db_path,
        raw_row,
        need_salario=salario is not None,
        need_valor_he=valor_he is not None,
        saved_param_id=saved_id,
    )
    if verify_status == "ok":
        return "ok", MSG_PARAM_SAVE_OK
    if verify_status == "ambiguous":
        return "ambiguous", MSG_PARAM_SAVE_AMBIGUOUS
    return "error", MSG_PARAM_SAVE_FAIL


def _resumen_observaciones(observaciones: list[dict[str, Any]]) -> dict[str, int]:
    resumen = {NIVEL_CRITICAL: 0, NIVEL_REVIEW: 0, NIVEL_INFO: 0}
    for obs in observaciones:
        nivel = str(obs.get("nivel") or NIVEL_INFO)
        if nivel in resumen:
            resumen[nivel] += 1
    return resumen


def _preguntas_necesarias(
    preview_rows: list[dict[str, Any]],
    *,
    observaciones: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    has_dl = any(int(r.get("domingo_laborado_detected") or 0) > 0 for r in preview_rows)
    has_bonos_ambiguos = any(
        "bono_manual_no_numerico_revisar" in {str(w or "") for w in (r.get("warnings_json") or [])}
        for r in preview_rows
    )

    preguntas: list[dict[str, Any]] = []
    if has_dl:
        preguntas.append(
            {
                "id": "domingo_dl",
                "prompt": "Se detecto clave DL. Como se pagaran los domingos laborados (DL)?",
                "options": [
                    {"value": "proporcional", "label": "Proporcional 1.17"},
                    {"value": "prima", "label": "Prima dominical 1.25"},
                    {"value": "manual", "label": "Revision manual"},
                ],
                "default": "proporcional",
            }
        )
    if has_bonos_ambiguos:
        preguntas.append(
            {
                "id": "bonos_ambiguos",
                "prompt": "Se detectaron bonos con informacion no estandar. Como deseas tratarlos?",
                "options": [
                    {"value": "manual", "label": "Mandar a revision manual"},
                    {"value": "solo_numericos", "label": "Continuar solo con bonos numericos validos"},
                ],
                "default": "manual",
            }
        )
    return preguntas


def build_calculo_preflight(db_path: str, *, import_id: int) -> dict[str, Any]:
    out: dict[str, Any] = {
        "import_id": int(import_id),
        "periodo_detectado": "",
        "cliente_grupo_detectado": "",
        "clientes_detectados": [],
        "plantas_detectadas": [],
        "total_filas": 0,
        "total_trabajadores_validos": 0,
        "alertas_criticas": [],
        "alertas_no_criticas": [],
        "observaciones": [],
        "observaciones_accionables": [],
        "observaciones_informativas": [],
        "observaciones_resumen": {NIVEL_CRITICAL: 0, NIVEL_REVIEW: 0, NIVEL_INFO: 0},
        "tiene_observaciones_criticas": False,
        "tiene_observaciones_revision": False,
        "requiere_aceptacion_observaciones": False,
        "prima_vacacional_informativa": [],
        "preguntas_necesarias": [],
        "defaults_politica": dict(CALCULO_DEFAULT_POLICY),
        "payload_preview": None,
        "preflight_version": PREFLIGHT_VERSION,
        "vacaciones_import_id": None,
    }
    imp = get_asistencia_import(db_path, int(import_id))
    if imp is None:
        out["alertas_criticas"] = ["No se pudo leer la importacion seleccionada."]
        return out

    rows = list(imp.get("rows") or [])
    valid_rows = _rows_validas(rows)
    out["total_filas"] = len(rows)
    out["periodo_detectado"] = str(imp.get("semana") or f"{imp.get('fecha_inicio')} -> {imp.get('fecha_fin')}")
    out["cliente_grupo_detectado"] = str(imp.get("cliente") or "")
    out["clientes_detectados"] = _unique_ordered(
        [str(c) for c in (imp.get("clientes") or [])]
        + [str(r.get("cliente") or "") for r in rows]
    )
    out["plantas_detectadas"] = sorted(
        {
            str(r.get("planta") or "").strip()
            for r in rows
            if str(r.get("planta") or "").strip()
        }
    )

    worker_keys: set[str] = set()
    for row in valid_rows:
        key = _trabajador_key(row)
        if key:
            worker_keys.add(key)
    out["total_trabajadores_validos"] = len(worker_keys)

    if not rows:
        out["alertas_criticas"].append("La importacion no contiene filas para calcular.")
        return out
    if not valid_rows:
        out["alertas_criticas"].append("No hay filas validas para calcular en esta importacion.")
        return out

    try:
        payload = build_calculo_payload(
            db_path,
            asistencia_import_id=int(import_id),
            clientes_filter=list(out["clientes_detectados"]),
            config_form=dict(CALCULO_DEFAULT_POLICY),
        )
    except ValueError as exc:
        out["alertas_criticas"].append(f"No se pudo preparar el calculo: {exc}")
        return out

    if int(payload.get("total_empleados") or 0) <= 0:
        out["alertas_criticas"].append("No hay trabajadores validos despues de aplicar validaciones actuales.")
        return out

    out["payload_preview"] = payload

    vac_imp, by_nss, by_name = _build_vacaciones_index(db_path)
    out["vacaciones_import_id"] = vac_imp
    rows_by_id = {int(r.get("id") or 0): r for r in rows if int(r.get("id") or 0) > 0}
    observaciones, prima_info = _build_observaciones_from_payload(
        payload,
        rows_by_id,
        by_nss=by_nss,
        by_name=by_name,
        db_path=db_path,
    )
    out["observaciones"] = observaciones
    accionables, informativas = _split_observaciones(observaciones)
    out["observaciones_accionables"] = accionables
    out["observaciones_informativas"] = informativas
    out["prima_vacacional_informativa"] = prima_info
    resumen = _resumen_observaciones(observaciones)
    out["observaciones_resumen"] = resumen
    out["tiene_observaciones_criticas"] = resumen[NIVEL_CRITICAL] > 0
    out["tiene_observaciones_revision"] = resumen[NIVEL_REVIEW] > 0
    out["requiere_aceptacion_observaciones"] = resumen[NIVEL_REVIEW] > 0 and resumen[NIVEL_CRITICAL] == 0

    out["alertas_no_criticas"] = [
        f"[{obs['nivel']}] {obs['detalle']}" for obs in observaciones if obs.get("nivel") != NIVEL_CRITICAL
    ]

    preview_rows = list(payload.get("rows") or [])
    out["preguntas_necesarias"] = _preguntas_necesarias(preview_rows, observaciones=observaciones)
    return out


def resolve_config_from_preflight_answers(answers: dict[str, str]) -> dict[str, Any]:
    cfg = dict(CALCULO_DEFAULT_POLICY)
    dom = str(answers.get("domingo_dl") or "").strip().lower()
    if dom in {"proporcional", "prima", "manual"}:
        cfg["domingo_opcion"] = dom
    return cfg


def preflight_requires_screen(preflight: dict[str, Any]) -> bool:
    if preflight.get("alertas_criticas"):
        return True
    if preflight.get("preguntas_necesarias"):
        return True
    if preflight.get("observaciones"):
        return True
    if preflight.get("prima_vacacional_informativa"):
        return True
    return False
