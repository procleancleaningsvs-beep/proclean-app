from __future__ import annotations

import os
from functools import wraps

from flask import Blueprint, g, jsonify, redirect, render_template, request, Response, flash, url_for

from modules.exportacion_imss.exportacion_service import (
    MOVIMIENTOS_DIR,
    autocompletar_desde_headcount,
    generar_txt_idse,
    generar_txt_sua,
    guardar_movimiento,
    obtener_movimientos,
)

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_TEMPLATE_DIR = os.path.join(_BASE, "templates", "exportacion_imss")

exportacion_imss_bp = Blueprint(
    "exportacion_imss",
    __name__,
    url_prefix="/exportacion-imss",
    template_folder=_TEMPLATE_DIR,
)


def _login_required_page(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def _parse_ids() -> list[str]:
    ids = request.args.getlist("ids")
    if not ids:
        raw = (request.args.get("ids") or "").strip()
        if raw:
            ids = [v.strip() for v in raw.split(",")]
    return [x for x in ids if x]


@exportacion_imss_bp.get("/")
@_login_required_page
def index():
    tipo = (request.args.get("tipo") or "").strip().upper() or None
    cliente = (request.args.get("cliente") or "").strip() or None
    movimientos = obtener_movimientos(tipo=tipo, cliente=cliente)
    clientes = sorted({m.get("cliente", "") for m in obtener_movimientos() if m.get("cliente")})
    agrupados = {"ALTA": [], "BAJA": [], "MODIFICACION": []}
    for m in movimientos:
        agrupados.setdefault(m.get("tipo_movimiento", "MODIFICACION"), []).append(m)
    return render_template(
        "exportacion_imss/index.html",
        movimientos=movimientos,
        agrupados=agrupados,
        clientes=clientes,
        filtro_tipo=tipo or "",
        filtro_cliente=cliente or "",
    )


@exportacion_imss_bp.get("/nuevo")
@_login_required_page
def nuevo_get():
    tipo = (request.args.get("tipo") or "ALTA").strip().upper()
    cliente = (request.args.get("cliente") or "").strip()
    nss = (request.args.get("nss") or "").strip()
    prefills = {
        "tipo_movimiento": tipo if tipo in {"ALTA", "BAJA", "MODIFICACION"} else "ALTA",
        "cliente": cliente,
        "nss": nss,
    }
    if nss:
        data = autocompletar_desde_headcount(nss=nss)
        if data:
            prefills.update(data)
    return render_template("exportacion_imss/captura.html", data=prefills)


@exportacion_imss_bp.post("/nuevo")
@_login_required_page
def nuevo_post():
    form = request.form
    payload = {
        "tipo_movimiento": form.get("tipo_movimiento"),
        "cliente": form.get("cliente"),
        "rp": form.get("rp"),
        "nss": form.get("nss"),
        "rfc": form.get("rfc"),
        "curp": form.get("curp"),
        "apellido_paterno": form.get("apellido_paterno"),
        "apellido_materno": form.get("apellido_materno"),
        "nombres": form.get("nombres"),
        "sbc": form.get("sbc"),
        "fecha_movimiento": form.get("fecha_movimiento"),
        "clave_ubicacion": form.get("clave_ubicacion"),
        "num_credito": form.get("num_credito"),
        "fecha_inicio_descuento": form.get("fecha_inicio_descuento"),
        "tipo_descuento": form.get("tipo_descuento"),
        "valor_descuento": form.get("valor_descuento"),
    }
    try:
        guardar_movimiento(payload)
        flash("Movimiento IMSS guardado correctamente.", "success")
    except Exception as exc:
        flash(str(exc), "error")
        return redirect(url_for("exportacion_imss.nuevo_get"))
    return redirect(url_for("exportacion_imss.index"))


@exportacion_imss_bp.get("/exportar-idse")
@_login_required_page
def exportar_idse():
    ids = _parse_ids()
    tipo = (request.args.get("tipo_movimiento") or "").strip().upper()
    if not ids:
        flash("Selecciona al menos un movimiento para exportar IDSE.", "error")
        return redirect(url_for("exportacion_imss.index"))
    txt = generar_txt_idse(ids, tipo)
    filename = f"movimientos_{tipo or 'IDSE'}.txt"
    return Response(
        txt,
        mimetype="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@exportacion_imss_bp.get("/exportar-sua")
@_login_required_page
def exportar_sua():
    ids = _parse_ids()
    if not ids:
        flash("Selecciona al menos un movimiento para exportar SUA.", "error")
        return redirect(url_for("exportacion_imss.index"))
    txt = generar_txt_sua(ids)
    return Response(
        txt,
        mimetype="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="movimientos_sua.txt"'},
    )


@exportacion_imss_bp.delete("/<movimiento_id>")
@_login_required_page
def eliminar_movimiento(movimiento_id: str):
    path = os.path.join(MOVIMIENTOS_DIR, f"{movimiento_id}.json")
    if os.path.exists(path):
        os.remove(path)
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Movimiento no encontrado"}), 404
