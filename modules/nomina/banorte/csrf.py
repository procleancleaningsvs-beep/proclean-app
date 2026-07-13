from __future__ import annotations

import hmac
import secrets
from typing import Any

from flask import current_app, g, request, session

_SESSION_KEY = "banorte_csrf_token"


def issue_csrf_token() -> str:
    token = session.get(_SESSION_KEY)
    if not isinstance(token, str) or len(token) < 32:
        # Bind randomness to app secret material without inventing custom crypto.
        token = secrets.token_urlsafe(32)
        # Touch SECRET_KEY so misconfigured apps without secret fail loudly.
        _ = current_app.config["SECRET_KEY"]
        session[_SESSION_KEY] = token
        session.modified = True
    return token


def _extract_token(payload: dict[str, Any] | None = None) -> str:
    header = request.headers.get("X-CSRF-Token") or request.headers.get("X-CSRFToken") or ""
    if header.strip():
        return header.strip()
    form_token = (request.form.get("csrf_token") or "").strip()
    if form_token:
        return form_token
    if payload and isinstance(payload.get("csrf_token"), str):
        return payload["csrf_token"].strip()
    if request.is_json:
        data = request.get_json(silent=True) or {}
        if isinstance(data.get("csrf_token"), str):
            return data["csrf_token"].strip()
    return ""


def validate_csrf_token(payload: dict[str, Any] | None = None) -> bool:
    expected = session.get(_SESSION_KEY)
    provided = _extract_token(payload)
    if not isinstance(expected, str) or not expected or not provided:
        return False
    # Ensure SECRET_KEY exists (session integrity depends on it).
    _ = current_app.config["SECRET_KEY"]
    return hmac.compare_digest(expected, provided)


def require_csrf(payload: dict[str, Any] | None = None) -> None:
    if not validate_csrf_token(payload):
        from flask import abort

        abort(403)
