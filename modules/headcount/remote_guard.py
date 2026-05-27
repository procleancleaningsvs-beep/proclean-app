"""Evita descargas OneDrive de Headcount durante GET de páginas."""
from __future__ import annotations

import logging

from services.perf_logging import perf_headcount_log

_logger = logging.getLogger(__name__)


def assert_remote_headcount_allowed(caller: str = "") -> None:
    """Bloquea carga remota si hay un request Flask GET activo."""
    try:
        from flask import has_request_context, request
    except ImportError:
        return
    if not has_request_context():
        return
    if request.method != "GET":
        return
    route = request.path or "unknown"
    _logger.warning(
        "[PERF] WARNING headcount remote_load_in_get route=%s caller=%s",
        route,
        caller or "unknown",
    )
    perf_headcount_log("remote_load_blocked", route=route, caller=caller or "unknown")
    raise RuntimeError(
        f"Headcount remoto bloqueado en GET ({route}). Use snapshot local."
    )
