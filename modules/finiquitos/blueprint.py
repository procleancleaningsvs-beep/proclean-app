"""Rutas Flask: Finiquitos y liquidación comparativa."""

from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from functools import wraps

from flask import Blueprint, Response, current_app, g, jsonify, redirect, render_template, request, url_for

from modules.finiquitos import config as fincfg
from modules.finiquitos.calc import calcular_finiquito, prima_antiguedad_aplica_separacion_voluntaria
from modules.finiquitos.export_docx import build_finiquito_placeholders, render_finiquito_docx, render_finiquito_pdf
from modules.finiquitos.graph_excel import buscar_fecha_ingreso_excel
from modules.finiquitos.liquidacion import calcular_liquidacion_comparativa
from modules.finiquitos.numero_letra import importe_mxn_a_letra
from services.finiquitos_history import (
    ensure_finiquitos_tables,
    insert_finiquito_history,
    insert_liquidacion_history,
    list_finiquito_history,
    list_liquidacion_history,
)

_BASE = Path(__file__).resolve().parent.parent.parent
_TEMPLATE_DIR = _BASE / "templates" / "finiquitos"
_ONEDRIVE_URL_ENV = "FINIQUITOS_ONEDRIVE_SHARED_URL"

finiquitos_bp = Blueprint(
    "finiquitos",
    __name__,
    url_prefix="/finiquitos",
    template_folder=str(_TEMPLATE_DIR),
)


def _login_required_json():
    if g.user is None:
        return jsonify({"ok": False, "error": "No autenticado."}), 401
    return None


def _login_required_page(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    s = str(s).strip()[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _parse_dec(s: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(s).replace(",", "").strip() or default)
    except (InvalidOperation, ValueError):
        return Decimal(default)


def template_finiquito_path() -> Path:
    return Path(current_app.config["DOCX_TEMPLATES_DIR"]) / "finiquito_plantilla.docx"


def _now_iso() -> str:
    return datetime.now(ZoneInfo("America/Mexico_City")).strftime("%Y-%m-%d %H:%M:%S")


def _payload_from_request(data: dict[str, Any]) -> dict[str, Any]:
    ingreso = _parse_date(data.get("fecha_ingreso"))
    baja = _parse_date(data.get("fecha_baja"))
    emision = _parse_date(data.get("fecha_emision")) or date.today()
    zona = (data.get("zona_salarial") or "general").strip().lower()
    if zona not in ("general", "frontera"):
        zona = "general"
    periodicidad = "semanal_mensualizada"
    modo = (data.get("modo_calculo") or "correcto_fiscal").strip().lower()
    if modo not in ("correcto_fiscal", "aguinaldo_todo_gravable"):
        modo = "correcto_fiscal"
    sueldo_semanal = _parse_dec(data.get("sueldo_semanal"))
    sal_diario_in = _parse_dec(data.get("salario_diario"))
    sal_diario = _parse_dec("0")
    if sueldo_semanal > 0:
        sal_diario = (sueldo_semanal / Decimal("7")).quantize(Decimal("0.01"))
    elif sal_diario_in > 0:
        sal_diario = sal_diario_in
    sal_m_dec = _parse_dec(data.get("salario_mensual")) if data.get("salario_mensual") not in (None, "", "null") else None
    if sal_diario > 0:
        sal_m_dec = (sal_diario * Decimal("30.4")).quantize(Decimal("0.01"))
    elif sal_m_dec is not None and sal_m_dec <= 0:
        sal_m_dec = None

    return {
        "ingreso": ingreso,
        "baja": baja,
        "emision": emision,
        "nombre": (data.get("nombre_completo") or "").strip(),
        "lugar_emision": (data.get("lugar_emision") or "").strip(),
        "estado_emision": (data.get("estado_emision") or "").strip(),
        "zona": zona,
        "periodicidad": periodicidad,
        "modo": modo,
        "salario_diario": sal_diario,
        "sueldo_semanal": sueldo_semanal,
        "dias_aguinaldo": _parse_dec(data.get("dias_aguinaldo_politica"), "15"),
        "prima_vac_pct": _parse_dec(data.get("prima_vacacional_pct"), "25"),
        "vac_ya": _parse_dec(data.get("vacaciones_ya_usadas")),
        "aguinaldo_ya": Decimal("0"),
        "prima_vac_ya": Decimal("0"),
        "dias_sueldo": _parse_dec(data.get("dias_sueldo_pendientes")),
        # Política operativa: séptimos automáticos, no capturados manualmente.
        "septimos": _parse_dec(data.get("dias_sueldo_pendientes")) / Decimal("6"),
        "incluir_pa": str(data.get("incluir_prima_antiguedad") or "").lower() in ("1", "true", "on", "yes"),
        "motivo": "retiro_voluntario",
        "observaciones": (data.get("observaciones_internas") or "").strip(),
        "salario_mensual_capturado": sal_m_dec,
    }


def _validate_base(p: dict[str, Any]) -> str | None:
    if not p["nombre"]:
        return "El nombre completo es obligatorio."
    if p["baja"] is None:
        return "La fecha de baja es obligatoria."
    if p["salario_diario"] <= 0:
        return "El salario diario debe ser mayor a cero."
    if p["ingreso"] is None:
        return "La fecha de ingreso es obligatoria (búsqueda en Excel o captura manual)."
    if p["ingreso"] > p["baja"]:
        return "La fecha de ingreso no puede ser posterior a la fecha de baja."
    return None


def _resolver_prima_antiguedad(p: dict[str, Any]) -> tuple[bool, bool]:
    """Devuelve (aplica, incluir_en_calculo)."""
    if not p.get("ingreso") or not p.get("baja"):
        return False, False
    aplica = prima_antiguedad_aplica_separacion_voluntaria(p["ingreso"], p["baja"])
    incluir = aplica and bool(p.get("incluir_pa"))
    return aplica, incluir


@finiquitos_bp.route("/finiquito", methods=["GET"])
@_login_required_page
def pagina_finiquito():
    return render_template("finiquito.html")


@finiquitos_bp.route("/liquidacion", methods=["GET"])
@_login_required_page
def pagina_liquidacion():
    return render_template("liquidacion.html")


@finiquitos_bp.route("/historial", methods=["GET"])
@_login_required_page
def pagina_historial():
    return render_template("historial.html")


@finiquitos_bp.route("/api/excel-ingreso", methods=["POST"])
def api_excel_ingreso():
    err = _login_required_json()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    url = (current_app.config.get(_ONEDRIVE_URL_ENV) or "").strip()
    if not url:
        from os import environ

        url = (environ.get(_ONEDRIVE_URL_ENV) or "").strip()
    nombre = (data.get("nombre_completo") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "No está configurada la URL interna de OneDrive para Finiquitos."}), 400
    fd, msg = buscar_fecha_ingreso_excel(url, nombre)
    if msg:
        return jsonify({"ok": False, "error": msg}), 400
    return jsonify({"ok": True, "fecha_ingreso": fd.isoformat() if fd else None})


@finiquitos_bp.route("/api/calcular", methods=["POST"])
def api_calcular():
    err = _login_required_json()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    p = _payload_from_request(data)
    v = _validate_base(p)
    if v:
        return jsonify({"ok": False, "error": v}), 400
    assert p["ingreso"] and p["baja"]
    prima_aplica, incluir_pa = _resolver_prima_antiguedad(p)
    calc = calcular_finiquito(
        ingreso=p["ingreso"],
        baja=p["baja"],
        fecha_emision=p["emision"],
        salario_diario=p["salario_diario"],
        zona=p["zona"],
        periodicidad_isr=p["periodicidad"],
        modo=p["modo"],
        dias_sueldo_pendientes=p["dias_sueldo"],
        septimos_pendientes=p["septimos"],
        dias_aguinaldo_politica=p["dias_aguinaldo"],
        prima_vacacional_pct=p["prima_vac_pct"],
        vacaciones_ya_usadas=p["vac_ya"],
        aguinaldo_ya_pagado=p["aguinaldo_ya"],
        prima_vac_ya_pagada=p["prima_vac_ya"],
        incluir_prima_antiguedad=incluir_pa,
        motivo_baja=p["motivo"],
        salario_mensual_capturado=p["salario_mensual_capturado"],
    )
    return jsonify(
        {
            "ok": True,
            "resultado": calc,
            "entrada": p,
            "prima_antiguedad_aplica": prima_aplica,
            "prima_antiguedad_incluida": incluir_pa,
            "periodicidad_operativa": "semanal",
            "criterio_isr_ordinario": "mensualizado",
            "neto_letra": importe_mxn_a_letra(Decimal(str(calc["totales"]["neto_final"]))),
        }
    )


@finiquitos_bp.route("/api/liquidacion", methods=["POST"])
def api_liquidacion():
    err = _login_required_json()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    p = _payload_from_request(data)
    v = _validate_base(p)
    if v:
        return jsonify({"ok": False, "error": v}), 400
    assert p["ingreso"] and p["baja"]
    _, incluir_pa = _resolver_prima_antiguedad(p)
    res = calcular_liquidacion_comparativa(
        ingreso=p["ingreso"],
        baja=p["baja"],
        fecha_emision=p["emision"],
        salario_diario=p["salario_diario"],
        zona=p["zona"],
        periodicidad_isr=p["periodicidad"],
        modo=p["modo"],
        dias_sueldo_pendientes=p["dias_sueldo"],
        septimos_pendientes=p["septimos"],
        dias_aguinaldo_politica=p["dias_aguinaldo"],
        prima_vacacional_pct=p["prima_vac_pct"],
        vacaciones_ya_usadas=p["vac_ya"],
        aguinaldo_ya_pagado=p["aguinaldo_ya"],
        prima_vac_ya_pagada=p["prima_vac_ya"],
        incluir_prima_antiguedad=incluir_pa,
        motivo_baja=p["motivo"],
        salario_mensual_capturado=p["salario_mensual_capturado"],
    )
    return jsonify({"ok": True, "resultado": res, "entrada": p})


@finiquitos_bp.route("/api/pdf", methods=["POST"])
def api_pdf():
    err = _login_required_json()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    p = _payload_from_request(data)
    v = _validate_base(p)
    if v:
        return jsonify({"ok": False, "error": v}), 400
    assert p["ingreso"] and p["baja"]
    _, incluir_pa = _resolver_prima_antiguedad(p)
    calc = calcular_finiquito(
        ingreso=p["ingreso"],
        baja=p["baja"],
        fecha_emision=p["emision"],
        salario_diario=p["salario_diario"],
        zona=p["zona"],
        periodicidad_isr=p["periodicidad"],
        modo=p["modo"],
        dias_sueldo_pendientes=p["dias_sueldo"],
        septimos_pendientes=p["septimos"],
        dias_aguinaldo_politica=p["dias_aguinaldo"],
        prima_vacacional_pct=p["prima_vac_pct"],
        vacaciones_ya_usadas=p["vac_ya"],
        aguinaldo_ya_pagado=p["aguinaldo_ya"],
        prima_vac_ya_pagada=p["prima_vac_ya"],
        incluir_prima_antiguedad=incluir_pa,
        motivo_baja=p["motivo"],
        salario_mensual_capturado=p["salario_mensual_capturado"],
    )
    tpl = template_finiquito_path()
    if not tpl.is_file():
        return jsonify({"ok": False, "error": f"No existe la plantilla DOCX en {tpl}"}), 400
    mapping = build_finiquito_placeholders(
        lugar_emision=p["lugar_emision"],
        estado_emision=p["estado_emision"],
        fecha_emision=p["emision"],
        empleado_nombre=p["nombre"],
        calc=calc,
        incluir_prima_antig=incluir_pa,
    )
    try:
        docx_b = render_finiquito_docx(tpl, mapping)
        pdf_b = render_finiquito_pdf(docx_b)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503

    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", p["nombre"])[:80] or "EMPLEADO"
    fname = f"FINIQUITO_{safe_name}_{p['baja'].isoformat()}.pdf"
    return Response(
        pdf_b,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@finiquitos_bp.route("/api/historial/finiquito", methods=["POST"])
def api_historial_finiquito():
    err = _login_required_json()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    p = _payload_from_request(data)
    v = _validate_base(p)
    if v:
        return jsonify({"ok": False, "error": v}), 400
    assert p["ingreso"] and p["baja"]
    prima_aplica, incluir_pa = _resolver_prima_antiguedad(p)
    calc = calcular_finiquito(
        ingreso=p["ingreso"],
        baja=p["baja"],
        fecha_emision=p["emision"],
        salario_diario=p["salario_diario"],
        zona=p["zona"],
        periodicidad_isr=p["periodicidad"],
        modo=p["modo"],
        dias_sueldo_pendientes=p["dias_sueldo"],
        septimos_pendientes=p["septimos"],
        dias_aguinaldo_politica=p["dias_aguinaldo"],
        prima_vacacional_pct=p["prima_vac_pct"],
        vacaciones_ya_usadas=p["vac_ya"],
        aguinaldo_ya_pagado=p["aguinaldo_ya"],
        prima_vac_ya_pagada=p["prima_vac_ya"],
        incluir_prima_antiguedad=incluir_pa,
        motivo_baja=p["motivo"],
        salario_mensual_capturado=p["salario_mensual_capturado"],
    )
    pdf_path = None
    pdf_fn = None
    if data.get("incluir_pdf_guardado"):
        tpl = template_finiquito_path()
        if tpl.is_file():
            mapping = build_finiquito_placeholders(
                lugar_emision=p["lugar_emision"],
                estado_emision=p["estado_emision"],
                fecha_emision=p["emision"],
                empleado_nombre=p["nombre"],
                calc=calc,
                incluir_prima_antig=incluir_pa,
            )
            try:
                docx_b = render_finiquito_docx(tpl, mapping)
                pdf_b = render_finiquito_pdf(docx_b)
                gen = Path(current_app.config["GENERATED_DIR"])
                gen.mkdir(parents=True, exist_ok=True)
                safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", p["nombre"])[:80] or "EMPLEADO"
                pdf_fn = f"FINIQUITO_{safe_name}_{p['baja'].isoformat()}.pdf"
                pdf_path = str(gen / pdf_fn)
                Path(pdf_path).write_bytes(pdf_b)
            except Exception:
                pdf_path = None
                pdf_fn = None

    payload = {
        "entrada": {k: str(v) if isinstance(v, date) else v for k, v in p.items()},
        "fecha_ingreso_excel": p["ingreso"].isoformat() if p["ingreso"] else None,
        "calculo": calc,
        "constantes": {
            "zona": p["zona"],
            "periodicidad_operativa": "semanal",
            "criterio_isr_ordinario": "mensualizado_tipo_contpaq",
            "prima_antiguedad_aplica": prima_aplica,
            "prima_antiguedad_incluida": incluir_pa,
            "salario_minimo_zona": calc["fiscal"]["salario_minimo_zona"],
            "SMG_GENERAL_2026": str(fincfg.SMG_GENERAL_2026),
            "SMG_FRONTERA_2026": str(fincfg.SMG_FRONTERA_2026),
            "UMA_DIARIA_2026": str(fincfg.UMA_DIARIA_2026),
            "UMA_MENSUAL_2026": str(fincfg.UMA_MENSUAL_2026),
            "tablas_isr": "ISR_TABLA_QUINCENAL_2026 / ISR_TABLA_MENSUAL_2026",
        },
    }
    rid = insert_finiquito_history(
        str(current_app.config["DATABASE"]),
        user_id=g.user["id"],
        created_at=_now_iso(),
        modo_calculo=p["modo"],
        payload=payload,
        pdf_path=pdf_path,
        pdf_filename=pdf_fn,
    )
    return jsonify({"ok": True, "id": rid})


@finiquitos_bp.route("/api/historial/liquidacion", methods=["POST"])
def api_historial_liquidacion():
    err = _login_required_json()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    p = _payload_from_request(data)
    v = _validate_base(p)
    if v:
        return jsonify({"ok": False, "error": v}), 400
    assert p["ingreso"] and p["baja"]
    _, incluir_pa = _resolver_prima_antiguedad(p)
    res = calcular_liquidacion_comparativa(
        ingreso=p["ingreso"],
        baja=p["baja"],
        fecha_emision=p["emision"],
        salario_diario=p["salario_diario"],
        zona=p["zona"],
        periodicidad_isr=p["periodicidad"],
        modo=p["modo"],
        dias_sueldo_pendientes=p["dias_sueldo"],
        septimos_pendientes=p["septimos"],
        dias_aguinaldo_politica=p["dias_aguinaldo"],
        prima_vacacional_pct=p["prima_vac_pct"],
        vacaciones_ya_usadas=p["vac_ya"],
        aguinaldo_ya_pagado=p["aguinaldo_ya"],
        prima_vac_ya_pagada=p["prima_vac_ya"],
        incluir_prima_antiguedad=incluir_pa,
        motivo_baja=p["motivo"],
        salario_mensual_capturado=p["salario_mensual_capturado"],
    )
    payload = {
        "entrada": {k: str(v) if isinstance(v, date) else v for k, v in p.items()},
        "resultado": res,
    }
    rid = insert_liquidacion_history(
        str(current_app.config["DATABASE"]),
        user_id=g.user["id"],
        created_at=_now_iso(),
        payload=payload,
    )
    return jsonify({"ok": True, "id": rid})


@finiquitos_bp.route("/api/historial/lista", methods=["GET"])
def api_historial_lista():
    err = _login_required_json()
    if err:
        return err
    kind = (request.args.get("tipo") or "finiquito").strip().lower()
    if kind == "liquidacion":
        rows = list_liquidacion_history(str(current_app.config["DATABASE"]))
        out = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "created_at": r["created_at"],
                    "username": r["username"],
                    "tipo": "liquidacion",
                }
            )
    else:
        rows = list_finiquito_history(str(current_app.config["DATABASE"]))
        out = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "created_at": r["created_at"],
                    "username": r["username"],
                    "modo_calculo": r["modo_calculo"],
                    "pdf_filename": r["pdf_filename"],
                    "tipo": "finiquito",
                }
            )
    return jsonify({"ok": True, "items": out})


def register_finiquitos(app):
    ensure_finiquitos_tables(str(app.config["DATABASE"]))
    app.register_blueprint(finiquitos_bp)
