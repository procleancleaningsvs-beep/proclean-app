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
