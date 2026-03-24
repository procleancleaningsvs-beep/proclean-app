"""
Caché en memoria de respuestas CheckID por término normalizado (RFC/CURP).
Evita repetir la misma consulta en ventana corta (TTL). Thread-safe.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

_lock = threading.Lock()
_store: dict[str, tuple[float, dict[str, Any]]] = {}
_MAX_KEYS = 500


def _ttl_seconds() -> float:
    return max(5.0, float(os.environ.get("CHECKID_CACHE_TTL_SECONDS", "60")))


def get_cached_busqueda(cache_key: str) -> dict[str, Any] | None:
    """Devuelve copia del dict cacheado o None si no existe o expiró."""
    if not cache_key:
        return None
    now = time.monotonic()
    with _lock:
        item = _store.get(cache_key)
        if not item:
            return None
        ts, payload = item
        if now - ts > _ttl_seconds():
            del _store[cache_key]
            return None
        return dict(payload)


def set_cached_busqueda(cache_key: str, payload: dict[str, Any]) -> None:
    """Guarda copia del resultado de client.buscar()."""
    if not cache_key:
        return
    now = time.monotonic()
    with _lock:
        if len(_store) >= _MAX_KEYS:
            # Evicción simple: eliminar la entrada más antigua por timestamp
            oldest_k = min(_store.keys(), key=lambda k: _store[k][0])
            del _store[oldest_k]
        _store[cache_key] = (now, dict(payload))
