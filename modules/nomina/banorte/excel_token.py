"""Signed short-lived tokens for Excel inspect/preview/prepare (no PII)."""

from __future__ import annotations

import secrets
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

PURPOSE = "banorte_excel_nomina"
TTL_SECONDS = 15 * 60


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt=PURPOSE)


def issue_excel_token(
    secret_key: str,
    *,
    user: str,
    sha256: str,
    size: int,
) -> str:
    payload = {
        "purpose": PURPOSE,
        "user": user,
        "sha256": sha256,
        "size": int(size),
        "nonce": secrets.token_urlsafe(8),
    }
    return _serializer(secret_key).dumps(payload)


def verify_excel_token(
    secret_key: str,
    token: str,
    *,
    user: str,
    sha256: str,
    size: int,
) -> dict[str, Any]:
    try:
        payload = _serializer(secret_key).loads(token, max_age=TTL_SECONDS)
    except SignatureExpired as exc:
        raise ValueError("excel_token_expired") from exc
    except BadSignature as exc:
        raise ValueError("excel_token_invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("excel_token_invalid")
    if payload.get("purpose") != PURPOSE:
        raise ValueError("excel_token_invalid")
    if payload.get("user") != user:
        raise ValueError("excel_token_user_mismatch")
    if payload.get("sha256") != sha256:
        raise ValueError("excel_token_sha_mismatch")
    if int(payload.get("size") or -1) != int(size):
        raise ValueError("excel_token_size_mismatch")
    return payload
