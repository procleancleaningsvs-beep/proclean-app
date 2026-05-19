"""Configuración del módulo Headcount (patrones de auditoría SUA, sin duplicar env de OneDrive)."""
from __future__ import annotations

import json
import os
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

# Reutiliza la misma variable que comparativo/nómina (headcount_service).
HEADCOUNT_ONEDRIVE_URL_ENV = "HEADCOUNT_ONEDRIVE_URL"

_DEFAULT_PATRON_ALIASES = ("RAFAEL", "RAFAEL ALEJANDRO")
_ALIASES_FILE = "headcount_patron_aliases.json"


def _data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "./data"))


@lru_cache(maxsize=1)
def get_patron_auditoria_aliases() -> tuple[str, ...]:
    """Aliases de PATRON para auditoría SUA (env, JSON en DATA_DIR o defaults)."""
    env_raw = (os.environ.get("HEADCOUNT_PATRON_ALIASES") or "").strip()
    if env_raw:
        parts = [p.strip() for p in env_raw.replace(";", ",").split(",") if p.strip()]
        if parts:
            return tuple(parts)

    path = _data_dir() / _ALIASES_FILE
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return tuple(str(x).strip() for x in data if str(x).strip())
            if isinstance(data, dict):
                raw = data.get("aliases") or data.get("patrones") or []
                if isinstance(raw, list):
                    return tuple(str(x).strip() for x in raw if str(x).strip())
        except (json.JSONDecodeError, OSError):
            pass

    return _DEFAULT_PATRON_ALIASES


def normalize_patron(value: Any) -> str:
    s = str(value or "").strip().upper()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def patron_matches_auditoria(patron: Any) -> bool:
    """True si el patrón del Headcount aplica para auditoría SUA (RAFAEL y aliases)."""
    n = normalize_patron(patron)
    if not n:
        return False
    for alias in get_patron_auditoria_aliases():
        a = normalize_patron(alias)
        if not a:
            continue
        if n == a:
            return True
        if len(n) > len(a) and n.startswith(a + " "):
            return True
    return False
