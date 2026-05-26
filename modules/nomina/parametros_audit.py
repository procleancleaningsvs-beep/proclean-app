"""Trazabilidad operativa v1 para parámetros base (editable_json).

Pensado para migrar a tabla formal en microfase posterior.
"""
from __future__ import annotations

from typing import Any


def add_parametro_audit_event(
    editable_json: dict[str, Any],
    *,
    action: str,
    user_id: int | None,
    now_iso: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ed = dict(editable_json or {})
    events = list(ed.get("audit_events") or [])
    events.append(
        {
            "action": action,
            "user_id": user_id,
            "at": now_iso,
            "detail": detail or {},
        }
    )
    ed["audit_events"] = events[-50:]
    return ed


def get_parametro_audit_events(editable_json: dict[str, Any] | None) -> list[dict[str, Any]]:
    return list((editable_json or {}).get("audit_events") or [])
