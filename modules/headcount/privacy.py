from __future__ import annotations

from typing import Any


def should_mask_sensitive_data(role: str) -> bool:
    return role in {"usuario", "coordinador"}


def mask_nss(value: Any) -> str:
    s = str(value or "").strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) < 4:
        return "***" if digits else ""
    return f"***{digits[-4:]}"


def mask_curp(value: Any) -> str:
    s = str(value or "").strip().upper()
    if len(s) < 6:
        return "***" if s else ""
    return f"{s[:4]}***{s[-2:]}" if len(s) >= 6 else "***"


def mask_registro_for_display(registro: dict[str, Any], *, role: str) -> dict[str, Any]:
    if not should_mask_sensitive_data(role):
        return registro
    out = dict(registro)
    for key in ("nss", "nss_sua_original", "nss_headcount", "nss_normalizado", "curp", "curp_headcount"):
        if key in out and out[key]:
            if "curp" in key:
                out[key] = mask_curp(out[key])
            else:
                out[key] = mask_nss(out[key])
    return out
