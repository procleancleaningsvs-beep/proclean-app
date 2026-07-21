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

HEADCOUNT_MODULE_ROLES = frozenset({"admin", "nomina", "usuario", "coordinador"})
HEADCOUNT_AUDITORIA_ROLES = frozenset({"admin", "nomina"})
HEADCOUNT_CLIENTE_ROLES = frozenset({"admin", "nomina", "usuario", "coordinador"})
HEADCOUNT_DESGLOSE_ROLES = frozenset({"admin", "nomina"})


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


def can_access_headcount_module(role: str) -> bool:
    return role in HEADCOUNT_MODULE_ROLES


def can_access_headcount_auditoria(role: str) -> bool:
    return role in HEADCOUNT_AUDITORIA_ROLES


def can_access_headcount_cliente(role: str) -> bool:
    return role in HEADCOUNT_CLIENTE_ROLES


def can_access_headcount_desglose(role: str) -> bool:
    return role in HEADCOUNT_DESGLOSE_ROLES


def can_delete_headcount_audit(role: str) -> bool:
    return role == "admin"


def nav_show_headcount_module(role: str) -> bool:
    return role in HEADCOUNT_MODULE_ROLES


def nav_show_headcount_auditoria(role: str) -> bool:
    return role in HEADCOUNT_AUDITORIA_ROLES


def nav_show_headcount_cliente(role: str) -> bool:
    return role in HEADCOUNT_CLIENTE_ROLES


def nav_show_headcount_conteo(role: str) -> bool:
    return role in HEADCOUNT_CLIENTE_ROLES


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


def can_access_gestion_idse_sua(role: str) -> bool:
    """Shell del hub: cualquier rol autenticado válido (matriz definitiva en fase posterior)."""
    return role in VALID_USER_ROLES


def nav_show_gestion_idse_sua(role: str) -> bool:
    return can_access_gestion_idse_sua(role)


def nav_show_imss_exportacion_link(role: str) -> bool:
    """Acceso sidebar al módulo exportacion_imss; oculto tras unificación visual (ruta viva)."""
    return False


def nav_show_comparativo_export_links(role: str) -> bool:
    """Accesos sidebar Comparativo semanal / Reporte mensual; ocultos tras unificación (rutas vivas)."""
    return False


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
