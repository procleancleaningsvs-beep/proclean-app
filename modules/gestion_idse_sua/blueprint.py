"""Blueprint: hub unificado Gestión IDSE / SUA (shell Phase 1)."""

from __future__ import annotations

import os
from functools import wraps

from flask import Blueprint, g, redirect, render_template, url_for

from modules.roles_access import (
    can_access_comparativo,
    can_access_gestion_idse_sua,
    normalized_role,
)

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_TEMPLATE_DIR = os.path.join(_BASE, "templates", "gestion_idse_sua")

gestion_idse_sua_bp = Blueprint(
    "gestion_idse_sua",
    __name__,
    url_prefix="/gestion-idse-sua",
    template_folder=_TEMPLATE_DIR,
)


def _login_required_page(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def _build_areas(role: str) -> list[dict]:
    can_cmp = can_access_comparativo(role)
    # exportacion_imss solo exige sesión; no ampliar permisos aquí.
    return [
        {
            "key": "nominas",
            "title": "Nóminas y análisis",
            "description": "Importación semanal, aliases y comparativo contra Headcount.",
            "icon": "trending-up",
            "available": can_cmp,
            "href": url_for("comparativo.index") if can_cmp else None,
            "locked_reason": None
            if can_cmp
            else "Tu rol no tiene acceso al comparativo semanal. Solicita acceso o usa otra cuenta autorizada.",
        },
        {
            "key": "movimientos",
            "title": "Movimientos afiliatorios",
            "description": "Captura, validación y exportación de movimientos para IDSE y SUA.",
            "icon": "download",
            "available": True,
            "href": url_for("exportacion_imss.index"),
            "locked_reason": None,
        },
        {
            "key": "reportes",
            "title": "Reportes mensuales",
            "description": "Consolidación mensual de personal fijo y rotativo.",
            "icon": "bar-chart",
            "available": can_cmp,
            "href": url_for("comparativo.reporte_mensual_index") if can_cmp else None,
            "locked_reason": None
            if can_cmp
            else "Tu rol no tiene acceso al reporte mensual. Solicita acceso o usa otra cuenta autorizada.",
        },
    ]


def _build_historial_links(role: str) -> list[dict]:
    links: list[dict] = []
    links.append(
        {
            "label": "Historial de exportaciones IDSE/SUA",
            "href": url_for("exportacion_imss.index"),
            "note": "Disponible dentro de Movimientos afiliatorios.",
        }
    )
    if can_access_comparativo(role):
        links.append(
            {
                "label": "Historial de comparativos semanales",
                "href": url_for("comparativo.index"),
                "note": "Disponible dentro de Nóminas y análisis.",
            }
        )
        links.append(
            {
                "label": "Reportes mensuales guardados",
                "href": url_for("comparativo.reporte_mensual_index"),
                "note": "Disponible dentro de Reportes mensuales.",
            }
        )
    return links


@gestion_idse_sua_bp.get("/")
@_login_required_page
def hub():
    role = normalized_role(g.user)
    if not can_access_gestion_idse_sua(role):
        return redirect(url_for("login"))
    return render_template(
        "gestion_idse_sua/hub.html",
        areas=_build_areas(role),
        historial_links=_build_historial_links(role),
    )
