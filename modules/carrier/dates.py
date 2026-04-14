"""Fecha/hora en zona de la app (misma lógica que `app.now_in_app_tz` / `now_iso`)."""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo


def app_timezone() -> ZoneInfo:
    name = (os.environ.get("APP_TIMEZONE") or "America/Mexico_City").strip() or "America/Mexico_City"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("America/Mexico_City")


def now_in_app_tz() -> datetime:
    return datetime.now(app_timezone())


def now_iso() -> str:
    return now_in_app_tz().strftime("%Y-%m-%d %H:%M:%S")


def today_in_app_tz() -> date:
    return now_in_app_tz().date()
