"""Request-level performance logging and block timing helpers."""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Iterator

from flask import Flask, g, request

logger = logging.getLogger(__name__)

_PERF_ENV = "PERF_LOG_ENABLED"


def perf_log_enabled() -> bool:
    return os.environ.get(_PERF_ENV, "").strip() in {"1", "true", "True", "yes", "YES"}


def _debug_mode_label(app: Flask) -> str:
    return "debug" if bool(app.debug or app.config.get("DEBUG")) else "production"


@contextmanager
def perf_span(name: str) -> Iterator[None]:
    """Measure a code block when PERF_LOG_ENABLED=1."""
    if not perf_log_enabled():
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        logger.info("[PERF] span=%s duration_ms=%d", name, elapsed_ms)


def register_perf_hooks(app: Flask) -> None:
    """Register before/after request hooks for route timing."""

    @app.before_request
    def _perf_before_request() -> None:
        if not perf_log_enabled():
            return
        g._perf_started_at = time.perf_counter()

    @app.after_request
    def _perf_after_request(response):
        if not perf_log_enabled():
            return response
        started = getattr(g, "_perf_started_at", None)
        if started is None:
            return response
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        user = getattr(g, "user", None)
        role = None
        if user is not None:
            try:
                role = user["role"]
            except (TypeError, KeyError):
                role = getattr(user, "role", None)
        logger.info(
            "[PERF] %s %s endpoint=%s status=%s duration_ms=%d user_role=%s mode=%s",
            request.method,
            request.path,
            request.endpoint or "-",
            response.status_code,
            elapsed_ms,
            role or "-",
            _debug_mode_label(app),
        )
        return response
