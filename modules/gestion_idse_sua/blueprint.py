"""Blueprint: hub unificado Gestión IDSE / SUA."""

from __future__ import annotations

import os
from functools import wraps

from flask import Blueprint, abort, g, redirect, render_template, url_for

from modules.gestion_idse_sua import dashboard_data
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


def _build_quick_actions(role: str) -> list[dict]:
    can_cmp = can_access_comparativo(role)
    actions: list[dict] = []
    if can_cmp:
        actions.append(
            {
                "label": "Importar nómina",
                "href": url_for("gestion_idse_sua.nominas_index"),
                "kind": "primary",
            }
        )
    actions.append(
        {
            "label": "Nuevo movimiento",
            "href": url_for("exportacion_imss.index"),
            "kind": "secondary",
        }
    )
    if can_cmp:
        actions.append(
            {
                "label": "Crear reporte mensual",
                "href": url_for("gestion_idse_sua.reportes_index"),
                "kind": "secondary",
            }
        )
    return actions


def _build_areas(role: str) -> list[dict]:
    can_cmp = can_access_comparativo(role)
    return [
        {
            "key": "nominas",
            "title": "Nóminas y análisis",
            "description": "Importación semanal y comparativo contra Headcount.",
            "icon": "trending-up",
            "tone": "blue",
            "available": can_cmp,
            "primary_href": url_for("gestion_idse_sua.nominas_index") if can_cmp else None,
            "primary_label": "Importar nómina semanal",
            "full_href": url_for("gestion_idse_sua.nominas_index") if can_cmp else None,
            "locked_reason": None
            if can_cmp
            else "Tu rol no tiene acceso al comparativo semanal.",
            "recent": dashboard_data.recent_comparativos(5) if can_cmp else [],
            "recent_empty": "Aún no hay comparativos guardados.",
            "recent_kind": "comparativos",
        },
        {
            "key": "movimientos",
            "title": "Movimientos afiliatorios",
            "description": "Captura, validación y exportación IDSE / SUA.",
            "icon": "download",
            "tone": "green",
            "available": True,
            "primary_href": url_for("exportacion_imss.index"),
            "primary_label": "Nuevo movimiento",
            "full_href": url_for("gestion_idse_sua.area_movimientos"),
            "locked_reason": None,
            "recent": dashboard_data.recent_movimientos(5),
            "recent_empty": "Aún no hay movimientos capturados.",
            "recent_kind": "movimientos",
        },
        {
            "key": "reportes",
            "title": "Reportes mensuales",
            "description": "Consolidación mensual de personal y asistencia.",
            "icon": "bar-chart",
            "tone": "blue",
            "available": can_cmp,
            "primary_href": url_for("gestion_idse_sua.reportes_index") if can_cmp else None,
            "primary_label": "Abrir / crear reporte",
            "full_href": url_for("gestion_idse_sua.reportes_index") if can_cmp else None,
            "locked_reason": None
            if can_cmp
            else "Tu rol no tiene acceso al reporte mensual.",
            "recent": dashboard_data.recent_reportes(5) if can_cmp else [],
            "recent_empty": "Aún no hay reportes mensuales guardados.",
            "recent_kind": "reportes",
        },
    ]


def _build_historial_links(role: str) -> list[dict]:
    links: list[dict] = [
        {
            "label": "Exportaciones IDSE/SUA",
            "href": url_for("exportacion_imss.index"),
            "note": "Historial dentro de Movimientos afiliatorios.",
        }
    ]
    if can_access_comparativo(role):
        links.append(
            {
                "label": "Comparativo semanal (legado)",
                "href": url_for("comparativo.index"),
                "note": "Respaldo del comparativo anterior.",
            }
        )
        links.append(
            {
                "label": "Reporte mensual legado",
                "href": url_for("comparativo.reporte_mensual_index"),
                "note": "Respaldo del reporte mensual anterior (JSON).",
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
        quick_actions=_build_quick_actions(role),
        historial_links=_build_historial_links(role),
        recent_exportaciones=dashboard_data.recent_exportaciones(5),
    )


@gestion_idse_sua_bp.get("/movimientos")
@_login_required_page
def area_movimientos():
    role = normalized_role(g.user)
    if not can_access_gestion_idse_sua(role):
        return redirect(url_for("login"))
    return redirect(url_for("exportacion_imss.index"))


from modules.gestion_idse_sua.routes_nominas import register_nominas_routes
from modules.gestion_idse_sua.routes_reportes import register_reportes_routes

register_nominas_routes(gestion_idse_sua_bp, login_required=_login_required_page)
register_reportes_routes(gestion_idse_sua_bp, login_required=_login_required_page)
