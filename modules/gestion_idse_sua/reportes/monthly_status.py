from __future__ import annotations

from typing import Any

ACTIVE_CODES = frozenset({"A", "I", "V"})
MIN_WEEKS_FOR_FULL_MONTH = 4


def person_has_active_evidence(daily: list[dict[str, Any]]) -> bool:
    return any(str(d.get("code_normalized") or "") in ACTIVE_CODES for d in daily)


def compute_totals(daily: list[dict[str, Any]]) -> dict[str, int]:
    totals = {"A": 0, "F": 0, "I": 0, "V": 0, "D": 0}
    for day in daily:
        code = str(day.get("code_normalized") or "")
        if code in totals:
            totals[code] += 1
    return totals


def first_and_last_a(daily: list[dict[str, Any]]) -> tuple[str, str]:
    first_a = ""
    last_a = ""
    for day in sorted(daily, key=lambda d: str(d.get("fecha_iso") or "")):
        if day.get("code_normalized") == "A":
            if not first_a:
                first_a = str(day["fecha_iso"])
            last_a = str(day["fecha_iso"])
    return first_a, last_a


def _active_week_ids(daily: list[dict[str, Any]]) -> set[int]:
    return {
        int(d.get("period_id") or 0)
        for d in daily
        if str(d.get("code_normalized") or "") in ACTIVE_CODES and int(d.get("period_id") or 0) > 0
    }


def person_covers_selected_weeks(
    daily: list[dict[str, Any]],
    selected_period_ids: set[int],
) -> bool:
    if not selected_period_ids:
        return False
    active_weeks = _active_week_ids(daily)
    return selected_period_ids.issubset(active_weeks)


def classify_monthly_status(
    *,
    daily: list[dict[str, Any]],
    events: list[dict[str, Any]],
    selected_week_count: int,
    weeks_with_presence: int,
    coverage_complete: bool = False,
    selected_period_ids: set[int] | None = None,
) -> str:
    if not daily:
        return "Revisión"
    if any(str(e.get("interpretation_status") or "") == "conflict" for e in daily):
        return "Revisión"

    operational = [e for e in events if e.get("event_type") in {"posible_baja", "posible_reingreso"}]
    bajas = [e for e in operational if e.get("event_type") == "posible_baja" and e.get("status") != "discarded"]
    reingresos = [e for e in operational if e.get("event_type") == "posible_reingreso"]
    if any(str(e.get("status") or "") == "review" for e in operational):
        return "Revisión"
    if bajas and reingresos:
        if len(bajas) > 1 or len(reingresos) > 1:
            return "Varias interrupciones"
        return "Baja y reingreso"
    if bajas and not reingresos:
        return "Salida durante el mes"
    if reingresos and not bajas:
        return "Ingreso durante el mes"

    period_ids = selected_period_ids or set()
    active_weeks = _active_week_ids(daily)
    can_be_full_month = (
        coverage_complete
        and selected_week_count >= MIN_WEEKS_FOR_FULL_MONTH
        and not bajas
        and not reingresos
        and not any(str(d.get("interpretation_status") or "") == "conflict" for d in daily)
        and person_covers_selected_weeks(daily, period_ids)
        and len(active_weeks) >= selected_week_count
    )
    if can_be_full_month:
        return "Todo el mes"

    totals = compute_totals(daily)
    if totals["I"] and totals["I"] >= len(daily) // 2 and len(active_weeks) < selected_week_count:
        return "Incapacidad o vacaciones"
    if totals["V"] and totals["V"] >= len(daily) // 2 and len(active_weeks) < selected_week_count:
        return "Incapacidad o vacaciones"
    if weeks_with_presence < selected_week_count:
        return "Presencia parcial"

    first_a, last_a = first_and_last_a(daily)
    month_days = sorted({str(d.get("fecha_iso") or "") for d in daily if d.get("fecha_iso")})
    if not month_days:
        return "Revisión"
    if first_a and last_a and (first_a != month_days[0] or last_a != month_days[-1]):
        return "Presencia parcial"
    if totals["I"] or totals["V"]:
        return "Incapacidad o vacaciones"
    return "Presencia parcial"
