from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any


def normalize_spaces(value: str) -> str:
    return " ".join((value or "").split())


def normalize_upper(value: Any) -> str:
    return normalize_spaces(str(value or "").strip().upper())


def normalize_name(value: Any) -> str:
    raw = normalize_upper(value)
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = "".join(ch if (ch.isalnum() or ch == " ") else " " for ch in raw)
    return " ".join(raw.split())


def normalize_planta(value: Any) -> str:
    return normalize_name(value)


def normalize_header(value: Any) -> str:
    return normalize_spaces(str(value or "").strip().upper())


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
