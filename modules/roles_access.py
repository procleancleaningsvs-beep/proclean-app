"""Matriz de acceso por rol (Nóminas, Finiquitos, Facturación, CheckID, etc.)."""
from __future__ import annotations

from typing import Any

# Roles válidos en BD (SQLite CHECK tras migración).
VALID_USER_ROLES = frozenset({"admin", "nomina", "coordinador", "usuario", "cobranza"})

NOMINA_MODULE_ROLES = frozenset({"admin", "nomina", "coordinador"})
NOMINA_DASHBOARD_ROLES = frozenset({"admin", "nomina"})
FINIQUITO_ROLES = frozenset({"admin", "nomina", "usuario"})
FACTURACION_ROLES = frozenset({"admin", "cobranza"})
CHECKID_ROLES = frozenset({"admin", "nomina", "coordinador", "usuario"})


def normalized_role(user: Any) -> str:
    if not user:
        return ""
    try:
        return str(user.get("role") if isinstance(user, dict) else user["role"] or "").strip().lower()
    except (TypeError, KeyError, IndexError, AttributeError):
        return ""


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
