from __future__ import annotations

from typing import Any

from modules.gestion_idse_sua.reportes.date_utils import days_in_calendar_month, iter_period_dates


def compute_month_coverage(
    weeks: list[dict[str, Any]],
    *,
    mes: int,
    anio: int,
) -> dict[str, Any]:
    month_days = days_in_calendar_month(mes=mes, anio=anio)
    month_iso = {day.isoformat() for day in month_days}
    covered_counts: dict[str, int] = {}

    for week in weeks:
        fecha_inicio = week.get("fecha_inicio")
        fecha_fin = week.get("fecha_fin")
        if not fecha_inicio or not fecha_fin:
            continue
        for day in iter_period_dates(str(fecha_inicio), str(fecha_fin)):
            if day.month == mes and day.year == anio:
                iso = day.isoformat()
                covered_counts[iso] = covered_counts.get(iso, 0) + 1

    covered_dates = sorted(covered_counts)
    missing_dates = sorted(month_iso - set(covered_dates))
    overlap_dates = sorted(iso for iso, count in covered_counts.items() if count > 1)
    warnings: list[str] = []
    if overlap_dates:
        warnings.append(f"Superposición de periodos en {len(overlap_dates)} día(s) del mes.")
    if missing_dates:
        warnings.append(
            f"Cobertura calendario incompleta: faltan {len(missing_dates)} día(s) "
            f"({missing_dates[0]} … {missing_dates[-1]})."
            if len(missing_dates) > 1
            else f"Cobertura calendario incompleta: falta {missing_dates[0]}."
        )

    return {
        "coverage_complete": not missing_dates,
        "covered_dates": covered_dates,
        "missing_dates": missing_dates,
        "overlap_dates": overlap_dates,
        "warnings": warnings,
    }
