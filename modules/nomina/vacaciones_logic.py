"""Cálculo centralizado de saldos de vacaciones para Nómina."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from modules.shared.vacaciones import (
    calcular_dias_vacaciones_devengados,
    dias_vacaciones_ley_por_anio_servicio,
)

PRIMA_VACACIONAL_PCT = Decimal("0.25")
REINGRESO_PATTERN = re.compile(r"reingreso", re.IGNORECASE)


def parse_iso_date(value: str | None) -> date | None:
    s = str(value or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s, fmt).date()
        except ValueError:
            continue
    return None


def resolve_fecha_corte(
    *,
    periodo_fecha_fin: str | None = None,
    fecha_corte_ui: str | None = None,
) -> date:
    for raw in (fecha_corte_ui, periodo_fecha_fin):
        parsed = parse_iso_date(raw)
        if parsed is not None:
            return parsed
    return date.today()


def _f(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def calcular_balance_vacaciones_trabajador(
    row: dict[str, Any],
    *,
    fecha_corte: date | None = None,
) -> dict[str, Any]:
    """
    Calcula saldo de vacaciones usando fecha de ingreso Headcount (fecha_ingreso_usada)
    y la misma lógica de devengamiento que Finiquitos.
    """
    fecha_corte = fecha_corte or date.today()
    warnings = list(row.get("warnings") or [])

    ingreso = parse_iso_date(
        str(row.get("fecha_ingreso_usada") or row.get("fecha_ingreso_headcount") or row.get("fecha_ingreso_historica") or "")
    )
    if ingreso is None:
        warnings.append("Sin fecha de ingreso usable para recalcular saldo.")
        return {
            "fecha_corte": fecha_corte.isoformat(),
            "dias_generados": None,
            "dias_devengados": None,
            "dias_utilizados": _f(row.get("dias_utilizados")),
            "vacaciones_laboradas": _f(row.get("vacaciones_laboradas")),
            "dias_pagados": _f(row.get("dias_pagados")),
            "dias_consumidos": None,
            "saldo_calculado": None,
            "dias_restantes_calculado": None,
            "prima_pendiente": None,
            "prima_pagada_estimada": None,
            "pasos": [],
            "warnings": warnings,
        }

    devengados = calcular_dias_vacaciones_devengados(ingreso, fecha_corte)
    dias_generados = float(devengados["dias_vac_total_dev"])
    anios_completos = int(devengados["anios_completos"])
    dias_anuales = int(devengados["dias_vac_anuales_actual"])

    dias_utilizados = _f(row.get("dias_utilizados"))
    vacaciones_laboradas = _f(row.get("vacaciones_laboradas"))
    dias_pagados = _f(row.get("dias_pagados"))
    dias_consumidos = max(dias_pagados, dias_utilizados + vacaciones_laboradas)
    saldo = round(dias_generados - dias_consumidos, 4)

    if saldo < 0:
        warnings.append("Saldo negativo detectado")

    sueldo = row.get("sueldo_usado")
    if sueldo in (None, ""):
        sueldo = row.get("sueldo_headcount")
    if sueldo in (None, ""):
        sueldo = row.get("sueldo_historico")
    sueldo_f = _f(sueldo)

    prima_2025 = bool(row.get("prima_2025_pagada"))
    prima_2026 = bool(row.get("prima_2026_pagada"))
    prima_pagada_estimada = round(sueldo_f * dias_pagados * float(PRIMA_VACACIONAL_PCT), 2) if sueldo_f else None
    dias_prima_pendientes = max(0.0, saldo) if not prima_2026 else 0.0
    prima_pendiente = round(sueldo_f * dias_prima_pendientes * float(PRIMA_VACACIONAL_PCT), 2) if sueldo_f else None

    pasos = [
        {
            "titulo": "Antigüedad",
            "detalle": f"Años completos: {anios_completos}; días anuales vigentes (LFT): {dias_anuales}.",
        },
        {
            "titulo": "Devengamiento al corte",
            "detalle": (
                f"Ciclos completos: {float(devengados['dias_vac_completos']):.4f}; "
                f"proporcional ciclo actual: {float(devengados['dias_vac_prop_actual']):.4f}; "
                f"total devengado: {dias_generados:.4f}."
            ),
        },
        {
            "titulo": "Consumo histórico",
            "detalle": (
                f"Utilizados: {dias_utilizados}; laboradas: {vacaciones_laboradas}; "
                f"pagados: {dias_pagados}; consumo efectivo: {dias_consumidos:.4f}."
            ),
        },
        {
            "titulo": "Saldo",
            "detalle": f"Saldo = devengado ({dias_generados:.4f}) − consumo ({dias_consumidos:.4f}) = {saldo:.4f}.",
        },
        {
            "titulo": "Prima vacacional",
            "detalle": (
                f"Prima 2025 pagada: {'Sí' if prima_2025 else 'No'}; "
                f"Prima 2026 pagada: {'Sí' if prima_2026 else 'No'}; "
                f"pendiente estimada: {prima_pendiente if prima_pendiente is not None else 'N/D'}."
            ),
        },
    ]

    return {
        "fecha_corte": fecha_corte.isoformat(),
        "fecha_ingreso_usada": ingreso.isoformat(),
        "anios_completos": anios_completos,
        "dias_anuales_vigentes": dias_anuales,
        "dias_generados": dias_generados,
        "dias_devengados": dias_generados,
        "dias_vacaciones_historico": _f(row.get("dias_vacaciones_historico")),
        "dias_utilizados": dias_utilizados,
        "vacaciones_laboradas": vacaciones_laboradas,
        "dias_pagados": dias_pagados,
        "dias_consumidos": dias_consumidos,
        "saldo_calculado": saldo,
        "dias_restantes_calculado": saldo,
        "prima_pendiente": prima_pendiente,
        "prima_pagada_estimada": prima_pagada_estimada,
        "pasos": pasos,
        "warnings": warnings,
        "tabla_lft": {str(i): dias_vacaciones_ley_por_anio_servicio(i) for i in range(1, 6)},
    }


def aplicar_calculo_a_fila(row: dict[str, Any], *, fecha_corte: date | None = None) -> dict[str, Any]:
    """Enriquece fila importada/DB con campos calculados."""
    calc = calcular_balance_vacaciones_trabajador(row, fecha_corte=fecha_corte)
    out = dict(row)
    out["warnings"] = calc["warnings"]
    out["dias_generados"] = calc["dias_generados"]
    out["saldo_calculado"] = calc["saldo_calculado"]
    out["dias_restantes_calculado"] = calc["dias_restantes_calculado"]
    out["prima_pendiente"] = calc["prima_pendiente"]
    if calc["dias_generados"] is not None:
        out["dias_vacaciones_historico"] = calc["dias_generados"]
    editable = dict(out.get("editable_json") or {})
    editable["calc_json"] = {
        "fecha_corte": calc["fecha_corte"],
        "pasos": calc["pasos"],
        "anios_completos": calc.get("anios_completos"),
        "dias_anuales_vigentes": calc.get("dias_anuales_vigentes"),
    }
    out["editable_json"] = editable
    if out.get("sueldo_usado") not in (None, "") and out.get("dias_pagados") is not None:
        out["monto_total_recalculado"] = round(_f(out["sueldo_usado"]) * max(_f(out["dias_pagados"]), 0.0) * float(PRIMA_VACACIONAL_PCT), 2)
    return out


def build_migration_events_from_row(
    row: dict[str, Any],
    *,
    import_batch_id: int,
    imported_from_file: str,
    created_at: str,
) -> list[dict[str, Any]]:
    """Convierte datos históricos del Excel en eventos de ledger (staging)."""
    events: list[dict[str, Any]] = []
    base = {
        "empleado_id": row.get("id"),
        "worker_nss": row.get("nss"),
        "worker_nombre_normalizado": row.get("nombre_normalizado"),
        "source": "excel_historico_carrier",
        "imported_from_file": imported_from_file,
        "import_batch_id": import_batch_id,
        "created_at": created_at,
        "is_reviewed": 0,
        "is_active": 1,
    }

    if _f(row.get("dias_utilizados")) > 0:
        events.append(
            {
                **base,
                "event_type": "vacaciones_disfrutadas",
                "event_date": row.get("fecha_ingreso_usada"),
                "period_label": "historico_excel",
                "days": _f(row.get("dias_utilizados")),
                "amount": None,
                "notes": "Importado desde Excel histórico",
            }
        )
    if _f(row.get("vacaciones_laboradas")) > 0:
        events.append(
            {
                **base,
                "event_type": "vacaciones_laboradas",
                "event_date": row.get("fecha_ingreso_usada"),
                "period_label": "historico_excel",
                "days": _f(row.get("vacaciones_laboradas")),
                "amount": None,
                "notes": "Vacaciones laboradas detectadas en Excel",
            }
        )
    if _f(row.get("dias_pagados")) > 0:
        events.append(
            {
                **base,
                "event_type": "migracion_historica",
                "event_date": row.get("fecha_pago_prima_2026") or row.get("fecha_ingreso_usada"),
                "period_label": "dias_pagados",
                "days": _f(row.get("dias_pagados")),
                "amount": row.get("monto_total_historico"),
                "notes": "Días pagados registrados en Excel histórico",
            }
        )
    if row.get("prima_2025_pagada"):
        events.append(
            {
                **base,
                "event_type": "prima_vacacional_pagada",
                "event_date": None,
                "period_label": "2025",
                "days": None,
                "amount": row.get("monto_total_recalculado"),
                "notes": f"Semana pago: {row.get('semana_pago_prima_2025') or 'N/D'}",
            }
        )
    if row.get("prima_2026_pagada"):
        events.append(
            {
                **base,
                "event_type": "prima_vacacional_pagada",
                "event_date": row.get("fecha_pago_prima_2026"),
                "period_label": "2026",
                "days": None,
                "amount": row.get("monto_total_recalculado"),
                "notes": "Prima vacacional 2026 marcada como pagada en Excel",
            }
        )
    comentarios = str(row.get("comentarios") or "")
    if REINGRESO_PATTERN.search(comentarios):
        events.append(
            {
                **base,
                "event_type": "reinicio_reingreso",
                "event_date": row.get("fecha_ingreso_headcount") or row.get("fecha_ingreso_usada"),
                "period_label": "reingreso",
                "days": None,
                "amount": None,
                "notes": comentarios,
                "is_reviewed": 0,
            }
        )
    if comentarios and not REINGRESO_PATTERN.search(comentarios):
        events.append(
            {
                **base,
                "event_type": "ajuste_manual",
                "event_date": None,
                "period_label": "comentarios",
                "days": None,
                "amount": None,
                "notes": comentarios,
            }
        )
    if _f(row.get("saldo_calculado")) < 0 or _f(row.get("dias_restantes_calculado")) < 0:
        events.append(
            {
                **base,
                "event_type": "correccion_administrativa",
                "event_date": None,
                "period_label": "saldo_negativo",
                "days": _f(row.get("saldo_calculado") or row.get("dias_restantes_calculado")),
                "amount": None,
                "notes": "Saldo negativo detectado en importación; requiere revisión",
            }
        )
    if not events:
        events.append(
            {
                **base,
                "event_type": "migracion_historica",
                "event_date": row.get("fecha_ingreso_usada"),
                "period_label": "snapshot",
                "days": _f(row.get("dias_restantes_calculado")),
                "amount": row.get("monto_total_historico"),
                "notes": "Registro histórico importado sin movimientos explícitos",
            }
        )
    return events


def detect_headcount_diff_warnings(row: dict[str, Any]) -> list[str]:
    """Genera warnings cuando Excel difiere de Headcount."""
    warnings: list[str] = []
    hist_ing = str(row.get("fecha_ingreso_historica") or "").strip()
    hc_ing = str(row.get("fecha_ingreso_headcount") or "").strip()
    if hist_ing and hc_ing and hist_ing != hc_ing:
        warnings.append("Fecha de ingreso del Excel difiere de Headcount")

    hist_sueldo = row.get("sueldo_historico")
    hc_sueldo = row.get("sueldo_headcount")
    if hist_sueldo not in (None, "") and hc_sueldo not in (None, ""):
        try:
            if abs(float(hist_sueldo) - float(hc_sueldo)) > 0.01:
                warnings.append("Sueldo histórico difiere de Headcount")
        except (TypeError, ValueError):
            pass

    hist_planta = str(row.get("planta_historica") or "").strip().upper()
    hc_planta = str(row.get("planta_headcount") or "").strip().upper()
    if hist_planta and hc_planta and hist_planta != hc_planta:
        warnings.append("Planta histórica difiere de Headcount")

    comentarios = str(row.get("comentarios") or "")
    if REINGRESO_PATTERN.search(comentarios):
        warnings.append("Comentario de reingreso requiere revisión")

    return warnings
