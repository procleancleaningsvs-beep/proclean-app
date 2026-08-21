from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

_CURSOR_TTL_SECONDS = 3600
_SORT_ALLOWLIST = frozenset({"employee_asc", "employee_desc", "name_asc"})


class CatalogSearchCursorError(ValueError):
    pass


def _signing_key(secret_key: str) -> bytes:
    material = str(secret_key or "")
    if not material:
        raise CatalogSearchCursorError("secret_missing")
    return hashlib.sha256(f"banorte-catalog-sidebar-cursor:{material}".encode("utf-8")).digest()


def issue_catalog_search_cursor(
    *,
    secret_key: str,
    version_id: int,
    offset: int,
    sort: str,
    limit: int,
) -> str:
    if sort not in _SORT_ALLOWLIST:
        raise CatalogSearchCursorError("sort_invalid")
    payload = {
        "v": int(version_id),
        "o": int(offset),
        "s": sort,
        "l": int(limit),
        "e": int(time.time()) + _CURSOR_TTL_SECONDS,
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(_signing_key(secret_key), body, hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(json.dumps({"p": payload, "sig": sig}).encode("utf-8")).decode("ascii")
    return token.rstrip("=")


def parse_catalog_search_cursor(*, secret_key: str, cursor: str) -> dict[str, Any]:
    if not isinstance(cursor, str) or not cursor.strip():
        raise CatalogSearchCursorError("cursor_missing")
    padded = cursor.strip()
    pad = (-len(padded)) % 4
    if pad:
        padded += "=" * pad
    try:
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        payload = decoded["p"]
        sig = decoded["sig"]
    except Exception as exc:
        raise CatalogSearchCursorError("cursor_invalid") from exc
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    expected = hmac.new(_signing_key(secret_key), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(sig)):
        raise CatalogSearchCursorError("cursor_tampered")
    if int(payload.get("e") or 0) < int(time.time()):
        raise CatalogSearchCursorError("cursor_expired")
    sort = str(payload.get("s") or "")
    if sort not in _SORT_ALLOWLIST:
        raise CatalogSearchCursorError("sort_invalid")
    return {
        "version_id": int(payload["v"]),
        "offset": int(payload["o"]),
        "sort": sort,
        "limit": int(payload["l"]),
    }
