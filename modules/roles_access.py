"""Matriz de acceso por rol (Nóminas, Finiquitos, Facturación, CheckID, etc.)."""
from __future__ import annotations

import unicodedata
from typing import Any

# Roles válidos en BD (SQLite CHECK tras migración).
VALID_USER_ROLES = frozenset({"admin", "nomina", "coordinador", "usuario", "cobranza"})

NOMINA_MODULE_ROLES = frozenset({"admin", "nomina", "coordinador"})
NOMINA_DASHBOARD_ROLES = frozenset({"admin", "nomina"})
FINIQUITO_ROLES = frozenset({"admin", "nomina", "usuario"})
FACTURACION_ROLES = frozenset({"admin", "cobranza"})
CHECKID_ROLES = frozenset({"admin", "nomina", "coordinador", "usuario"})
IMSS_MOVIMIENTOS_ROLES = frozenset({"admin", "coordinador", "cobranza", "usuario"})
CARRIER_VITROFLEX_ROLES = frozenset({"admin", "usuario"})
COMPARATIVO_ROLES = frozenset({"admin", "usuario"})


def normalized_role(user: Any) -> str:
    if not user:
        return ""
    try:
        raw = user.get("role") if isinstance(user, dict) else user["role"]
    except (TypeError, KeyError, IndexError, AttributeError):
        return ""
    role = str(raw or "").strip().lower()
    role = unicodedata.normalize("NFKD", role)
    role = "".join(ch for ch in role if not unicodedata.combining(ch))
    return role


def can_access_nomina_module(role: str) -> bool:
    return role in NOMINA_MODULE_ROLES


def can_access_nomina_dashboard(role: str) -> bool:
    return role in NOMINA_DASHBOARD_ROLES


def can_access_finiquitos(role: str) -> bool:
    return role in FINIQUITO_ROLES


def can_access_facturacion(role: str) -> bool:
    return role in FACTURACION_ROLES


def can_access_checkid(role: str) -> bool:
    return role in CHECKID_ROLES


def can_access_imss_movimientos(role: str) -> bool:
    return role in IMSS_MOVIMIENTOS_ROLES


def can_access_carrier_vitroflex(role: str) -> bool:
    return role in CARRIER_VITROFLEX_ROLES


def can_access_comparativo(role: str) -> bool:
    return role in COMPARATIVO_ROLES


def nav_show_administration(role: str) -> bool:
    return role == "admin"


def nav_show_nomina_module(role: str) -> bool:
    return role in NOMINA_MODULE_ROLES


def nav_show_nomina_dashboard(role: str) -> bool:
    return role in NOMINA_DASHBOARD_ROLES


def nav_show_nomina_hub(role: str) -> bool:
    return role in NOMINA_MODULE_ROLES


def nav_show_finiquitos(role: str) -> bool:
    return role in FINIQUITO_ROLES


def nav_show_facturacion(role: str) -> bool:
    return role in FACTURACION_ROLES


def nav_show_imss_movimientos(role: str) -> bool:
    return role in IMSS_MOVIMIENTOS_ROLES


def nav_show_carrier_vitroflex(role: str) -> bool:
    return role in CARRIER_VITROFLEX_ROLES


def nav_show_checkid(role: str) -> bool:
    return role in CHECKID_ROLES


def nav_show_comparativo(role: str) -> bool:
    return role in COMPARATIVO_ROLES


def login_home_endpoint(role: str) -> str:
    if role == "admin":
        return "dashboard"
    if role == "nomina":
        return "nomina.index"
    if role == "coordinador":
        return "nomina.master_hub"
    if role == "cobranza":
        return "facturacion.dashboard"
    return "home_usuario"
