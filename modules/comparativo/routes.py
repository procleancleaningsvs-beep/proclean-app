from __future__ import annotations

import io
import os
from functools import wraps

from flask import Blueprint, g, jsonify, redirect, render_template, request, send_file, flash, url_for
from openpyxl import Workbook

from modules.comparativo.comparativo_service import (
    generar_reporte_mensual,
    guardar_comparativo_semanal,
    obtener_historial,
    parsear_nomina,
    comparar_listas,
)
from modules.comparativo.headcount_service import actualizar_headcount, obtener_activos, obtener_metadata_headcount

DATA_DIR = os.environ.get("DATA_DIR", "./data")
COMPARATIVOS_DIR = os.path.join(DATA_DIR, "comparativos")
_BASE = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_TEMPLATE_DIR = os.path.join(_BASE, "templates", "comparativo")

comparativo_bp = Blueprint(
    "comparativo",
    __name__,
    url_prefix="/comparativo",
    template_folder=_TEMPLATE_DIR,
)


def _login_required_page(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def _ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(COMPARATIVOS_DIR, exist_ok=True)
    try:
        os.makedirs("/app/data/comparativos", exist_ok=True)
    except OSError:
        pass


def _headcount_meta() -> dict:
    activos = obtener_activos()
    clientes = sorted({a.get("cliente", "") for a in activos if a.get("cliente")})
    meta = obtener_metadata_headcount()
    return {
        "exists": bool(meta.get("url_configurada")),
        "total_activos": len(activos),
        "fecha_actualizacion": meta.get("fecha_actualizacion"),
        "clientes_detectados": clientes,
    }


@comparativo_bp.get("/")
@_login_required_page
def index():
    _ensure_dirs()
    try:
        meta = _headcount_meta()
    except Exception:
        meta = {"exists": False, "total_activos": 0, "fecha_actualizacion": None, "clientes_detectados": []}
    historial = obtener_historial()[:10]
    clientes = meta["clientes_detectados"]
    return render_template(
        "comparativo/index.html",
        clientes=clientes,
        historial=historial,
        headcount=meta,
    )


@comparativo_bp.post("/actualizar-headcount")
@_login_required_page
def actualizar_headcount_route():
    _ensure_dirs()
    try:
        result = actualizar_headcount()
        flash(result.get("message", "Caché de headcount invalidado."), "success")
    except Exception as exc:
        flash(str(exc), "error")
    return redirect(url_for("comparativo.index"))


@comparativo_bp.post("/semanal")
@_login_required_page
def comparativo_semanal():
    _ensure_dirs()
    nomina_file = request.files.get("nomina_file")
    cliente = (request.form.get("cliente") or "").strip()
    periodo_inicio = (request.form.get("periodo_inicio") or "").strip()
    periodo_fin = (request.form.get("periodo_fin") or "").strip()
    if not nomina_file or not cliente or not periodo_inicio or not periodo_fin:
        flash("Faltan datos para generar el comparativo semanal.", "error")
        return redirect(url_for("comparativo.index"))

    try:
        lista_nomina = parsear_nomina(nomina_file)
        activos_cliente = obtener_activos(cliente=cliente)
        lista_activos = [item.get("nombre_completo", "") for item in activos_cliente]
        resultado = comparar_listas(lista_nomina, lista_activos)
        comparativo = guardar_comparativo_semanal(
            resultado=resultado,
            cliente=cliente,
            periodo_inicio=periodo_inicio,
            periodo_fin=periodo_fin,
            fecha_baja_asumida=periodo_fin,
        )
        flash("Comparativo semanal generado correctamente.", "success")
        return render_template("comparativo/resultado_semanal.html", comparativo=comparativo, resultado=resultado)
    except Exception as exc:
        flash(str(exc), "error")
        return redirect(url_for("comparativo.index"))


@comparativo_bp.get("/mensual")
@_login_required_page
def reporte_mensual():
    _ensure_dirs()
    cliente = (request.args.get("cliente") or "").strip()
    mes_raw = (request.args.get("mes") or "").strip()
    anio_raw = (request.args.get("anio") or "").strip()
    if not cliente or not mes_raw or not anio_raw:
        flash("Selecciona cliente, mes y año para generar el reporte mensual.", "error")
        return redirect(url_for("comparativo.index"))
    try:
        reporte = generar_reporte_mensual(cliente=cliente, mes=int(mes_raw), anio=int(anio_raw))
        return render_template("comparativo/resultado_mensual.html", reporte=reporte)
    except Exception as exc:
        flash(str(exc), "error")
        return redirect(url_for("comparativo.index"))


@comparativo_bp.get("/exportar-semanal/<comparativo_id>")
@_login_required_page
def exportar_semanal(comparativo_id: str):
    _ensure_dirs()
    historial = obtener_historial()
    comp = next((item for item in historial if str(item.get("id")) == str(comparativo_id)), None)
    if not comp:
        flash("Comparativo no encontrado.", "error")
        return redirect(url_for("comparativo.index"))

    wb = Workbook()
    ws_altas = wb.active
    ws_altas.title = "Altas"
    ws_altas.append(["Nombre", "Fecha Alta"])
    for nombre in comp.get("altas", []):
        ws_altas.append([nombre, comp.get("periodo_inicio", "")])

    ws_bajas = wb.create_sheet("Bajas")
    ws_bajas.append(["Nombre", "Fecha Baja"])
    for nombre in comp.get("bajas", []):
        ws_bajas.append([nombre, comp.get("fecha_baja_asumida", "")])

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    filename = f"comparativo_semanal_{comparativo_id}.xlsx"
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


@comparativo_bp.get("/exportar-mensual")
@_login_required_page
def exportar_mensual():
    _ensure_dirs()
    cliente = (request.args.get("cliente") or "").strip()
    mes_raw = (request.args.get("mes") or "").strip()
    anio_raw = (request.args.get("anio") or "").strip()
    if not cliente or not mes_raw or not anio_raw:
        flash("Faltan parámetros para exportar el reporte mensual.", "error")
        return redirect(url_for("comparativo.index"))

    try:
        reporte = generar_reporte_mensual(cliente=cliente, mes=int(mes_raw), anio=int(anio_raw))
    except Exception as exc:
        flash(str(exc), "error")
        return redirect(url_for("comparativo.index"))

    wb = Workbook()
    ws_resumen = wb.active
    ws_resumen.title = "Resumen"
    ws_resumen.append(["Cliente", reporte.get("cliente", "")])
    ws_resumen.append(["Mes", reporte.get("mes", "")])
    ws_resumen.append(["Año", reporte.get("anio", "")])
    ws_resumen.append(["Total altas", len(reporte.get("altas_mes", []))])
    ws_resumen.append(["Total bajas", len(reporte.get("bajas_mes", []))])
    ws_resumen.append([])
    ws_resumen.append(["Semanas del mes"])
    ws_resumen.append(["Periodo inicio", "Periodo fin", "Altas", "Bajas"])
    for semana in reporte.get("semanas", []):
        ws_resumen.append(
            [
                semana.get("periodo_inicio", ""),
                semana.get("periodo_fin", ""),
                len(semana.get("altas", [])),
                len(semana.get("bajas", [])),
            ]
        )

    ws_altas = wb.create_sheet("Altas mes")
    ws_altas.append(["Nombre", "Fecha Alta"])
    for item in reporte.get("altas_mes", []):
        ws_altas.append([item.get("nombre", ""), item.get("fecha_alta", "")])

    ws_bajas = wb.create_sheet("Bajas mes")
    ws_bajas.append(["Nombre", "Fecha Baja"])
    for item in reporte.get("bajas_mes", []):
        ws_bajas.append([item.get("nombre", ""), item.get("fecha_baja", "")])

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    filename = f"comparativo_mensual_{cliente}_{mes_raw}_{anio_raw}.xlsx"
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )
