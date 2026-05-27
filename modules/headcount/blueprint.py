from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from modules.headcount.exports import build_auditoria_excel_bytes
from modules.headcount.matching import info_estado_label, warning_label
from modules.headcount.ui_format import (
    build_cliente_cards_for_ui,
    display_cell,
    display_cliente,
    display_fecha_ingreso,
    display_periodo_corte,
    display_registro_patronal,
    display_ubicacion,
    sort_value_date,
    sort_value_number,
)
from modules.headcount.privacy import mask_registro_for_display, should_mask_sensitive_data
from modules.headcount.services import (
    calc_metricas_desarrollo_inf,
    ejecutar_auditoria_sua,
    filtrar_detalle,
    listar_clientes_headcount,
    listar_ubicaciones_headcount,
    obtener_registros_headcount,
    resumen_cliente_view,
)
from modules.headcount.storage import (
    delete_sua_audit,
    ensure_headcount_tables,
    find_duplicate_audit,
    get_sua_audit,
    insert_sua_audit,
    list_sua_audits,
)
from modules.roles_access import (
    can_access_headcount_auditoria,
    can_access_headcount_cliente,
    can_access_headcount_desglose,
    can_access_headcount_module,
    can_delete_headcount_audit,
    normalized_role,
)

_BASE = Path(__file__).resolve().parent.parent.parent
_TEMPLATE_DIR = _BASE / "templates" / "headcount"
_TZ = ZoneInfo("America/Mexico_City")


headcount_bp = Blueprint(
    "headcount",
    __name__,
    url_prefix="/headcount",
    template_folder=str(_TEMPLATE_DIR),
)


def _login_required_page(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


@headcount_bp.before_request
def _headcount_module_guard() -> None:
    if g.user is None:
        return
    role = normalized_role(g.user)
    if not can_access_headcount_module(role):
        abort(403)
    path = request.path or ""
    if not can_access_headcount_auditoria(role):
        if path.startswith("/headcount/auditoria-sua") or path.startswith("/headcount/historial-sua"):
            abort(403)
        if "/exportar-excel" in path:
            abort(403)
    if not can_access_headcount_cliente(role):
        if path.startswith("/headcount/conteo-personal"):
            abort(403)


def _require_auditoria() -> None:
    if not can_access_headcount_auditoria(normalized_role(g.user)):
        abort(403)


def _require_cliente() -> None:
    if not can_access_headcount_cliente(normalized_role(g.user)):
        abort(403)


def _require_desglose() -> None:
    if not can_access_headcount_desglose(normalized_role(g.user)):
        abort(403)


def _now_iso() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _db_path() -> str:
    return str(current_app.config["DATABASE"])


def _role() -> str:
    return normalized_role(g.user)


def _solo_activos_for_role() -> bool:
    return _role() in {"usuario", "coordinador"}


def _sua_detalle_total(resumen: dict, detalle_full: list) -> int:
    total = int(resumen.get("total_cotizantes_sua") or resumen.get("total_cotizantes") or 0)
    if total > 0:
        return total
    return len(detalle_full)


def _load_payload_from_audit(audit_id: str) -> dict:
    audit = get_sua_audit(_db_path(), audit_id)
    if not audit:
        abort(404)
    try:
        return json.loads(audit["detalle_json"])
    except json.JSONDecodeError:
        abort(500)


@headcount_bp.route("/")
@_login_required_page
def index():
    role = _role()
    if can_access_headcount_auditoria(role):
        return redirect(url_for("headcount.auditoria_sua"))
    if can_access_headcount_cliente(role):
        return redirect(url_for("headcount.conteo_personal"))
    abort(403)


@headcount_bp.route("/auditoria-sua")
@_login_required_page
def auditoria_sua():
    _require_auditoria()
    fecha_filtro = (request.args.get("fecha_corte") or "").strip()
    historial_rows, historial_fechas = _historial_rows(fecha_filtro or None)
    return render_template(
        "headcount/auditoria_sua.html",
        historial_rows=historial_rows,
        historial_fechas=historial_fechas,
        historial_fecha_filtro=fecha_filtro,
        can_delete=can_delete_headcount_audit(_role()),
    )


@headcount_bp.route("/auditoria-sua/procesar", methods=["POST"])
@_login_required_page
def auditoria_sua_procesar():
    _require_auditoria()
    fecha_corte = (request.form.get("fecha_corte_sua") or "").strip()
    if not fecha_corte:
        flash("Captura la fecha de corte del SUA.", "error")
        return redirect(url_for("headcount.auditoria_sua"))

    archivo = request.files.get("pdf_sua")
    if not archivo or not archivo.filename:
        flash("Selecciona un archivo PDF SUA.", "error")
        return redirect(url_for("headcount.auditoria_sua"))

    pdf_bytes = archivo.read()
    if not pdf_bytes:
        flash("El PDF está vacío.", "error")
        return redirect(url_for("headcount.auditoria_sua"))

    resultado = ejecutar_auditoria_sua(
        pdf_bytes,
        fecha_corte_sua=fecha_corte,
        archivo_nombre=archivo.filename,
    )

    if not resultado.get("ok"):
        if resultado.get("fase") == "conteo":
            return render_template(
                "headcount/auditoria_resultado.html",
                modo="diagnostico",
                diagnostico=resultado.get("diagnostico") or {},
                metadatos=resultado.get("metadatos") or {},
                fecha_corte_sua=fecha_corte,
            )
        flash(resultado.get("error") or "No se pudo procesar el SUA.", "error")
        return redirect(url_for("headcount.auditoria_sua"))

    registro = resultado.get("registro_patronal_sua") or ""
    duplicados = find_duplicate_audit(
        _db_path(),
        fecha_corte_sua=fecha_corte,
        registro_patronal=registro,
    )
    if duplicados and request.form.get("confirmar_duplicado") != "1":
        return render_template(
            "headcount/auditoria_sua.html",
            duplicados=duplicados,
            pending_fecha_corte=fecha_corte,
            pending_registro=registro,
            show_duplicate_warning=True,
        )

    audit_id = str(uuid.uuid4())
    payload = resultado["payload"]
    insert_sua_audit(
        _db_path(),
        audit_id=audit_id,
        user_id=int(g.user["id"]),
        created_at=_now_iso(),
        fecha_corte_sua=fecha_corte,
        archivo_original_nombre=resultado.get("archivo_original_nombre") or archivo.filename,
        registro_patronal_sua=registro,
        razon_social_sua=resultado.get("razon_social_sua") or "",
        rfc_patronal_sua=resultado.get("rfc_patronal_sua") or "",
        periodo_proceso_sua=resultado.get("periodo_proceso_sua") or "",
        fecha_proceso_sua=resultado.get("fecha_proceso_sua") or "",
        total_cotizantes=int(resultado.get("total_cotizantes") or 0),
        trabajadores_extraidos=int(resultado.get("trabajadores_extraidos") or 0),
        total_matches=int(resultado.get("total_matches") or 0),
        total_sin_match=int(resultado.get("total_sin_match") or 0),
        total_warnings=int(resultado.get("total_warnings") or 0),
        resumen=resultado.get("resumen") or {},
        payload=payload,
        hash_archivo=resultado.get("hash_archivo") or "",
    )
    return redirect(url_for("headcount.auditoria_sua_resultado", audit_id=audit_id))


@headcount_bp.route("/auditoria-sua/<audit_id>")
@_login_required_page
def auditoria_sua_resultado(audit_id: str):
    _require_auditoria()
    audit = get_sua_audit(_db_path(), audit_id)
    if not audit:
        abort(404)
    payload = json.loads(audit["detalle_json"])
    resumen = json.loads(audit["resumen_json"])
    detalle_full = payload.get("detalle") or []
    detalle = _apply_filters(detalle_full)
    if detalle_full:
        resumen_clientes, sin_cliente_card, otro_patron_card = build_cliente_cards_for_ui(detalle_full)
    else:
        resumen_clientes = payload.get("resumen_clientes") or []
        sin_cliente_card = payload.get("sin_cliente_card") or {}
        otro_patron_card = payload.get("otro_patron_card") or {}

    resumen = dict(resumen)
    if not resumen.get("desarrollo_inf_mas_6_meses") and not resumen.get("desarrollo_inf_mas_1_anio"):
        metricas_di = calc_metricas_desarrollo_inf(
            resumen.get("fecha_corte_sua") or audit.get("fecha_corte_sua") or "",
            fecha_proceso_sua=resumen.get("fecha_proceso_sua") or audit.get("fecha_proceso_sua") or "",
        )
        resumen["desarrollo_inf_mas_6_meses"] = metricas_di.get("desarrollo_inf_mas_6_meses", 0)
        resumen["desarrollo_inf_mas_1_anio"] = metricas_di.get("desarrollo_inf_mas_1_anio", 0)
    if not resumen.get("registro_patronal_sua"):
        resumen["registro_patronal_sua"] = audit.get("registro_patronal_sua") or ""
    if not resumen.get("periodo_proceso_sua"):
        resumen["periodo_proceso_sua"] = audit.get("periodo_proceso_sua") or ""
    if not resumen.get("fecha_corte_sua"):
        resumen["fecha_corte_sua"] = audit.get("fecha_corte_sua") or ""
    clientes_opts = resumen.get("clientes_detectados_opts")
    if not clientes_opts:
        from modules.headcount.ui_format import clientes_detectados_labels

        clientes_opts = clientes_detectados_labels(detalle_full)
    return render_template(
        "headcount/auditoria_resultado.html",
        modo="resultado",
        audit_id=audit_id,
        resumen=resumen,
        resumen_clientes=resumen_clientes,
        regular_client_cards=resumen_clientes,
        sin_cliente_card=sin_cliente_card,
        detalle=detalle,
        detalle_total=_sua_detalle_total(resumen, detalle_full),
        detalle_filtrado=len(detalle),
        otro_patron_card=otro_patron_card,
        headcount_sin_sua=payload.get("headcount_sin_sua") or [],
        warnings_catalog=payload.get("warnings_catalog") or {},
        clientes_opts=clientes_opts,
        row_meta=audit,
        can_delete=can_delete_headcount_audit(_role()),
        warning_label=warning_label,
        info_estado_label=info_estado_label,
        filtros=_current_filters(),
        hc_display=display_cell,
        hc_display_cliente=display_cliente,
        hc_display_ubicacion=display_ubicacion,
        hc_display_registro_patronal=display_registro_patronal,
        hc_display_periodo_corte=display_periodo_corte,
    )


@headcount_bp.route("/auditoria-sua/<audit_id>/exportar-excel")
@_login_required_page
def auditoria_sua_exportar_excel(audit_id: str):
    _require_auditoria()
    audit = get_sua_audit(_db_path(), audit_id)
    if not audit:
        abort(404)
    payload = json.loads(audit["detalle_json"])
    resumen = dict(payload.get("resumen") or {})
    if not resumen.get("desarrollo_inf_mas_6_meses") and not resumen.get("desarrollo_inf_mas_1_anio"):
        metricas_di = calc_metricas_desarrollo_inf(
            resumen.get("fecha_corte_sua") or audit.get("fecha_corte_sua") or "",
            fecha_proceso_sua=resumen.get("fecha_proceso_sua") or audit.get("fecha_proceso_sua") or "",
        )
        resumen["desarrollo_inf_mas_6_meses"] = metricas_di.get("desarrollo_inf_mas_6_meses", 0)
        resumen["desarrollo_inf_mas_1_anio"] = metricas_di.get("desarrollo_inf_mas_1_anio", 0)
    payload["resumen"] = resumen
    data = build_auditoria_excel_bytes(payload)
    from io import BytesIO

    bio = BytesIO(data)
    bio.seek(0)
    filename = f"auditoria_sua_{audit_id[:8]}.xlsx"
    return send_file(
        bio,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


@headcount_bp.route("/auditoria-sua/<audit_id>/eliminar", methods=["POST"])
@_login_required_page
def auditoria_sua_eliminar(audit_id: str):
    _require_auditoria()
    if not can_delete_headcount_audit(_role()):
        abort(403)
    if delete_sua_audit(_db_path(), audit_id):
        flash("Auditoría eliminada.", "success")
    else:
        flash("No se encontró la auditoría.", "error")
    return redirect(url_for("headcount.auditoria_sua") + "#historial")


@headcount_bp.route("/historial-sua")
@_login_required_page
def historial_sua():
    _require_auditoria()
    return redirect(url_for("headcount.auditoria_sua") + "#historial")


@headcount_bp.route("/cliente")
@_login_required_page
def headcount_cliente():
    return redirect(url_for("headcount.conteo_personal", **request.args))


@headcount_bp.route("/desglose")
@_login_required_page
def headcount_desglose():
    return redirect(url_for("headcount.conteo_personal", **request.args))


@headcount_bp.route("/conteo-personal")
@_login_required_page
def conteo_personal():
    _require_cliente()
    solo_activos = _solo_activos_for_role()
    cliente = (request.args.get("cliente") or "").strip()
    ubicacion = (request.args.get("ubicacion") or "").strip()
    patron = (request.args.get("patron") or "").strip()
    status = (request.args.get("status_operacion") or "").strip()
    busqueda = (request.args.get("q") or "").strip()
    page = max(1, int(request.args.get("page") or 1))
    per_page = 50

    from modules.headcount.snapshot_service import headcount_snapshot_page_context

    db_path = _db_path()
    snap_ctx = headcount_snapshot_page_context(db_path)
    snapshot_missing = not snap_ctx.get("has_data")

    t0 = time.perf_counter()
    base_regs = obtener_registros_headcount(solo_activos=solo_activos, db_path=db_path)
    t_loaded = time.perf_counter()

    registros = base_regs
    if cliente:
        cf = cliente.casefold()
        registros = [r for r in registros if str(r.get("cliente", "")).strip().casefold() == cf]
    if ubicacion:
        uf = ubicacion.casefold()
        registros = [r for r in registros if str(r.get("ubicacion", "")).strip().casefold() == uf]
    if patron and not solo_activos:
        pf = patron.casefold()
        registros = [r for r in registros if str(r.get("patron", "")).strip().casefold() == pf]
    if status and not solo_activos:
        sf = status.upper()
        registros = [r for r in registros if str(r.get("status_operacion", "")).upper() == sf]
    if busqueda:
        from modules.headcount.matching import normalize_text

        q = normalize_text(busqueda)
        registros = [
            r
            for r in registros
            if q in normalize_text(r.get("nombre_completo"))
            or q in normalize_text(r.get("nss"))
            or q in normalize_text(r.get("curp"))
            or q in normalize_text(r.get("cliente"))
        ]

    total_filtered = len(registros)
    start = (page - 1) * per_page
    registros_page = registros[start : start + per_page]

    resumen = resumen_cliente_view(registros)
    if not solo_activos:
        resumen["clientes"] = len({r.get("cliente") for r in registros if r.get("cliente")})
        resumen["ubicaciones"] = len({r.get("ubicacion") for r in registros if r.get("ubicacion")})

    clientes = listar_clientes_headcount(solo_activos=solo_activos, regs=base_regs)
    ubicaciones = listar_ubicaciones_headcount(
        cliente or None,
        solo_activos=solo_activos,
        regs=base_regs,
    )
    t_filtered = time.perf_counter()

    if should_mask_sensitive_data(_role()):
        registros_page = [mask_registro_for_display(r, role=_role()) for r in registros_page]

    headcount_error = None
    if snapshot_missing and not any([cliente, ubicacion, patron, status, busqueda]):
        headcount_error = (
            "Headcount pendiente de actualización. Se requiere generar la primera copia local."
        )
    elif not registros_page and not any([cliente, ubicacion, patron, status, busqueda]) and not snapshot_missing:
        headcount_error = "No hay registros activos disponibles para mostrar."

    current_app.logger.info(
        "conteo_personal: snapshot=%.3fs filter=%.3fs total=%.3fs base_rows=%d visible_rows=%d page=%d",
        t_loaded - t0,
        t_filtered - t_loaded,
        t_filtered - t0,
        len(base_regs),
        len(registros_page),
        page,
    )

    return render_template(
        "headcount/conteo_personal.html",
        registros=registros_page,
        resumen=resumen,
        clientes=clientes,
        ubicaciones=ubicaciones,
        cliente_sel=cliente,
        ubicacion_sel=ubicacion,
        patron_sel=patron,
        status_sel=status,
        busqueda=busqueda,
        solo_activos=solo_activos,
        can_status_filter=not solo_activos,
        can_patron_filter=not solo_activos,
        mask_sensitive=should_mask_sensitive_data(_role()),
        headcount_error=headcount_error,
        snapshot_message=snap_ctx.get("message"),
        snapshot_stale=snap_ctx.get("stale"),
        snapshot_refreshing=snap_ctx.get("refreshing"),
        snapshot_missing=snapshot_missing,
        snapshot_activos=snap_ctx.get("activos_count"),
        snapshot_last_refresh=snap_ctx.get("last_refresh_at"),
        page=page,
        per_page=per_page,
        total_filtered=total_filtered,
        total_pages=max(1, (total_filtered + per_page - 1) // per_page),
    )


@headcount_bp.post("/actualizar-headcount")
@_login_required_page
def actualizar_headcount_headcount():
    _require_cliente()
    from modules.headcount.snapshot_service import refresh_headcount_snapshot

    result = refresh_headcount_snapshot(_db_path(), force=True, source="headcount_manual")
    if result.get("ok"):
        flash(
            f"Headcount actualizado: {result.get('activos_count', 0)} activos, "
            f"{result.get('total_rows', 0)} registros guardados.",
            "success",
        )
    elif result.get("skipped"):
        flash("Headcount ya se está actualizando. Intenta de nuevo en unos minutos.", "warning")
    else:
        flash(
            "No se pudo actualizar Headcount. Se conserva la última copia válida si existía.",
            "error",
        )
    return redirect(request.referrer or url_for("headcount.conteo_personal"))


def _current_filters() -> dict:
    return {
        "cliente": (request.args.get("cliente") or "").strip(),
        "ubicacion": (request.args.get("ubicacion") or "").strip(),
        "ubicacion_provided": "ubicacion" in request.args,
        "match_status": (request.args.get("match_status") or "").strip(),
        "warning": (request.args.get("warning") or "").strip(),
        "movimiento": (request.args.get("movimiento") or "").strip(),
        "status_operacion": (request.args.get("status_operacion") or "").strip(),
        "status_imss": (request.args.get("status_imss") or "").strip(),
        "estado_sua": (request.args.get("estado_sua") or "").strip(),
        "conciliacion": (request.args.get("conciliacion") or "").strip(),
        "solo_hc_sin_sua": request.args.get("solo_hc_sin_sua") == "1",
        "busqueda": (request.args.get("q") or "").strip(),
    }


def _historial_rows(fecha_corte: str | None = None) -> tuple[list[dict], list[str]]:
    db = _db_path()
    rows = list_sua_audits(db, fecha_corte=fecha_corte)
    fechas = sorted({r["fecha_corte_sua"] for r in list_sua_audits(db, limit=500)}, reverse=True)
    out: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            resumen = json.loads(item["resumen_json"])
            item["activos_corte"] = resumen.get("total_sua_activos_al_corte")
            item["bajas_periodo"] = resumen.get("total_sua_bajas_periodo")
        except (json.JSONDecodeError, TypeError, KeyError):
            item["activos_corte"] = None
            item["bajas_periodo"] = None
        out.append(item)
    return out, fechas


def _apply_filters(detalle: list) -> list:
    f = _current_filters()
    if f.get("solo_hc_sin_sua"):
        return []
    return filtrar_detalle(
        detalle,
        cliente=f["cliente"],
        ubicacion=f["ubicacion"],
        ubicacion_provided=bool(f.get("ubicacion_provided")),
        match_status=f["match_status"],
        warning=f["warning"],
        movimiento=f["movimiento"],
        status_operacion=f["status_operacion"],
        status_imss=f["status_imss"],
        estado_sua=f["estado_sua"],
        conciliacion=f["conciliacion"],
        busqueda=f["busqueda"],
    )


@headcount_bp.app_context_processor
def _headcount_template_helpers():
    return {
        "hc_display": display_cell,
        "hc_display_cliente": display_cliente,
        "hc_display_ubicacion": display_ubicacion,
        "hc_display_registro_patronal": display_registro_patronal,
        "hc_display_periodo_corte": display_periodo_corte,
        "hc_display_fecha_ingreso": display_fecha_ingreso,
        "hc_sort_value_date": sort_value_date,
        "hc_sort_value_number": sort_value_number,
    }


def register_headcount(app) -> None:
    import os

    from flask import abort, jsonify, request

    from services.perf_logging import perf_span

    ensure_headcount_tables(str(app.config["DATABASE"]))
    app.register_blueprint(headcount_bp)

    @app.post("/internal/headcount/refresh")
    def internal_headcount_refresh():
        expected = (os.environ.get("INTERNAL_REFRESH_TOKEN") or "").strip()
        token = (
            request.headers.get("X-Internal-Token")
            or request.form.get("token")
            or request.args.get("token")
            or ""
        ).strip()
        if not expected or token != expected:
            abort(403)
        from modules.headcount.snapshot_service import refresh_headcount_snapshot

        with perf_span("headcount.snapshot_internal_refresh"):
            result = refresh_headcount_snapshot(
                str(app.config["DATABASE"]),
                source="internal_cron",
            )
        status = 200 if result.get("ok") else 500
        return jsonify(result), status
