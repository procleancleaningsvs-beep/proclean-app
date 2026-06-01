"""Prevalidacion para flujo de entrada de calculo preliminar de nomina."""
from __future__ import annotations

from typing import Any

from modules.nomina.calc_service import build_calculo_payload
from modules.nomina.db import get_asistencia_import
from modules.nomina.validators import _norm_header

CALCULO_DEFAULT_POLICY = {
    "domingo_opcion": "proporcional",
    "dias_tarifa_isr": 7,
    "dias_tarifa_subs": 7,
    "es_fin_de_mes": False,
    "permitir_negativo_isr": False,
}


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


def _has_prima_signal(payload_row: dict[str, Any], raw_row: dict[str, Any] | None) -> bool:
    if int(payload_row.get("prima_vacacional_aplicada") or 0) == 1:
        return True
    if _float_or_zero(payload_row.get("dias_prima_vacacional_pendientes")) > 0:
        return True
    warns = {str(w or "") for w in (payload_row.get("warnings_json") or [])}
    if "prima_vacacional_sin_datos_vacaciones" in warns:
        return True
    if "prima_vacacional_ya_cubierta" in warns:
        return True
    prima_raw = _norm_header(str((raw_row or {}).get("prima_vacacional") or ""))
    return prima_raw == "SOLICITA"


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
        "preguntas_necesarias": [],
        "defaults_politica": dict(CALCULO_DEFAULT_POLICY),
        "payload_preview": None,
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

    run_warnings = [str(w) for w in ((payload.get("raw_json") or {}).get("run_warnings") or []) if str(w).strip()]
    out["alertas_no_criticas"].extend(run_warnings)
    if int(payload.get("warning_count") or 0) > 0:
        out["alertas_no_criticas"].append(
            f"Se detectaron {int(payload.get('warning_count') or 0)} advertencias en filas del borrador."
        )
    if int(payload.get("block_count") or 0) > 0:
        out["alertas_no_criticas"].append(
            f"Se detectaron {int(payload.get('block_count') or 0)} filas bloqueadas para revision manual."
        )

    rows_by_id = {int(r.get("id") or 0): r for r in rows if int(r.get("id") or 0) > 0}
    preview_rows = list(payload.get("rows") or [])
    has_dl = any(int(r.get("domingo_laborado_detected") or 0) > 0 for r in preview_rows)
    has_prima = any(
        _has_prima_signal(r, rows_by_id.get(int(r.get("asistencia_row_id") or 0)))
        for r in preview_rows
    )
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
    if has_prima:
        preguntas.append(
            {
                "id": "prima_vacacional",
                "prompt": "Se detecto prima vacacional solicitada o pendiente. Como deseas manejarla?",
                "options": [
                    {
                        "value": "auto",
                        "label": "Aplicar automaticamente cuando el historial indique que procede",
                    },
                    {"value": "manual", "label": "Mandar a revision manual"},
                    {"value": "no_aplicar", "label": "No aplicar en este calculo"},
                ],
                "default": "auto",
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
    if out["alertas_no_criticas"]:
        preguntas.append(
            {
                "id": "alertas_no_criticas",
                "prompt": "Se detectaron alertas no criticas. Deseas continuar con advertencias?",
                "options": [
                    {"value": "continuar", "label": "Continuar con advertencias"},
                    {"value": "cancelar", "label": "Cancelar y revisar asistencia"},
                ],
                "default": "continuar",
            }
        )
    out["preguntas_necesarias"] = preguntas
    return out


def resolve_config_from_preflight_answers(answers: dict[str, str]) -> dict[str, Any]:
    cfg = dict(CALCULO_DEFAULT_POLICY)
    dom = str(answers.get("domingo_dl") or "").strip().lower()
    if dom in {"proporcional", "prima", "manual"}:
        cfg["domingo_opcion"] = dom
    return cfg
