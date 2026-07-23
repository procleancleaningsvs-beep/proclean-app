from __future__ import annotations

from typing import Any

ACTIVE_CODES = frozenset({"A", "I", "V"})


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


def classify_monthly_status(
    *,
    daily: list[dict[str, Any]],
    events: list[dict[str, Any]],
    selected_week_count: int,
    weeks_with_presence: int,
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

    active_weeks = _active_week_ids(daily)
    if (
        len(active_weeks) >= selected_week_count
        and not bajas
        and not reingresos
        and not any(str(d.get("interpretation_status") or "") == "conflict" for d in daily)
    ):
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
