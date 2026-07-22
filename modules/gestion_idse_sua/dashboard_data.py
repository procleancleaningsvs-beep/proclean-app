"""Lectura segura de registros recientes desde fuentes legacy (solo lectura)."""

from __future__ import annotations

from typing import Any


def recent_comparativos(limit: int = 5) -> list[dict[str, Any]]:
    try:
        from modules.comparativo.comparativo_service import obtener_historial

        items = obtener_historial() or []
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for item in items[: max(0, int(limit))]:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "cliente": str(item.get("cliente") or "").strip() or "—",
                "periodo": f"{item.get('periodo_inicio') or '—'} – {item.get('periodo_fin') or '—'}",
                "estado": "Guardado",
                "fecha": str(item.get("fecha_generacion") or "").strip() or "—",
                "id": str(item.get("id") or "").strip(),
            }
        )
    return out


def recent_movimientos(limit: int = 5) -> list[dict[str, Any]]:
    try:
        from modules.exportacion_imss.exportacion_service import obtener_movimientos

        items = obtener_movimientos() or []
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for item in items[: max(0, int(limit))]:
        if not isinstance(item, dict):
            continue
        nombre = " ".join(
            str(x or "").strip()
            for x in (item.get("apellido_paterno"), item.get("apellido_materno"), item.get("nombres"))
            if str(x or "").strip()
        ) or "—"
        out.append(
            {
                "nombre": nombre,
                "tipo": str(item.get("tipo_movimiento") or "").strip() or "—",
                "fecha": str(item.get("fecha_movimiento") or item.get("fecha_captura") or "").strip() or "—",
                "estado": "Capturado",
            }
        )
    return out


def recent_exportaciones(limit: int = 5) -> list[dict[str, Any]]:
    try:
        from modules.exportacion_imss.exportacion_service import obtener_historial_exportaciones

        items = obtener_historial_exportaciones() or []
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for item in items[: max(0, int(limit))]:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "tipo": str(item.get("tipo_export") or "").strip() or "—",
                "rp": str(item.get("rp") or "").strip() or "—",
                "fecha": str(item.get("fecha_exportacion") or "").strip() or "—",
                "total": item.get("total_movimientos"),
            }
        )
    return out


def recent_reportes(limit: int = 5) -> list[dict[str, Any]]:
    try:
        import sqlite3
        from flask import current_app

        from modules.gestion_idse_sua.reportes.repository import list_recent_reports
        from modules.gestion_idse_sua.reportes.schema import ensure_gis_monthly_tables
        from modules.gestion_idse_sua.nominas.schema import ensure_gis_nominas_tables

        conn = sqlite3.connect(str(current_app.config["DATABASE"]))
        conn.row_factory = sqlite3.Row
        try:
            ensure_gis_nominas_tables(conn)
            ensure_gis_monthly_tables(conn)
            items = list_recent_reports(conn, limit=limit)
        finally:
            conn.close()
        if items:
            out: list[dict[str, Any]] = []
            for item in items:
                mes = item.get("mes")
                anio = item.get("anio")
                mes_label = f"{int(mes):02d}/{int(anio)}" if mes and anio else "—"
                out.append(
                    {
                        "cliente": str(item.get("cliente") or "").strip() or "—",
                        "mes": mes_label,
                        "semanas": item.get("week_count") or 0,
                        "personas": item.get("person_count") or 0,
                        "pendientes": item.get("pending_count") or 0,
                        "estado": str(item.get("estado") or "borrador"),
                        "id": item.get("id"),
                    }
                )
            return out
    except Exception:
        pass
    try:
        from modules.comparativo.comparativo_service import obtener_historial_reportes

        items = obtener_historial_reportes() or []
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for item in items[: max(0, int(limit))]:
        if not isinstance(item, dict):
            continue
        mes = item.get("mes")
        anio = item.get("anio")
        mes_label = f"{int(mes):02d}/{int(anio)}" if mes and anio else "—"
        out.append(
            {
                "cliente": str(item.get("cliente") or "").strip() or "—",
                "mes": mes_label,
                "estado": "Listo" if item.get("completo") else "Guardado",
            }
        )
    return out
