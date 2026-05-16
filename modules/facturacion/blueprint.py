from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from functools import wraps
from io import BytesIO
from pathlib import Path
from typing import Any

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
from werkzeug.utils import secure_filename

from modules.facturacion.config import ALERTA_SET, CLIENTE_POR_CLASIFICAR, OPERATIVO_ORDER, PAGO_ORDER
from modules.facturacion.db import (
    apply_automatic_fechas_from_adjunto,
    dashboard_stats,
    dashboard_stats_anual,
    delete_cliente_credito,
    delete_cliente_plantilla,
    delete_correo_cliente_map,
    delete_factura_soft,
    delete_huerfano,
    delete_razon_social_map,
    distinct_clientes,
    ensure_facturacion_tables,
    fabricar_esqueleto_desde_plantillas,
    find_factura_activa_por_numero_en_texto,
    get_factura,
    get_huerfano,
    get_import_log,
    get_latest_import_log,
    insert_cliente_plantilla,
    insert_factura,
    insert_huerfano,
    insert_import_log,
    insert_nota_credito,
    list_cliente_credito,
    list_cliente_plantillas,
    list_correo_cliente_map,
    list_eventos_for_factura,
    list_facturas_filtradas,
    list_huerfanos,
    list_notas_credito,
    list_razon_social_map,
    refacturar,
    update_cliente_plantilla,
    update_factura,
    upsert_adjunto,
    upsert_cliente_credito,
    upsert_correo_cliente_map,
    upsert_razon_social_map,
)
from modules.facturacion.excel_export import build_facturacion_export_bytes
from modules.facturacion.excel_import import import_facturacion_excel
from modules.facturacion.normalize import extraer_numero_factura_desde_nombre_archivo, validar_factura_payload
from services.app_activity import log_app_activity

_BASE = Path(__file__).resolve().parent.parent.parent
_TEMPLATE_DIR = _BASE / "templates" / "facturacion"

facturacion_bp = Blueprint(
    "facturacion",
    __name__,
    url_prefix="/facturacion",
    template_folder=str(_TEMPLATE_DIR),
)


def _current_user_role() -> str | None:
    """g.user puede ser dict o sqlite3.Row; Row no tiene .get()."""
    user = getattr(g, "user", None)
    if not user:
        return None
    if isinstance(user, dict):
        return user.get("role")
    try:
        return user["role"]
    except (TypeError, KeyError, IndexError):
        return None


def _is_admin() -> bool:
    return _current_user_role() == "admin"


def _login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def _admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        if not _is_admin():
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(current_app.config["DATABASE"])
    conn.row_factory = sqlite3.Row
    ensure_facturacion_tables(conn)
    return conn


def _upload_root() -> Path:
    return Path(current_app.config["DATABASE"]).parent / "facturacion_uploads"


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _allowed_ext(name: str) -> str | None:
    lower = name.lower()
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith(".xml"):
        return "xml"
    return None


def _hash_file(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def register_facturacion(app) -> None:
    upload_root = Path(app.config["DATABASE"]).parent / "facturacion_uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    app.register_blueprint(facturacion_bp)

    @app.template_filter("fx_money_mx")
    def fx_money_mx(value: object) -> str:
        from modules.facturacion.money_format import format_money_mx

        return format_money_mx(value)

    @app.cli.command("facturacion-init-schema")
    def facturacion_init_schema():
        """Crea tablas de facturación si no existen."""
        conn = sqlite3.connect(app.config["DATABASE"])
        try:
            ensure_facturacion_tables(conn)
            conn.commit()
            print("facturacion: tablas OK")
        finally:
            conn.close()


@facturacion_bp.route("/")
@_login_required
def index():
    return redirect(url_for("facturacion.dashboard"))


@facturacion_bp.route("/dashboard")
@_login_required
def dashboard():
    try:
        mes = int(request.args.get("mes") or datetime.now().month)
        anio = int(request.args.get("anio") or datetime.now().year)
    except ValueError:
        mes, anio = datetime.now().month, datetime.now().year
    conn = _db_conn()
    try:
        mstats = dashboard_stats(conn, mes=mes, anio=anio)
        ystats = dashboard_stats_anual(conn, anio=anio)
        por_op_m = mstats.get("por_operativo") or {}
        dough_operativo = [{"label": k, "value": v} for k, v in por_op_m.items()]
        chart_json = json.dumps(
            {
                "mensual": mstats,
                "anual": ystats,
                "dough_operativo": dough_operativo,
                "mes": mes,
                "anio": anio,
            },
            ensure_ascii=False,
        )
    finally:
        conn.close()
    return render_template(
        "facturacion_dashboard.html",
        mstats=mstats,
        ystats=ystats,
        mes=mes,
        anio=anio,
        chart_json=chart_json,
        is_admin=_is_admin(),
    )


@facturacion_bp.route("/facturas")
@_login_required
def facturas_list():
    def _ig(k: str) -> str | None:
        v = (request.args.get(k) or "").strip()
        return v or None

    try:
        mes = int(_ig("mes") or 0) or None
    except ValueError:
        mes = None
    try:
        anio = int(_ig("anio") or 0) or None
    except ValueError:
        anio = None
    cliente_eq = None
    cliente_arg = _ig("cliente")
    if request.args.get("por_clasificar") == "1":
        if not _is_admin():
            abort(403)
        cliente_eq = CLIENTE_POR_CLASIFICAR
        cliente_arg = None
    solo_pendientes = request.args.get("solo_pendientes") == "1"
    conn = _db_conn()
    try:
        rows = list_facturas_filtradas(
            conn,
            mes=mes,
            anio=anio,
            cliente=cliente_arg,
            cliente_eq=cliente_eq,
            estatus_operativo=_ig("estatus_operativo"),
            estatus_pago=_ig("estatus_pago"),
            alerta=_ig("alerta"),
            q_numero=_ig("q"),
            q_po=_ig("po"),
            solo_pre_factura=solo_pendientes,
        )
        clientes = distinct_clientes(conn)
    finally:
        conn.close()
    return render_template(
        "facturacion_facturas.html",
        rows=rows,
        clientes=clientes,
        filtros={
            "mes": mes,
            "anio": anio,
            "cliente": cliente_arg,
            "por_clasificar": bool(cliente_eq),
            "solo_pendientes": solo_pendientes,
            "estatus_operativo": _ig("estatus_operativo"),
            "estatus_pago": _ig("estatus_pago"),
            "alerta": _ig("alerta"),
            "q": _ig("q"),
            "po": _ig("po"),
        },
        operativos=OPERATIVO_ORDER,
        pagos=PAGO_ORDER,
        alertas=sorted(ALERTA_SET),
        is_admin=_is_admin(),
    )


@facturacion_bp.route("/catalogo", methods=["GET", "POST"])
@_login_required
@_admin_required
def facturacion_catalogo():
    conn = _db_conn()
    try:
        if request.method == "POST":
            act = (request.form.get("action") or "").strip()
            now = _now_iso()
            try:
                if act == "add_razon":
                    upsert_razon_social_map(
                        conn,
                        razon_social=(request.form.get("razon_social") or "").strip(),
                        cliente_principal=(request.form.get("cliente_principal") or "").strip(),
                        now=now,
                    )
                    flash("Mapeo razón social → cliente guardado.", "success")
                elif act == "edit_razon":
                    rid = int(request.form.get("id") or 0)
                    upsert_razon_social_map(
                        conn,
                        razon_social=(request.form.get("razon_social") or "").strip(),
                        cliente_principal=(request.form.get("cliente_principal") or "").strip(),
                        now=now,
                        row_id=rid,
                    )
                    flash("Mapeo actualizado.", "success")
                elif act == "del_razon":
                    delete_razon_social_map(conn, int(request.form.get("id") or 0))
                    flash("Mapeo eliminado.", "success")
                elif act == "add_credito":
                    upsert_cliente_credito(
                        conn,
                        cliente_principal=(request.form.get("cliente_principal") or "").strip(),
                        dias_credito=int(request.form.get("dias_credito") or 0),
                        now=now,
                    )
                    flash("Días de crédito guardados.", "success")
                elif act == "del_credito":
                    delete_cliente_credito(conn, (request.form.get("cliente_principal") or "").strip())
                    flash("Cliente eliminado del catálogo de crédito.", "success")
                elif act == "add_correo_map":
                    upsert_correo_cliente_map(
                        conn,
                        tipo=(request.form.get("tipo_correo") or "EMAIL").strip(),
                        valor=(request.form.get("valor_correo") or "").strip(),
                        cliente_principal=(request.form.get("cliente_principal_correo") or "").strip(),
                        now=now,
                    )
                    flash("Mapeo correo/dominio → cliente guardado.", "success")
                elif act == "edit_correo_map":
                    upsert_correo_cliente_map(
                        conn,
                        tipo=(request.form.get("tipo_correo") or "").strip(),
                        valor=(request.form.get("valor_correo") or "").strip(),
                        cliente_principal=(request.form.get("cliente_principal_correo") or "").strip(),
                        now=now,
                        row_id=int(request.form.get("id") or 0),
                    )
                    flash("Mapeo actualizado.", "success")
                elif act == "del_correo_map":
                    delete_correo_cliente_map(conn, int(request.form.get("id") or 0))
                    flash("Mapeo eliminado.", "success")
                else:
                    flash("Acción no reconocida.", "error")
            except (ValueError, TypeError) as exc:
                flash(str(exc) or "Datos inválidos.", "error")
            conn.commit()
            return redirect(url_for("facturacion.facturacion_catalogo"))
        razones = list_razon_social_map(conn)
        creditos = list_cliente_credito(conn)
        correos_map = list_correo_cliente_map(conn)
    finally:
        conn.close()
    return render_template(
        "facturacion_catalogo.html",
        razones=razones,
        creditos=creditos,
        correos_map=correos_map,
        is_admin=_is_admin(),
    )


@facturacion_bp.route("/plantillas-cliente", methods=["GET", "POST"])
@_login_required
@_admin_required
def plantillas_cliente():
    conn = _db_conn()
    filtro_cli = (request.args.get("cliente") or "").strip() or None
    try:
        if request.method == "POST":
            act = (request.form.get("action") or "").strip()
            now = _now_iso()
            try:
                if act == "save":
                    row_id = int(request.form.get("id") or 0)
                    orden = int(request.form.get("orden") or 0)
                    cli = (request.form.get("cliente") or "").strip()
                    if not cli:
                        flash("Cliente es obligatorio.", "error")
                    else:
                        common = dict(
                            cliente=cli,
                            orden=orden,
                            clasificacion=(request.form.get("clasificacion") or "").strip() or None,
                            planta_servicio=(request.form.get("planta_servicio") or "").strip() or None,
                            usuario_contacto=(request.form.get("usuario_contacto") or "").strip() or None,
                            razon_social=(request.form.get("razon_social") or "").strip() or None,
                            responsable_interno=(request.form.get("responsable_interno") or "").strip() or None,
                            requiere_portal=1 if request.form.get("requiere_portal") in {"1", "on", "true"} else 0,
                            notas_internas=(request.form.get("notas_internas") or "").strip() or None,
                            now=now,
                        )
                        if row_id:
                            update_cliente_plantilla(conn, row_id, **common)
                            flash("Línea de plantilla actualizada.", "success")
                        else:
                            insert_cliente_plantilla(conn, **common)
                            flash("Línea de plantilla agregada.", "success")
                        filtro_cli = cli
                elif act == "del":
                    rid = int(request.form.get("id") or 0)
                    if delete_cliente_plantilla(conn, rid):
                        flash("Línea eliminada.", "success")
                    else:
                        flash("No se puede eliminar: hay facturas activas vinculadas a esta plantilla.", "error")
                else:
                    flash("Acción no reconocida.", "error")
            except (ValueError, TypeError) as exc:
                flash(str(exc) or "Datos inválidos.", "error")
            conn.commit()
            q = {"cliente": filtro_cli} if filtro_cli else {}
            return redirect(url_for("facturacion.plantillas_cliente", **q))
        edit_row = None
        eid = (request.args.get("edit") or "").strip()
        if eid.isdigit():
            r = conn.execute(
                "SELECT * FROM facturacion_cliente_plantilla WHERE id = ?",
                (int(eid),),
            ).fetchone()
            if r:
                edit_row = dict(r)
        rows = list_cliente_plantillas(conn, cliente=filtro_cli)
        clientes = distinct_clientes(conn)
    finally:
        conn.close()
    return render_template(
        "facturacion_plantillas.html",
        rows=rows,
        clientes=clientes,
        filtro_cli=filtro_cli,
        edit_row=edit_row,
        is_admin=_is_admin(),
    )


@facturacion_bp.route("/plantillas-cliente/generar-mes", methods=["POST"])
@_login_required
@_admin_required
def plantillas_generar_mes():
    try:
        mes = int(request.form.get("mes") or 0)
        anio = int(request.form.get("anio") or 0)
    except (TypeError, ValueError):
        mes, anio = 0, 0
    if mes < 1 or mes > 12 or anio < 2000:
        flash("Indica mes (1–12) y año válidos para generar el esqueleto.", "error")
        return redirect(request.referrer or url_for("facturacion.dashboard"))
    cliente = (request.form.get("cliente") or "").strip() or None
    conn = _db_conn()
    try:
        res = fabricar_esqueleto_desde_plantillas(
            conn,
            mes=mes,
            anio=anio,
            cliente=cliente,
            user_id=int(g.user["id"]),
            now=_now_iso(),
        )
        conn.commit()
        log_app_activity(
            str(current_app.config["DATABASE"]),
            user_id=int(g.user["id"]),
            module="facturacion",
            action="plantillas_generar_mes",
            status="ok",
            ref=f"{anio}-{mes:02d}",
        )
        flash(
            f"Esqueleto del periodo: {res['inserted']} filas nuevas, "
            f"{res['skipped_y_existia']} ya existían por plantilla, "
            f"{res['plantillas_encontradas']} líneas de plantilla consideradas.",
            "success",
        )
    finally:
        conn.close()
    dest = (request.form.get("next") or "facturas").strip()
    if dest == "dashboard":
        return redirect(url_for("facturacion.dashboard", mes=mes, anio=anio))
    return redirect(
        url_for(
            "facturacion.facturas_list",
            mes=mes,
            anio=anio,
            **({"cliente": cliente} if cliente else {}),
        )
    )


def _parse_factura_form(form) -> dict[str, Any]:
    def fnum(k: str):
        v = (form.get(k) or "").strip()
        if not v:
            return None
        try:
            return float(v.replace(",", ""))
        except ValueError:
            return None

    es_seg = form.get("es_seguimiento") in {"1", "on", "true", "yes"}
    num_raw = (form.get("numero_factura") or "").strip()
    if es_seg:
        num_raw = ""
    es_pre = 1 if es_seg or not num_raw else 0

    alertas_raw = form.getlist("alertas")
    alertas = [a.strip().upper() for a in alertas_raw if a.strip().upper() in ALERTA_SET]
    return {
        "mes": int(form.get("mes") or 0),
        "anio": int(form.get("anio") or 0),
        "asistencia_mes": int(form["asistencia_mes"]) if (form.get("asistencia_mes") or "").strip() else None,
        "asistencia_anio": int(form["asistencia_anio"]) if (form.get("asistencia_anio") or "").strip() else None,
        "cliente": (form.get("cliente") or "").strip(),
        "razon_social": (form.get("razon_social") or "").strip() or None,
        "planta_servicio": (form.get("planta_servicio") or "").strip() or None,
        "usuario_contacto": (form.get("usuario_contacto") or "").strip() or None,
        "responsable_interno": (form.get("responsable_interno") or "").strip() or None,
        "numero_factura": num_raw or None,
        "es_pre_factura": es_pre,
        "po_oc": (form.get("po_oc") or "").strip() or None,
        "requiere_portal": 1 if form.get("requiere_portal") in {"1", "on", "true", "yes"} else 0,
        "subtotal": fnum("subtotal"),
        "iva": fnum("iva"),
        "total": fnum("total"),
        "fecha_factura": (form.get("fecha_factura") or "").strip() or None,
        "fecha_vencimiento": (form.get("fecha_vencimiento") or "").strip() or None,
        "estatus_operativo": (form.get("estatus_operativo") or "").strip(),
        "estatus_pago": (form.get("estatus_pago") or "PENDIENTE").strip(),
        "alertas": alertas,
        "comentarios": (form.get("comentarios") or "").strip() or None,
    }


@facturacion_bp.route("/facturas/nuevo", methods=["GET", "POST"])
@_login_required
@_admin_required
def factura_nuevo():
    if request.method == "POST":
        data = _parse_factura_form(request.form)
        ok, err = validar_factura_payload(data)
        if not ok:
            flash(err or "Validación fallida.", "error")
        else:
            conn = _db_conn()
            try:
                insert_factura(conn, data, user_id=int(g.user["id"]), now=_now_iso())
                conn.commit()
                log_app_activity(
                    str(current_app.config["DATABASE"]),
                    user_id=int(g.user["id"]),
                    module="facturacion",
                    action="crear_factura",
                    status="ok",
                    ref=data.get("numero_factura") or f"seguimiento_pre_{data.get('cliente')}",
                )
                if int(data.get("es_pre_factura") or 0):
                    flash("Registro de seguimiento guardado (sin número de factura).", "success")
                else:
                    flash("Factura creada.", "success")
                return redirect(url_for("facturacion.facturas_list"))
            except sqlite3.IntegrityError:
                conn.rollback()
                flash("Ya existe una factura activa con ese número, cliente y periodo.", "error")
            finally:
                conn.close()
    return render_template(
        "facturacion_form.html",
        row=None,
        operativos=OPERATIVO_ORDER,
        pagos=PAGO_ORDER,
        alertas=sorted(ALERTA_SET),
    )


@facturacion_bp.route("/facturas/<int:fid>")
@_login_required
def factura_detail(fid: int):
    conn = _db_conn()
    try:
        row = get_factura(conn, fid)
        if not row:
            abort(404)
        eventos = list_eventos_for_factura(conn, fid)
        cadena: list[dict[str, Any]] = []
        cur = row
        seen: set[int] = set()
        while cur and cur.get("factura_original_id") and int(cur["factura_original_id"]) not in seen:
            pid = int(cur["factura_original_id"])
            seen.add(pid)
            prev = get_factura(conn, pid)
            if prev:
                cadena.append(dict(prev))
                cur = prev
            else:
                break
        cadena.reverse()
        reemplazo = None
        rid = row.get("factura_reemplazada_por_id")
        if rid:
            reemplazo = get_factura(conn, int(rid))
    finally:
        conn.close()
    return render_template(
        "facturacion_detail.html",
        row=row,
        eventos=eventos,
        cadena=cadena,
        reemplazo=reemplazo,
        is_admin=_is_admin(),
    )


@facturacion_bp.route("/facturas/<int:fid>/editar", methods=["GET", "POST"])
@_login_required
@_admin_required
def factura_editar(fid: int):
    conn = _db_conn()
    try:
        row = get_factura(conn, fid)
        if not row or not int(row["es_factura_activa"]):
            abort(404)
        if request.method == "POST":
            data = _parse_factura_form(request.form)
            data["mes"] = int(data["mes"] or row["mes"])
            data["anio"] = int(data["anio"] or row["anio"])
            ok, err = validar_factura_payload(data)
            if not ok:
                flash(err or "Validación fallida.", "error")
            else:
                try:
                    update_factura(conn, fid, data, user_id=int(g.user["id"]), now=_now_iso())
                    conn.commit()
                    flash("Cambios guardados.", "success")
                    return redirect(url_for("facturacion.factura_detail", fid=fid))
                except sqlite3.IntegrityError:
                    conn.rollback()
                    flash("Conflicto con otra factura activa (número + cliente + periodo).", "error")
        row = get_factura(conn, fid)
    finally:
        conn.close()
    return render_template(
        "facturacion_form.html",
        row=row,
        operativos=OPERATIVO_ORDER,
        pagos=PAGO_ORDER,
        alertas=sorted(ALERTA_SET),
    )


@facturacion_bp.route("/facturas/<int:fid>/eliminar", methods=["POST"])
@_login_required
@_admin_required
def factura_eliminar(fid: int):
    conn = _db_conn()
    try:
        if delete_factura_soft(conn, fid, user_id=int(g.user["id"]), now=_now_iso()):
            conn.commit()
            flash("Factura desactivada.", "success")
        else:
            flash("No se pudo eliminar.", "error")
    finally:
        conn.close()
    return redirect(url_for("facturacion.facturas_list"))


@facturacion_bp.route("/facturas/<int:fid>/listo", methods=["POST"])
@_login_required
@_admin_required
def factura_marcar_listo(fid: int):
    conn = _db_conn()
    try:
        row = get_factura(conn, fid)
        if not row:
            abort(404)
        update_factura(
            conn,
            fid,
            {"estatus_operativo": "LISTO"},
            user_id=int(g.user["id"]),
            now=_now_iso(),
        )
        conn.commit()
        flash("Marcada como LISTO.", "success")
    finally:
        conn.close()
    return redirect(url_for("facturacion.factura_detail", fid=fid))


@facturacion_bp.route("/facturas/<int:fid>/refacturar", methods=["GET", "POST"])
@_login_required
@_admin_required
def factura_refacturar(fid: int):
    conn = _db_conn()
    old = None
    try:
        old = get_factura(conn, fid)
        if not old or not int(old["es_factura_activa"]):
            abort(404)
        if request.method == "POST":
            motivo = (request.form.get("motivo") or "").strip()
            if len(motivo) < 3:
                flash("Describe el motivo de la refacturación.", "error")
            else:
                data = _parse_factura_form(request.form)
                ok, err = validar_factura_payload(data)
                if not ok:
                    flash(err or "Validación fallida.", "error")
                else:
                    nid = refacturar(conn, fid, data, motivo=motivo, user_id=int(g.user["id"]), now=_now_iso())
                    if nid:
                        conn.commit()
                        flash("Refacturación registrada.", "success")
                        return redirect(url_for("facturacion.factura_detail", fid=nid))
                    flash("No se pudo refacturar.", "error")
    finally:
        conn.close()
    return render_template(
        "facturacion_refacturar.html",
        base=old,
        operativos=OPERATIVO_ORDER,
        pagos=PAGO_ORDER,
        alertas=sorted(ALERTA_SET),
    )


@facturacion_bp.route("/upload", methods=["POST"])
@_login_required
@_admin_required
def upload_adjuntos():
    mes = request.form.get("mes")
    anio = request.form.get("anio")
    mes_i = int(mes) if mes and str(mes).isdigit() else None
    anio_i = int(anio) if anio and str(anio).isdigit() else None
    files = request.files.getlist("archivos")
    if not files:
        flash("Selecciona al menos un archivo.", "error")
        return redirect(url_for("facturacion.facturas_list"))
    root = _upload_root()
    root.mkdir(parents=True, exist_ok=True)
    conn = _db_conn()
    linked = 0
    orphans = 0
    try:
        forced_id: int | None = None
        raw_force = (request.form.get("factura_id") or "").strip()
        if raw_force.isdigit():
            frow = get_factura(conn, int(raw_force))
            if frow and int(frow["es_factura_activa"]):
                forced_id = int(raw_force)
        for f in files:
            if not f or not f.filename:
                continue
            ext = _allowed_ext(f.filename)
            if not ext:
                flash(f"Extensión no permitida: {f.filename}", "error")
                continue
            data = f.read()
            if not data:
                continue
            h = _hash_file(data)
            safe = secure_filename(f.filename) or f"upload.{ext}"
            stem = Path(safe).stem[:80]
            stored = f"{h[:16]}_{stem}.{ext}"
            dest = root / stored
            dest.write_bytes(data)
            rel = str(dest)
            match_id = forced_id
            if match_id is None:
                match_id = find_factura_activa_por_numero_en_texto(conn, safe, anio=anio_i, mes=mes_i)
            if match_id is None:
                cand = extraer_numero_factura_desde_nombre_archivo(safe)
                if cand:
                    match_id = find_factura_activa_por_numero_en_texto(conn, cand, anio=anio_i, mes=mes_i)
            if match_id:
                upsert_adjunto(
                    conn,
                    factura_id=match_id,
                    tipo=ext,
                    stored_filename=stored,
                    file_path=rel,
                    file_hash=h,
                    original_name=f.filename[:240],
                    user_id=int(g.user["id"]),
                    now=_now_iso(),
                )
                linked += 1
                try:
                    apply_automatic_fechas_from_adjunto(
                        conn,
                        factura_id=match_id,
                        file_bytes=data,
                        ext=ext,
                        user_id=int(g.user["id"]),
                        now=_now_iso(),
                    )
                except Exception:
                    current_app.logger.exception("facturacion: fechas desde adjunto")
            else:
                insert_huerfano(
                    conn,
                    stored_path=rel,
                    original_name=f.filename[:240],
                    ext=ext,
                    file_hash=h,
                    detected_numero=extraer_numero_factura_desde_nombre_archivo(safe),
                    user_id=int(g.user["id"]),
                    now=_now_iso(),
                )
                orphans += 1
        conn.commit()
        flash(f"Carga terminada: {linked} relacionados, {orphans} sin relación.", "success")
        redir_args: dict[str, Any] = {}
        if mes_i is not None:
            redir_args["mes"] = mes_i
        if anio_i is not None:
            redir_args["anio"] = anio_i
        return redirect(url_for("facturacion.facturas_list", **redir_args))
    finally:
        conn.close()


@facturacion_bp.route("/adjunto/<int:fid>/<tipo>")
@_login_required
def descargar_adjunto(fid: int, tipo: str):
    if tipo not in {"pdf", "xml"}:
        abort(404)
    conn = _db_conn()
    try:
        row = conn.execute(
            "SELECT * FROM facturacion_adjuntos WHERE factura_id = ? AND tipo = ?",
            (fid, tipo),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        abort(404)
    p = Path(row["file_path"])
    if not p.is_file():
        abort(404)
    return send_file(p, as_attachment=True, download_name=row["original_name"] or p.name)


@facturacion_bp.route("/huerfanos")
@_login_required
@_admin_required
def huerfanos():
    conn = _db_conn()
    try:
        rows = list_huerfanos(conn)
    finally:
        conn.close()
    return render_template(
        "facturacion_huerfanos.html",
        rows=rows,
    )


@facturacion_bp.route("/huerfanos/<int:hid>/relacionar", methods=["POST"])
@_login_required
@_admin_required
def huerfano_relacionar(hid: int):
    fid = int(request.form.get("factura_id") or 0)
    conn = _db_conn()
    try:
        h = get_huerfano(conn, hid)
        if not h or not fid:
            flash("Datos inválidos.", "error")
            return redirect(url_for("facturacion.huerfanos"))
        p = Path(h["stored_path"])
        if not p.is_file():
            flash("Archivo físico no encontrado.", "error")
            return redirect(url_for("facturacion.huerfanos"))
        upsert_adjunto(
            conn,
            factura_id=fid,
            tipo=str(h["ext"]),
            stored_filename=p.name,
            file_path=str(p),
            file_hash=h.get("file_hash"),
            original_name=h["original_name"],
            user_id=int(g.user["id"]),
            now=_now_iso(),
        )
        try:
            raw = p.read_bytes()
            apply_automatic_fechas_from_adjunto(
                conn,
                factura_id=fid,
                file_bytes=raw,
                ext=str(h["ext"]),
                user_id=int(g.user["id"]),
                now=_now_iso(),
            )
        except Exception:
            current_app.logger.exception("facturacion: fechas desde adjunto huérfano")
        delete_huerfano(conn, hid)
        conn.commit()
        flash("Archivo relacionado.", "success")
    finally:
        conn.close()
    return redirect(url_for("facturacion.huerfanos"))


@facturacion_bp.route("/export")
@_login_required
@_admin_required
def export_excel():
    mes = int(request.args.get("mes") or datetime.now().month)
    anio = int(request.args.get("anio") or datetime.now().year)
    conn = _db_conn()
    try:
        data = build_facturacion_export_bytes(conn, mes=mes, anio=anio)
    finally:
        conn.close()
    meses = [
        "",
        "ENERO",
        "FEBRERO",
        "MARZO",
        "ABRIL",
        "MAYO",
        "JUNIO",
        "JULIO",
        "AGOSTO",
        "SEPTIEMBRE",
        "OCTUBRE",
        "NOVIEMBRE",
        "DICIEMBRE",
    ]
    label = meses[mes] if 1 <= mes <= 12 else str(mes)
    fname = f"Facturacion_ProClean_{label}_{anio}.xlsx"
    bio = BytesIO(data)
    bio.seek(0)
    return send_file(
        bio,
        as_attachment=True,
        download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@facturacion_bp.route("/import/ultimo")
@_login_required
@_admin_required
def import_ultimo():
    conn = _db_conn()
    try:
        row = get_latest_import_log(conn)
    finally:
        conn.close()
    if not row:
        flash("Aún no hay importaciones registradas.", "error")
        return redirect(url_for("facturacion.import_excel"))
    return redirect(url_for("facturacion.import_result", log_id=int(row["id"])))


@facturacion_bp.route("/import", methods=["GET", "POST"])
@_login_required
@_admin_required
def import_excel():
    if request.method == "POST":
        f = request.files.get("archivo")
        if not f or not f.filename:
            flash("Selecciona un archivo Excel.", "error")
            return redirect(url_for("facturacion.import_excel"))
        try:
            anio = int(request.form.get("anio") or datetime.now().year)
        except ValueError:
            anio = datetime.now().year
        fname = (f.filename or "").strip() or None
        content = f.read()
        conn = _db_conn()
        log_id: int | None = None
        try:
            res = import_facturacion_excel(
                conn,
                content,
                anio_default=anio,
                user_id=int(g.user["id"]),
                now=_now_iso(),
                original_filename=fname,
            )
            log_id = insert_import_log(
                conn,
                res,
                user_id=int(g.user["id"]),
                now=_now_iso(),
                original_filename=fname,
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            flash(f"Error al importar: {exc}", "error")
            return redirect(url_for("facturacion.import_excel"))
        finally:
            conn.close()
        flash("Importación finalizada. Revisa el resumen detallado.", "success")
        return redirect(url_for("facturacion.import_result", log_id=int(log_id)))

    return render_template("facturacion_import.html")


@facturacion_bp.route("/import/resultado/<int:log_id>")
@_login_required
@_admin_required
def import_result(log_id: int):
    conn = _db_conn()
    try:
        log_row = get_import_log(conn, log_id)
    finally:
        conn.close()
    if not log_row:
        abort(404)
    return render_template("facturacion_import_result.html", log=log_row)


@facturacion_bp.route("/revision/por-clasificar")
@_login_required
@_admin_required
def revision_por_clasificar():
    return redirect(url_for("facturacion.facturas_list", por_clasificar="1"))


@facturacion_bp.route("/notas-credito", methods=["GET", "POST"])
@_login_required
@_admin_required
def notas_credito():
    if request.method == "POST":
        conn = _db_conn()
        try:
            insert_nota_credito(
                conn,
                {
                    "cliente": (request.form.get("cliente") or "").strip() or None,
                    "numero_nota": (request.form.get("numero_nota") or "").strip() or None,
                    "factura_id": int(request.form["factura_id"])
                    if (request.form.get("factura_id") or "").strip().isdigit()
                    else None,
                    "monto": float(request.form["monto"]) if (request.form.get("monto") or "").strip() else None,
                    "comentario": (request.form.get("comentario") or "").strip() or None,
                    "fecha": (request.form.get("fecha") or "").strip() or _now_iso()[:10],
                    "mes": int(request.form["mes"]) if (request.form.get("mes") or "").strip().isdigit() else None,
                    "anio": int(request.form["anio"]) if (request.form.get("anio") or "").strip().isdigit() else None,
                },
                user_id=int(g.user["id"]),
                now=_now_iso(),
            )
            conn.commit()
            flash("Nota de crédito registrada.", "success")
        except (ValueError, sqlite3.Error) as exc:
            conn.rollback()
            flash(str(exc), "error")
        finally:
            conn.close()
        return redirect(url_for("facturacion.notas_credito"))
    conn = _db_conn()
    try:
        rows = list_notas_credito(conn)
    finally:
        conn.close()
    return render_template("facturacion_notas_credito.html", rows=rows)
