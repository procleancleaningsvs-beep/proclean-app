from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

STREAK_BREAKERS = frozenset({"A", "I", "V"})
IGNORE_FOR_STREAK = frozenset({"D", ""})
INFERENCE_STOPPERS = frozenset({"review", "conflict"})


def count_consecutive_absences(codes: list[str]) -> int:
    streak = 0
    max_streak = 0
    for code in codes:
        if code in IGNORE_FOR_STREAK:
            continue
        if code == "F":
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return max_streak


def detect_four_absence_event(codes: list[str]) -> bool:
    return count_consecutive_absences(codes) >= 4


def _day_sort_key(day: dict[str, Any]) -> tuple[str, int]:
    return (str(day.get("fecha_iso") or ""), int(day.get("period_id") or 0))


def merge_daily_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_date[str(record["fecha_iso"])].append(record)

    merged: list[dict[str, Any]] = []
    warnings: list[str] = []
    for fecha, items in sorted(by_date.items()):
        codes = {str(item.get("code_normalized") or "") for item in items if item.get("code_normalized")}
        statuses = {str(item.get("interpretation_status") or "ok") for item in items}
        if len(codes) > 1:
            warnings.append(f"Conflicto de asistencia en {fecha}: códigos distintos entre semanas importadas.")
            merged.append(
                {
                    **items[0],
                    "interpretation_status": "conflict",
                    "warning": "Conflicto entre periodos importados.",
                }
            )
            continue
        item = items[0]
        if "conflict" in statuses:
            item = {**item, "interpretation_status": "conflict"}
        merged.append(item)
    merged.sort(key=_day_sort_key)
    return merged, warnings


def _next_iso_day(fecha_iso: str) -> str:
    day = datetime.strptime(fecha_iso, "%Y-%m-%d").date()
    return (day + timedelta(days=1)).isoformat()


def _has_inference_blocker(days: list[dict[str, Any]], start_idx: int, end_idx: int) -> bool:
    for day in days[start_idx : end_idx + 1]:
        code = str(day.get("code_normalized") or "")
        status = str(day.get("interpretation_status") or "ok")
        if status in INFERENCE_STOPPERS:
            return True
        if code in {"I", "V"}:
            return True
        if code and code not in {"F", "D", "A"}:
            return True
    return False


def suggest_trajectory_events(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not daily:
        return events

    ordered = sorted(daily, key=lambda d: str(d.get("fecha_iso") or ""))
    streak = 0
    streak_indices: list[int] = []

    def _append_baja(idx: int) -> None:
        block_start = streak_indices[0]
        last_a_idx = None
        for pos in range(block_start - 1, -1, -1):
            if str(ordered[pos].get("code_normalized") or "") == "A":
                last_a_idx = pos
                break
        scan_start = block_start if last_a_idx is None else last_a_idx + 1
        blocked = _has_inference_blocker(ordered, scan_start, idx)
        fecha_sugerida = ""
        status = "suggested"
        reason = "Cuatro faltas consecutivas observadas (descansos ignorados)."
        if last_a_idx is not None and not blocked:
            fecha_sugerida = _next_iso_day(str(ordered[last_a_idx]["fecha_iso"]))
        else:
            status = "review"
            reason += " Requiere revisión por bloqueo, hueco o código intermedio."
        events.append(
            {
                "event_type": "posible_baja",
                "fecha_sugerida": fecha_sugerida,
                "sequence_start": ordered[streak_indices[0]]["fecha_iso"],
                "status": status,
                "reason": reason,
            }
        )

    for idx, day in enumerate(ordered):
        code = str(day.get("code_normalized") or "")
        status = str(day.get("interpretation_status") or "ok")
        fecha = str(day.get("fecha_iso") or "")
        if status in INFERENCE_STOPPERS:
            streak = 0
            streak_indices = []
            continue
        if code in IGNORE_FOR_STREAK:
            continue
        if code == "F":
            streak_indices.append(idx)
            streak += 1
            if streak == 4:
                _append_baja(idx)
        elif code in STREAK_BREAKERS:
            if code == "A" and events and events[-1].get("event_type") == "posible_baja":
                events.append(
                    {
                        "event_type": "posible_reingreso",
                        "fecha_sugerida": fecha,
                        "status": "suggested",
                        "reason": "Primera asistencia después de secuencia de faltas.",
                    }
                )
            streak = 0
            streak_indices = []
        else:
            streak = 0
            streak_indices = []

    first_a = next((d for d in ordered if d.get("code_normalized") == "A"), None)
    if first_a:
        events.insert(
            0,
            {
                "event_type": "primera_a",
                "fecha_sugerida": first_a["fecha_iso"],
                "status": "info",
                "reason": "Primera asistencia en la trayectoria importada.",
            },
        )
    last_a = next((d for d in reversed(ordered) if d.get("code_normalized") == "A"), None)
    if last_a:
        events.append(
            {
                "event_type": "ultima_a",
                "fecha_sugerida": last_a["fecha_iso"],
                "status": "info",
                "reason": "Última asistencia en la trayectoria importada.",
            },
        )
    return events


def build_trajectory(
    records: list[dict[str, Any]],
    *,
    identity_key: str,
) -> dict[str, Any]:
    eligible = [r for r in records if r.get("identity_key") == identity_key and r.get("identity_resolved")]
    daily, warnings = merge_daily_records(eligible)
    totals = {
        "A": sum(1 for d in daily if d.get("code_normalized") == "A"),
        "F": sum(1 for d in daily if d.get("code_normalized") == "F"),
        "I": sum(1 for d in daily if d.get("code_normalized") == "I"),
        "V": sum(1 for d in daily if d.get("code_normalized") == "V"),
        "D": sum(1 for d in daily if d.get("code_normalized") == "D"),
    }
    return {
        "identity_key": identity_key,
        "daily": daily,
        "warnings": warnings,
        "totals": totals,
        "events": suggest_trajectory_events(daily),
        "sequence": [d.get("code_normalized") or "" for d in daily],
    }


def resolve_worker_identity(worker: dict[str, Any], match: dict[str, Any] | None) -> tuple[str | None, bool]:
    if match:
        status = str(match.get("status") or "")
        if status in {"auto", "confirmed", "manual"}:
            key = str(match.get("headcount_key") or match.get("nss") or "").strip()
            if key:
                return key, True
            hc_name = str(match.get("hc_nombre") or "").strip()
            if hc_name:
                return f"name:{hc_name}", True
    num = str(worker.get("num_empleado") or "").strip()
    if num:
        return f"num:{num}", False
    return None, False


def build_trajectories_for_workers(
    workers: list[dict[str, Any]],
    attendance_rows: list[dict[str, Any]],
    matches: dict[int, dict[str, Any] | None],
) -> dict[str, Any]:
    enriched: list[dict[str, Any]] = []
    unresolved_workers: list[int] = []
    for worker in workers:
        wid = int(worker["id"])
        identity_key, resolved = resolve_worker_identity(worker, matches.get(wid))
        if not identity_key:
            unresolved_workers.append(wid)
            continue
        if not resolved:
            unresolved_workers.append(wid)
            continue
        worker_att = [row for row in attendance_rows if int(row["worker_id"]) == wid]
        for row in worker_att:
            enriched.append(
                {
                    **row,
                    "identity_key": identity_key,
                    "identity_resolved": resolved,
                }
            )

    trajectories: dict[str, dict[str, Any]] = {}
    keys = sorted({row["identity_key"] for row in enriched if row.get("identity_resolved")})
    for key in keys:
        trajectories[key] = build_trajectory(enriched, identity_key=key)

    return {
        "trajectories": trajectories,
        "unresolved_worker_ids": unresolved_workers,
    }
