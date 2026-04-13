"""Rutas Flask: Finiquitos."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from functools import wraps

from flask import Blueprint, Response, current_app, g, jsonify, redirect, render_template, request, send_file, url_for

from modules.finiquitos import config as fincfg
from modules.finiquitos.calc import (
    anios_servicio_exactos,
    calcular_dias_vacaciones_devengados,
    calcular_finiquito,
    prima_antiguedad_aplica_separacion_voluntaria,
)
from modules.finiquitos.export_docx import build_finiquito_placeholders, render_finiquito_docx, render_finiquito_final
from modules.finiquitos.nombre_archivo_finiquito import build_finiquito_pdf_filename
from modules.finiquitos.edicion_libre_finiquito import apply_desglose_manual
from modules.finiquitos.excel_mirror_fecha_ingreso import buscar_fecha_ingreso_headcount_onedrive
from modules.finiquitos.graph_excel import buscar_fecha_ingreso_excel
from modules.finiquitos.numero_letra import importe_mxn_a_letra
from services.finiquitos_history import (
    delete_finiquito_history,
    ensure_finiquitos_tables,
    get_finiquito_history_row,
    insert_finiquito_history,
    list_finiquito_history,
)

_BASE = Path(__file__).resolve().parent.parent.parent
_TEMPLATE_DIR = _BASE / "templates" / "finiquitos"
_ONEDRIVE_URL_ENV = "FINIQUITOS_ONEDRIVE_SHARED_URL"

logger = logging.getLogger(__name__)

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
    return Path(current_app.config["DOCX_TEMPLATES_DIR"]) / "FINIQUITO FORMATO.docx"


def _now_iso() -> str:
    return datetime.now(ZoneInfo("America/Mexico_City")).strftime("%Y-%m-%d %H:%M:%S")


_MODO_FINIQUITO_ETIQUETA: dict[str, str] = {
    "correcto_fiscal": "Fiscal",
    "total_gravable": "Total gravable",
}


def _modo_slug_finiquito(modo_raw: Any) -> str:
    """Normaliza espacios/guiones a guión bajo para comparar variantes de texto."""
    s = str(modo_raw or "").strip().lower().replace("-", "_")
    s = "_".join(p for p in s.replace(" ", "_").split("_") if p)
    return s


def _normalize_finiquito_modo(modo_raw: Any) -> str:
    """
    Solo modos activos: correcto_fiscal | total_gravable.
    Cualquier slug que implique 'aguinaldo' + 'todo' + 'gravable' se trata como legado y se mapea a total_gravable.
    """
    m = _modo_slug_finiquito(modo_raw) or "total_gravable"
    if m in ("correcto_fiscal", "total_gravable"):
        return m
    legacy = "aguinaldo" in m and "todo" in m and "gravable" in m
    if legacy:
        logger.warning("modo_calculo legado recibido (%r); se normaliza a 'total_gravable'.", modo_raw)
        return "total_gravable"
    if m:
        logger.warning("modo_calculo desconocido (%r); se usa 'total_gravable'.", modo_raw)
    return "total_gravable"


def _modo_finiquito_etiqueta(modo: str | None) -> str:
    if modo is None or str(modo).strip() == "":
        return ""
    key = _normalize_finiquito_modo(modo)
    return _MODO_FINIQUITO_ETIQUETA.get(key, key)


def _apply_desglose_manual_si_aplica(
    data: dict[str, Any],
    calc: dict[str, Any],
    *,
    entrada: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not _bool_coerce_entrada(data.get("edicion_libre_desglose")):
        return calc
    dm = data.get("desglose_manual")
    if not isinstance(dm, dict):
        return calc
    ent = entrada or {}
    if dm.get("v") == 2:
        return apply_desglose_manual(calc, dm, entrada=ent)
    filas = dm.get("filas")
    if not isinstance(filas, list) or not filas:
        return calc
    return apply_desglose_manual(calc, filas, entrada=ent)


def _payload_resumen_lista(payload_json: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "nombre_empleado": "",
        "sueldo_semanal": "",
        "fecha_ingreso": "",
        "total_finiquito_pagado": None,
    }
    if not payload_json:
        return out
    try:
        d = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError):
        return out
    ent = d.get("entrada") or {}
    calc = d.get("calculo") or {}
    tot = calc.get("totales") or {}
    ing = ent.get("ingreso")
    out["nombre_empleado"] = str(ent.get("nombre") or "").strip()
    ss = ent.get("sueldo_semanal")
    out["sueldo_semanal"] = "" if ss is None else str(ss).strip()
    if ing is not None:
        s = str(ing).strip()
        out["fecha_ingreso"] = s[:10] if len(s) >= 10 else s
    if "neto_final" in tot:
        try:
            out["total_finiquito_pagado"] = float(tot["neto_final"])
        except (TypeError, ValueError):
            out["total_finiquito_pagado"] = None
    return out


def _resolved_pdf_path_if_safe(pdf_path: str | None, generated_dir: Path) -> Path | None:
    if not pdf_path or not str(pdf_path).strip():
        return None
    try:
        p = Path(pdf_path).resolve()
        root = generated_dir.resolve()
        p.relative_to(root)
    except (ValueError, OSError):
        return None
    return p if p.is_file() else None


def _sanitize_download_basename(raw: str | None, *, fallback: str) -> str:
    """Nombre de archivo sin ruta ni extensión forzada; seguro para Content-Disposition."""
    fb = (fallback or "Finiquito").strip() or "Finiquito"
    if not raw or not str(raw).strip():
        return fb
    s = Path(str(raw).strip()).name.replace("\\", "").replace("/", "")
    for ch in ('<', '>', ':', '"', '|', '?', '*'):
        s = s.replace(ch, "_")
    s = s.strip(" .") or fb
    low = s.lower()
    for ext in (".pdf", ".docx"):
        if low.endswith(ext):
            s = s[: -len(ext)].strip(" .") or fb
            break
    if len(s) > 150:
        s = s[:150].strip(" .") or fb
    return s or fb


def _payload_from_request(data: dict[str, Any]) -> dict[str, Any]:
    ingreso = _parse_date(data.get("fecha_ingreso"))
    baja = _parse_date(data.get("fecha_baja"))
    emision = _parse_date(data.get("fecha_emision")) or date.today()
    zona = (data.get("zona_salarial") or "general").strip().lower()
    if zona not in ("general", "frontera"):
        zona = "general"
    periodicidad = "semanal_mensualizada"
    modo = _normalize_finiquito_modo(data.get("modo_calculo"))
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

    prima_pagada_previamente = str(data.get("prima_pagada_previamente") or "").lower() in ("1", "true", "on", "yes", "si")

    modo_solicitado = modo

    dm = data.get("desglose_manual")
    if not isinstance(dm, dict):
        dm = {}

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
        "modo_solicitado": modo_solicitado,
        "salario_diario": sal_diario,
        "sueldo_semanal": sueldo_semanal,
        "dias_aguinaldo": _parse_dec(data.get("dias_aguinaldo_politica"), "15"),
        "prima_vac_pct": _parse_dec(data.get("prima_vacacional_pct"), "25"),
        "vac_ya": _parse_dec(data.get("vacaciones_ya_usadas")),
        "aguinaldo_pagado_previamente": str(data.get("aguinaldo_pagado_previamente") or "").lower() in ("1", "true", "on", "yes", "si"),
        "aguinaldo_ya": Decimal("0"),
        "prima_vac_ya": Decimal("0"),
        "prima_pagada_previamente": prima_pagada_previamente,
        "prima_dias_cubiertos": _parse_dec(data.get("prima_dias_cubiertos")) if prima_pagada_previamente else Decimal("0"),
        "dias_sueldo": _parse_dec(data.get("dias_sueldo_pendientes")),
        # Política operativa: séptimos automáticos, no capturados manualmente.
        "septimos": _parse_dec(data.get("dias_sueldo_pendientes")) / Decimal("6"),
        "incluir_pa": str(data.get("incluir_prima_antiguedad") or "").lower() in ("1", "true", "on", "yes"),
        "motivo": "retiro_voluntario",
        "observaciones": (data.get("observaciones_internas") or "").strip(),
        "salario_mensual_capturado": sal_m_dec,
        "edicion_libre_desglose": _bool_coerce_entrada(data.get("edicion_libre_desglose")),
        "desglose_manual": dm,
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


def _aplicar_tope_vacaciones_ya_usadas(p: dict[str, Any]) -> None:
    """Si ingreso es del año calendario actual, no permitir vac_ya > días devengados (misma lógica que el cálculo)."""
    ing = p.get("ingreso")
    baja = p.get("baja")
    if ing is None or baja is None:
        return
    if ing.year != date.today().year:
        return
    cap = calcular_dias_vacaciones_devengados(ing, baja)["dias_vac_total_dev"]
    if p["vac_ya"] > cap:
        p["vac_ya"] = cap


def _resolver_prima_antiguedad(p: dict[str, Any]) -> tuple[bool, bool]:
    """Devuelve (aplica, incluir_en_calculo)."""
    if not p.get("ingreso") or not p.get("baja"):
        return False, False
    aplica = prima_antiguedad_aplica_separacion_voluntaria(p["ingreso"], p["baja"])
    incluir = aplica and bool(p.get("incluir_pa"))
    return aplica, incluir


def _build_finiquito_snapshot_payload(
    p: dict[str, Any],
    calc: dict[str, Any],
    prima_aplica: bool,
    incluir_pa: bool,
) -> dict[str, Any]:
    return {
        "entrada": {k: str(v) if isinstance(v, date) else v for k, v in p.items()},
        "fecha_ingreso_excel": p["ingreso"].isoformat() if p["ingreso"] else None,
        "calculo": calc,
        "tipo_documento": "finiquito",
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


def _bool_coerce_entrada(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("1", "true", "yes", "si", "on")


def _entrada_a_formulario_api(entrada: dict[str, Any]) -> dict[str, Any]:
    """Mapea snapshot.entrada (claves internas del payload) al JSON que consume el formulario."""
    if not entrada:
        return {}

    def slice_iso(key: str) -> str:
        v = entrada.get(key)
        if v is None or v == "":
            return ""
        s = str(v).strip()
        return s[:10] if len(s) >= 10 else s

    def num_str(key: str, default: str = "0") -> str:
        v = entrada.get(key)
        if v is None or v == "":
            return default
        return str(v).strip()

    modo_raw = _normalize_finiquito_modo(entrada.get("modo_solicitado") or entrada.get("modo"))

    zona = str(entrada.get("zona") or "general").strip().lower()
    if zona not in ("general", "frontera"):
        zona = "general"

    dm = entrada.get("desglose_manual")
    if not isinstance(dm, dict):
        dm = {}

    return {
        "nombre_completo": str(entrada.get("nombre") or ""),
        "fecha_ingreso": slice_iso("ingreso"),
        "fecha_baja": slice_iso("baja"),
        "fecha_emision": slice_iso("emision"),
        "lugar_emision": str(entrada.get("lugar_emision") or ""),
        "estado_emision": str(entrada.get("estado_emision") or ""),
        "zona_salarial": zona,
        "modo_calculo": modo_raw,
        "sueldo_semanal": num_str("sueldo_semanal", ""),
        "dias_aguinaldo_politica": num_str("dias_aguinaldo", "15"),
        "prima_vacacional_pct": num_str("prima_vac_pct", "25"),
        "vacaciones_ya_usadas": num_str("vac_ya", "0"),
        "prima_pagada_previamente": _bool_coerce_entrada(entrada.get("prima_pagada_previamente")),
        "aguinaldo_pagado_previamente": _bool_coerce_entrada(entrada.get("aguinaldo_pagado_previamente")),
        "prima_dias_cubiertos": num_str("prima_dias_cubiertos", "0"),
        "dias_sueldo_pendientes": num_str("dias_sueldo", "0"),
        "incluir_prima_antiguedad": _bool_coerce_entrada(entrada.get("incluir_pa")),
        "edicion_libre_desglose": _bool_coerce_entrada(entrada.get("edicion_libre_desglose")),
        "desglose_manual": dm,
    }


@finiquitos_bp.route("/finiquito", methods=["GET"])
@_login_required_page
def pagina_finiquito():
    return render_template("finiquito.html")


@finiquitos_bp.route("/historial", methods=["GET"])
@_login_required_page
def pagina_historial():
    return render_template("historial.html")


@finiquitos_bp.route("/api/antiguedad-servicio", methods=["POST"])
def api_antiguedad_servicio():
    """Años de servicio exactos (misma función que el cálculo laboral): ingreso → baja."""
    err = _login_required_json()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    ing = _parse_date(data.get("fecha_ingreso"))
    baj = _parse_date(data.get("fecha_baja"))
    if ing is None or baj is None:
        return jsonify({"ok": False, "error": "Se requieren fecha de ingreso y fecha de baja."}), 400
    if ing > baj:
        return jsonify({"ok": False, "error": "La fecha de ingreso no puede ser posterior a la fecha de baja."}), 400
    anios = anios_servicio_exactos(ing, baj)
    return jsonify({"ok": True, "anios_servicio_exactos": float(anios)})


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


@finiquitos_bp.route("/buscar-fecha-ingreso-excel", methods=["POST"])
def buscar_fecha_ingreso_excel_mirror():
    """Descarga el Excel de headcount desde OneDrive (enlace compartido) y cruza por nombre."""
    err = _login_required_json()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    nombre = (data.get("nombre_completo") or "").strip()
    fd, nombre_encontrado, msg = buscar_fecha_ingreso_headcount_onedrive(nombre)
    if msg:
        return jsonify({"ok": False, "error": msg}), 400
    return jsonify(
        {
            "ok": True,
            "fecha_ingreso": fd.isoformat() if fd else None,
            "nombre_encontrado": nombre_encontrado or "",
        }
    )


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
    _aplicar_tope_vacaciones_ya_usadas(p)
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
        aguinaldo_pagado_previamente=p["aguinaldo_pagado_previamente"],
        prima_dias_cubiertos=p["prima_dias_cubiertos"],
        incluir_prima_antiguedad=incluir_pa,
        motivo_baja=p["motivo"],
        salario_mensual_capturado=p["salario_mensual_capturado"],
    )
    calc = _apply_desglose_manual_si_aplica(data, calc, entrada=p)
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
    _aplicar_tope_vacaciones_ya_usadas(p)
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
        aguinaldo_pagado_previamente=p["aguinaldo_pagado_previamente"],
        prima_dias_cubiertos=p["prima_dias_cubiertos"],
        incluir_prima_antiguedad=incluir_pa,
        motivo_baja=p["motivo"],
        salario_mensual_capturado=p["salario_mensual_capturado"],
    )
    calc = _apply_desglose_manual_si_aplica(data, calc, entrada=p)
    tpl = template_finiquito_path()
    if not tpl.is_file():
        return jsonify({"ok": False, "error": f"No existe la plantilla DOCX en {tpl}"}), 400
    mapping = build_finiquito_placeholders(
        lugar_emision=p["lugar_emision"],
        estado_emision=p["estado_emision"],
        fecha_emision=p["emision"],
        fecha_baja=p["baja"],
        empleado_nombre=p["nombre"],
        calc=calc,
        incluir_prima_antig=incluir_pa,
    )
    fname = build_finiquito_pdf_filename(p["nombre"] or "")
    pdf_stem = Path(fname).stem
    try:
        _docx_b, pdf_b = render_finiquito_final(tpl, mapping, pdf_stem=pdf_stem)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503

    gen = Path(current_app.config["GENERATED_DIR"])
    gen.mkdir(parents=True, exist_ok=True)
    pdf_path = str(gen / fname)
    Path(pdf_path).write_bytes(pdf_b)
    payload = _build_finiquito_snapshot_payload(p, calc, prima_aplica, incluir_pa)
    rid = insert_finiquito_history(
        str(current_app.config["DATABASE"]),
        user_id=g.user["id"],
        created_at=_now_iso(),
        modo_calculo=p["modo"],
        payload=payload,
        pdf_path=pdf_path,
        pdf_filename=fname,
    )
    resp = Response(
        pdf_b,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-Finiquito-Historial-Id": str(rid),
        },
    )
    return resp


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
    _aplicar_tope_vacaciones_ya_usadas(p)
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
        aguinaldo_pagado_previamente=p["aguinaldo_pagado_previamente"],
        prima_dias_cubiertos=p["prima_dias_cubiertos"],
        incluir_prima_antiguedad=incluir_pa,
        motivo_baja=p["motivo"],
        salario_mensual_capturado=p["salario_mensual_capturado"],
    )
    calc = _apply_desglose_manual_si_aplica(data, calc, entrada=p)
    pdf_path = None
    pdf_fn = None
    if data.get("incluir_pdf_guardado"):
        tpl = template_finiquito_path()
        if tpl.is_file():
            mapping = build_finiquito_placeholders(
                lugar_emision=p["lugar_emision"],
                estado_emision=p["estado_emision"],
                fecha_emision=p["emision"],
                fecha_baja=p["baja"],
                empleado_nombre=p["nombre"],
                calc=calc,
                incluir_prima_antig=incluir_pa,
            )
            try:
                pdf_fn = build_finiquito_pdf_filename(p["nombre"] or "")
                pdf_stem = Path(pdf_fn).stem
                _docx_b, pdf_b = render_finiquito_final(tpl, mapping, pdf_stem=pdf_stem)
                gen = Path(current_app.config["GENERATED_DIR"])
                gen.mkdir(parents=True, exist_ok=True)
                pdf_path = str(gen / pdf_fn)
                Path(pdf_path).write_bytes(pdf_b)
            except Exception:
                pdf_path = None
                pdf_fn = None

    payload = _build_finiquito_snapshot_payload(p, calc, prima_aplica, incluir_pa)
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


@finiquitos_bp.route("/api/historial/lista", methods=["GET"])
def api_historial_lista():
    err = _login_required_json()
    if err:
        return err
    rows = list_finiquito_history(str(current_app.config["DATABASE"]))
    gen = Path(current_app.config["GENERATED_DIR"]).resolve()
    out = []
    for r in rows:
        res = _payload_resumen_lista(r["payload_json"])
        pdf_ok = _resolved_pdf_path_if_safe(r["pdf_path"], gen) is not None
        out.append(
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "username": r["username"],
                "modo_calculo": r["modo_calculo"],
                "modo_etiqueta": _modo_finiquito_etiqueta(r["modo_calculo"]),
                "pdf_filename": r["pdf_filename"],
                "nombre_empleado": res["nombre_empleado"],
                "sueldo_semanal": res["sueldo_semanal"],
                "fecha_ingreso": res["fecha_ingreso"],
                "total_finiquito_pagado": res["total_finiquito_pagado"],
                "pdf_disponible": pdf_ok,
                "tipo": "finiquito",
            }
        )
    return jsonify({"ok": True, "items": out})


@finiquitos_bp.route("/api/historial/registro/<int:hid>", methods=["GET", "DELETE"])
def api_historial_registro(hid: int):
    err = _login_required_json()
    if err:
        return err
    if request.method == "DELETE":
        if g.user["role"] != "admin":
            return jsonify({"ok": False, "error": "Solo administradores pueden eliminar registros."}), 403
        row = get_finiquito_history_row(str(current_app.config["DATABASE"]), hid)
        if not row:
            return jsonify({"ok": False, "error": "No encontrado."}), 404
        gen = Path(current_app.config["GENERATED_DIR"]).resolve()
        pdf_p = _resolved_pdf_path_if_safe(row["pdf_path"], gen)
        if not delete_finiquito_history(str(current_app.config["DATABASE"]), hid):
            return jsonify({"ok": False, "error": "No se pudo eliminar."}), 500
        if pdf_p and pdf_p.is_file():
            try:
                pdf_p.unlink()
            except OSError:
                pass
        return jsonify({"ok": True})

    row = get_finiquito_history_row(str(current_app.config["DATABASE"]), hid)
    if not row:
        return jsonify({"ok": False, "error": "No encontrado."}), 404
    try:
        snap = json.loads(row["payload_json"])
    except (json.JSONDecodeError, TypeError):
        snap = {}
    gen = Path(current_app.config["GENERATED_DIR"]).resolve()
    raw_ent = snap.get("entrada")
    entrada = raw_ent if isinstance(raw_ent, dict) else {}
    if raw_ent is not None and not isinstance(raw_ent, dict):
        logger.warning("historial finiquito id=%s: entrada con tipo inesperado %s", hid, type(raw_ent).__name__)
    if not entrada:
        logger.warning("historial finiquito id=%s: snapshot sin entrada capturada o vacía", hid)
    formulario = _entrada_a_formulario_api(entrada)
    return jsonify(
        {
            "ok": True,
            "id": row["id"],
            "created_at": row["created_at"],
            "username": row["username"],
            "modo_calculo": row["modo_calculo"],
            "modo_etiqueta": _modo_finiquito_etiqueta(row["modo_calculo"]),
            "pdf_filename": row["pdf_filename"],
            "pdf_disponible": _resolved_pdf_path_if_safe(row["pdf_path"], gen) is not None,
            "snapshot": snap,
            "formulario": formulario,
        }
    )


@finiquitos_bp.route("/api/historial/registro/<int:hid>/pdf", methods=["GET"])
def api_historial_registro_pdf(hid: int):
    if g.user is None:
        return redirect(url_for("login"))
    row = get_finiquito_history_row(str(current_app.config["DATABASE"]), hid)
    if not row:
        return jsonify({"ok": False, "error": "No encontrado."}), 404
    gen = Path(current_app.config["GENERATED_DIR"]).resolve()
    pdf_p = _resolved_pdf_path_if_safe(row["pdf_path"], gen)
    if not pdf_p:
        return jsonify({"ok": False, "error": "No hay PDF guardado para este registro."}), 404
    default_name = row["pdf_filename"] or pdf_p.name
    default_stem = Path(default_name).stem or "Finiquito"
    raw_bn = (request.args.get("basename") or request.args.get("nombre") or "").strip()
    if raw_bn:
        stem = _sanitize_download_basename(raw_bn, fallback=default_stem)
        download_name = f"{stem}.pdf"
    else:
        download_name = default_name
    return send_file(
        pdf_p,
        mimetype="application/pdf",
        download_name=download_name,
        as_attachment=True,
    )


@finiquitos_bp.route("/api/historial/registro/<int:hid>/docx", methods=["GET"])
def api_historial_registro_docx(hid: int):
    if g.user is None:
        return redirect(url_for("login"))
    row = get_finiquito_history_row(str(current_app.config["DATABASE"]), hid)
    if not row:
        return jsonify({"ok": False, "error": "No encontrado."}), 404
    try:
        payload = json.loads(row["payload_json"])
    except (json.JSONDecodeError, TypeError):
        return jsonify({"ok": False, "error": "Snapshot inválido."}), 400
    entrada = payload.get("entrada") or {}
    calc = payload.get("calculo")
    if not calc:
        return jsonify({"ok": False, "error": "Sin cálculo en el snapshot."}), 400
    consts = payload.get("constantes") or {}
    incluir_pa = bool(consts.get("prima_antiguedad_incluida"))
    emision = _parse_date(str(entrada.get("emision") or "")[:10]) or date.today()
    baja = _parse_date(str(entrada.get("baja") or "")[:10])
    if baja is None:
        return jsonify({"ok": False, "error": "Snapshot sin fecha de baja."}), 400
    tpl = template_finiquito_path()
    if not tpl.is_file():
        return jsonify({"ok": False, "error": f"No existe la plantilla DOCX en {tpl}"}), 400
    mapping = build_finiquito_placeholders(
        lugar_emision=str(entrada.get("lugar_emision") or ""),
        estado_emision=str(entrada.get("estado_emision") or ""),
        fecha_emision=emision,
        fecha_baja=baja,
        empleado_nombre=str(entrada.get("nombre") or ""),
        calc=calc,
        incluir_prima_antig=incluir_pa,
    )
    try:
        docx_b = render_finiquito_docx(tpl, mapping)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    pdf_fn = row["pdf_filename"] or ""
    base = Path(pdf_fn).stem if pdf_fn else ""
    if not base:
        base = Path(build_finiquito_pdf_filename(str(entrada.get("nombre") or ""))).stem
    default_stem = base or "Finiquito"
    raw_bn = (request.args.get("basename") or request.args.get("nombre") or "").strip()
    if raw_bn:
        stem = _sanitize_download_basename(raw_bn, fallback=default_stem)
        fname = f"{stem}.docx"
    else:
        fname = f"{default_stem}.docx"
    return Response(
        docx_b,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


def register_finiquitos(app):
    ensure_finiquitos_tables(str(app.config["DATABASE"]))
    app.register_blueprint(finiquitos_bp)
