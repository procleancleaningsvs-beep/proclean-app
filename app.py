from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
from datetime import datetime
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


def _app_timezone() -> ZoneInfo:
    name = (os.environ.get("APP_TIMEZONE") or "America/Mexico_City").strip() or "America/Mexico_City"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("America/Mexico_City")


def now_in_app_tz() -> datetime:
    return datetime.now(_app_timezone())

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_limiter import Limiter
from flask_limiter.errors import RateLimitExceeded
from flask_limiter.util import get_remote_address
from werkzeug.security import check_password_hash, generate_password_hash

from modules.roles_access import (
    VALID_USER_ROLES,
    can_access_checkid,
    can_access_comparativo,
    can_access_imss_movimientos,
    login_home_endpoint,
    nav_show_administration,
    nav_show_carrier_vitroflex,
    nav_show_checkid,
    nav_show_comparativo,
    nav_show_headcount_auditoria,
    nav_show_headcount_cliente,
    nav_show_headcount_conteo,
    nav_show_headcount_module,
    nav_show_facturacion,
    nav_show_finiquitos,
    nav_show_imss_movimientos,
    nav_show_nomina_dashboard,
    nav_show_nomina_hub,
    nav_show_nomina_module,
    normalized_role,
)

from generator import TEMPLATE_FILENAMES, generate_constancia, movimientos_from_form, parse_movimientos
from services.checkid_cache import get_cached_busqueda, set_cached_busqueda
from services.checkid_client import (
    CheckIDClient,
    CheckIDConfigurationError,
    is_valid_checkid_term,
    normalize_termino_busqueda,
)
from services.app_activity import build_admin_dashboard, list_activity_feed, log_app_activity
from services.checkid_history import (
    delete_checkid_query_by_id,
    find_checkid_history_match_by_rfc_curp,
    get_checkid_detail_bundle_for_row,
    get_checkid_success_row_by_id,
    list_checkid_queries_global,
    persist_checkid_query,
    update_checkid_success_row_from_response,
)

logger = logging.getLogger(__name__)

CHECKID_BUSCAR_RATE_LIMIT = os.environ.get("CHECKID_BUSCAR_RATE_LIMIT", "20 per minute")


def checkid_rate_limit_key() -> str:
    """Clave por usuario autenticado o IP si no hay sesión."""
    u = getattr(g, "user", None)
    if u is not None:
        return f"checkid:uid:{u['id']}"
    return get_remote_address()


limiter = Limiter(
    key_func=checkid_rate_limit_key,
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
    default_limits=[],
)


def _log_checkid_struct(level: str, event: str, **fields: object) -> None:
    """Log en una línea JSON (sin credenciales)."""
    payload = {"event": f"checkid_{event}", **fields}
    line = json.dumps(payload, ensure_ascii=False, default=str)
    if level == "error":
        logger.error("%s", line)
    else:
        logger.info("%s", line)

BASE_DIR = Path(__file__).resolve().parent
BUNDLED_TEMPLATES_DIR = BASE_DIR / "docx_templates"

# Persistencia (Railway Volume en /app/data)
# - Si existen rutas específicas via env vars, se respetan para compatibilidad local.
# - Si no, se intenta usar /app/data (o PROCLEAN_DATA_DIR) cuando está disponible,
#   y si no, se cae a rutas locales del proyecto.
_env_instance_dir = os.environ.get("PROCLEAN_INSTANCE_DIR")
_env_generated_dir = os.environ.get("PROCLEAN_GENERATED_DIR")
_env_templates_dir = os.environ.get("PROCLEAN_TEMPLATES_DIR")

_local_instance_dir = BASE_DIR / "instance"
_local_generated_dir = BASE_DIR / "generated"
_local_templates_dir = BUNDLED_TEMPLATES_DIR

_persist_base_dir = Path(os.environ.get("PROCLEAN_DATA_DIR", "/app/data"))
_can_use_persist = (os.name != "nt") and (_persist_base_dir.exists() or os.environ.get("PROCLEAN_DATA_DIR"))

if _env_instance_dir or _env_generated_dir or _env_templates_dir:
    INSTANCE_DIR = Path(_env_instance_dir) if _env_instance_dir else _local_instance_dir
    GENERATED_DIR = Path(_env_generated_dir) if _env_generated_dir else _local_generated_dir
    DOCX_TEMPLATES_DIR = Path(_env_templates_dir) if _env_templates_dir else _local_templates_dir
else:
    if _can_use_persist:
        INSTANCE_DIR = _persist_base_dir / "instance"
        GENERATED_DIR = _persist_base_dir / "generated"
        DOCX_TEMPLATES_DIR = _persist_base_dir / "docx_templates"
    else:
        INSTANCE_DIR = _local_instance_dir
        GENERATED_DIR = _local_generated_dir
        DOCX_TEMPLATES_DIR = _local_templates_dir

ADMIN_CREDENTIALS_PATH = INSTANCE_DIR / "admin_credentials.txt"
SECRET_KEY_PATH = INSTANCE_DIR / "secret_key.txt"
DB_PATH = INSTANCE_DIR / "proclean.db"

APP_NAME = "ProClean App"


def _max_content_bytes() -> int:
    """Límite global de subida (MB). Para expedientes con PDF/imágenes, sube p. ej. PROCLEAN_MAX_CONTENT_MB=32."""
    try:
        mb = int(os.environ.get("PROCLEAN_MAX_CONTENT_MB", "4"))
    except ValueError:
        mb = 4
    return max(1, mb) * 1024 * 1024


def _checkid_payload_source(payload: dict) -> str:
    """Indica qué campo aportó el término (sin registrar el valor)."""
    if payload.get("termino"):
        return "termino"
    if payload.get("rfc"):
        return "rfc"
    if payload.get("curp"):
        return "curp"
    if payload.get("termino_busqueda"):
        return "termino_busqueda"
    return "none"


def _strip_secrets_for_log(obj: object) -> object:
    """Copia superficial/recursiva para logs: oculta ApiKey y campos sensibles similares."""
    if isinstance(obj, dict):
        out: dict[object, object] = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in ("apikey", "api_key", "password", "secret", "token", "authorization"):
                out[k] = "[redacted]"
            else:
                out[k] = _strip_secrets_for_log(v)
        return out
    if isinstance(obj, list):
        return [_strip_secrets_for_log(i) for i in obj]
    return obj


def _safe_persist_checkid_history(termino_log: str, body: dict) -> None:
    if not getattr(g, "user", None):
        return
    try:
        persist_checkid_query(str(DB_PATH), g.user["id"], termino_log, body)
    except Exception:
        logger.exception("checkid_query_log persist failed")
        return
    try:
        if body.get("ok"):
            st = "ok"
        elif body.get("internal"):
            st = "fail"
        else:
            st = "error"
        code = body.get("error_code")
        ref = str(code) if code is not None else None
        log_app_activity(
            str(DB_PATH),
            user_id=int(g.user["id"]),
            module="checkid",
            action="consulta",
            status=st,
            ref=ref,
        )
    except Exception:
        logger.exception("activity log after checkid persist failed")


def checkid_http_response(result: dict) -> tuple[dict, int]:
    """
    Convierte el dict del cliente CheckID en (cuerpo JSON, status HTTP).

    El cliente ya devuelve siempre: ok, error_code, message, http_status, internal, data.
    - Respuestas con internal=True (VALIDATION_ERROR, TIMEOUT, NETWORK_ERROR, etc.) → HTTP 4xx/5xx.
    - Respuestas CheckID (internal=False, códigos E*) → HTTP 200; el detalle va en el JSON.

    Ejemplos de status HTTP devueltos aquí:
    - VALIDATION_ERROR → 400
    - TIMEOUT → 504
    - NETWORK_ERROR → 502
    - Otro error interno no listado → 500
    - Éxito o error de negocio CheckID → 200
    """
    if result.get("internal"):
        code = result.get("error_code") or ""
        status_map = {
            "VALIDATION_ERROR": 400,
            "TIMEOUT": 504,
            "NETWORK_ERROR": 502,
        }
        return result, status_map.get(code, 500)
    return result, 200


def create_app() -> Flask:
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    ensure_default_templates()

    app = Flask(__name__, instance_path=str(INSTANCE_DIR))
    app.config.update(
        APP_NAME=APP_NAME,
        DATABASE=str(DB_PATH),
        GENERATED_DIR=str(GENERATED_DIR),
        DOCX_TEMPLATES_DIR=str(DOCX_TEMPLATES_DIR),
        SECRET_KEY=get_or_create_secret_key(),
        MAX_CONTENT_LENGTH=_max_content_bytes(),
    )

    limiter.init_app(app)

    @app.errorhandler(RateLimitExceeded)
    def handle_checkid_rate_limit_exceeded(_exc: RateLimitExceeded):
        _log_checkid_struct(
            "info",
            "rate_limited",
            path=request.path,
            user_id=g.user["id"] if g.user else None,
        )
        return (
            jsonify(
                {
                    "ok": False,
                    "internal": True,
                    "error_code": "RATE_LIMIT",
                    "message": "Demasiadas consultas CheckID. Espera un momento e intenta de nuevo.",
                    "http_status": 429,
                    "data": None,
                }
            ),
            429,
        )

    init_db()
    ensure_forced_admin_from_env()

    @app.context_processor
    def inject_globals():
        user = current_user()
        role = normalized_role(user)
        return {
            "app_name": APP_NAME,
            "current_user": user,
            "user_role": role,
            "nav_show_administration": nav_show_administration(role),
            "nav_show_nomina_module": nav_show_nomina_module(role),
            "nav_show_nomina_dashboard": nav_show_nomina_dashboard(role),
            "nav_show_nomina_hub": nav_show_nomina_hub(role),
            "nav_show_finiquitos": nav_show_finiquitos(role),
            "nav_show_facturacion": nav_show_facturacion(role),
            "nav_show_imss_movimientos": nav_show_imss_movimientos(role),
            "nav_show_carrier_vitroflex": nav_show_carrier_vitroflex(role),
            "nav_show_checkid": nav_show_checkid(role),
            "nav_show_comparativo": nav_show_comparativo(role),
            "nav_show_headcount_module": nav_show_headcount_module(role),
            "nav_show_headcount_auditoria": nav_show_headcount_auditoria(role),
            "nav_show_headcount_cliente": nav_show_headcount_cliente(role),
            "nav_show_headcount_conteo": nav_show_headcount_conteo(role),
            "login_home_endpoint": login_home_endpoint,
        }

    def _post_login_redirect(user_row):
        role = normalized_role(user_row)
        return redirect(url_for(login_home_endpoint(role)))

    @app.before_request
    def load_logged_in_user():
        user_id = session.get("user_id")
        g.user = get_user_by_id(user_id) if user_id else None

    @app.before_request
    def _cobranza_allowed_paths_guard():
        """Rol cobranza: solo Facturación, exportación IMSS y flujo de movimientos en app principal."""
        u = g.get("user")
        if not u:
            return
        if normalized_role(u) != "cobranza":
            return
        path = request.path or ""
        if path.startswith("/static/") or path == "/favicon.ico":
            return
        if path in ("/", "/inicio", "/login", "/logout"):
            return
        if path.startswith("/facturacion"):
            return
        if path.startswith("/exportacion-imss"):
            return
        if path.startswith("/formatos/nuevo"):
            return
        if path.startswith("/historial") or path.startswith("/descargar/"):
            return
        abort(403)

    @app.before_request
    def _imss_movimientos_role_guard():
        """Bloquea Movimientos IMSS (app principal) a roles no autorizados."""
        u = g.get("user")
        if not u:
            return
        path = request.path or ""
        if path.startswith("/static/") or path == "/favicon.ico":
            return
        imss_paths = (
            path.startswith("/formatos/"),
            path.startswith("/historial"),
            path.startswith("/descargar/"),
        )
        if not any(imss_paths):
            return
        if can_access_imss_movimientos(normalized_role(u)):
            return
        abort(403)

    @app.route("/")
    def index():
        if g.user:
            return _post_login_redirect(g.user)
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return _post_login_redirect(g.user)

        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            user = get_user_by_username(username)

            if not user or not check_password_hash(user["password_hash"], password):
                flash("Credenciales inválidas. Revisa usuario y contraseña.", "error")
                return render_template("login.html")

            session.clear()
            session["user_id"] = user["id"]
            flash(f"Bienvenido a {APP_NAME}, {user['username']}.", "success")
            log_app_activity(
                str(DB_PATH),
                user_id=int(user["id"]),
                module="auth",
                action="login",
                status="ok",
                ref=None,
                detail=None,
            )
            return _post_login_redirect(user)

        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        uid = int(g.user["id"])
        log_app_activity(str(DB_PATH), user_id=uid, module="auth", action="logout", status="ok")
        session.clear()
        flash("Sesión cerrada.", "success")
        return redirect(url_for("login"))

    @app.route("/inicio")
    @login_required
    def home_usuario():
        role = normalized_role(g.user)
        if role == "admin":
            return redirect(url_for("dashboard"))
        if role in {"nomina", "coordinador", "cobranza"}:
            return redirect(url_for(login_home_endpoint(role)))
        return render_template("home_usuario.html")

    @app.route("/dashboard")
    @login_required
    @role_required("admin")
    def dashboard():
        dash = build_admin_dashboard(str(DB_PATH))
        recent_history = [_history_row_to_template_dict(r) for r in list_history(limit=8)]
        desde = (request.args.get("desde") or "").strip()
        hasta = (request.args.get("hasta") or "").strip()
        modulo = (request.args.get("modulo") or "").strip()
        usuario = (request.args.get("usuario") or "").strip()
        since = f"{desde} 00:00:00" if desde else None
        until = f"{hasta} 23:59:59" if hasta else None
        activity_feed = list_activity_feed(
            str(DB_PATH),
            limit=150,
            since=since,
            until=until,
            module=modulo or None,
            username=usuario or None,
        )
        return render_template(
            "dashboard.html",
            dash=dash,
            recent_history=recent_history,
            activity_feed=activity_feed,
            filtros={"desde": desde, "hasta": hasta, "modulo": modulo, "usuario": usuario},
        )

    @app.route("/admin/diagnostico/persistencia", methods=["GET"])
    @login_required
    @role_required("admin")
    def diagnostico_persistencia():
        if os.environ.get("PROCLEAN_ENABLE_DIAGNOSTICS") != "1":
            abort(404)

        db_exists = DB_PATH.exists()
        admin_credentials_exists = ADMIN_CREDENTIALS_PATH.exists()
        users_total = 0
        admins_total = 0
        admin_usernames: list[str] = []

        if db_exists:
            conn = sqlite3.connect(DB_PATH)
            try:
                users_total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                admins_total = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
                admin_usernames = [row[0] for row in conn.execute("SELECT username FROM users WHERE role = 'admin' ORDER BY username").fetchall()]
            finally:
                conn.close()

        return {
            "INSTANCE_DIR": str(INSTANCE_DIR),
            "DB_PATH": str(DB_PATH),
            "ADMIN_CREDENTIALS_PATH": str(ADMIN_CREDENTIALS_PATH),
            "GENERATED_DIR": str(GENERATED_DIR),
            "DOCX_TEMPLATES_DIR": str(DOCX_TEMPLATES_DIR),
            "db_exists": db_exists,
            "admin_credentials_exists": admin_credentials_exists,
            "users_total": users_total,
            "admins_total": admins_total,
            "admin_usernames": admin_usernames,
        }

    @app.route("/formatos/nuevo", methods=["GET", "POST"])
    @login_required
    def nuevo_formato():
        from modules.carrier.db import (
            attach_alta_format_history,
            get_expediente,
            sync_expediente_nombre_desde_alta,
        )
        from modules.carrier.integration import (
            clear_return_expediente_id,
            pop_return_expediente_id,
            set_return_expediente_id,
        )

        if request.method == "GET":
            raw_eid = request.args.get("carrier_curso_expediente_id")
            if raw_eid is not None and str(raw_eid).strip() != "":
                try:
                    eid = int(str(raw_eid).strip())
                except ValueError:
                    eid = None
                if eid is not None:
                    row = get_expediente(str(DB_PATH), eid)
                    if row is not None and int(row.user_id) == int(g.user["id"]):
                        set_return_expediente_id(session, eid)
                        return redirect(url_for("nuevo_formato"))

        if request.method == "POST":
            try:
                movimientos, fecha_lote, hora_lote = movimientos_from_form(request.form)
                output_format = (request.form.get("output_format") or "pdf").strip().lower()
                if output_format not in {"pdf", "png"}:
                    raise ValueError("Formato de salida no válido. Usa PDF o PNG.")

                first_page_only = request.form.get("first_page_only") in {"1", "on", "true", "yes"}

                result = generate_constancia(
                    template_path=Path(app.config["DOCX_TEMPLATES_DIR"]),
                    output_dir=Path(app.config["GENERATED_DIR"]),
                    movimientos=movimientos,
                    keep_docx=False,
                    fecha_lote=fecha_lote,
                    hora_lote=hora_lote,
                    output_format=output_format,
                    first_page_only=first_page_only,
                )
                record_id = insert_history(
                    user_id=g.user["id"],
                    filename=Path(result["pdf"]).name,
                    pdf_path=result["pdf"],
                    folio=result["folio"],
                    lote=result["lote"],
                    movement_count=result["movimientos"],
                    payload_json=result["movimientos_data"],
                )
                log_app_activity(
                    str(DB_PATH),
                    user_id=int(g.user["id"]),
                    module="movimientos_imss",
                    action="generar_constancia",
                    status="ok",
                    ref=str(record_id),
                )
                popped = pop_return_expediente_id(session)
                if popped is not None:
                    ok = attach_alta_format_history(
                        str(DB_PATH), int(popped), int(g.user["id"]), int(record_id), now_iso()
                    )
                    if ok:
                        sync_expediente_nombre_desde_alta(
                            str(DB_PATH), int(popped), int(record_id), now_iso()
                        )
                        flash(
                            "Constancia generada con éxito y vinculada al expediente de Cursos.",
                            "success",
                        )
                        return redirect(url_for("carrier_curso.index", e=popped))
                    set_return_expediente_id(session, int(popped))
                    flash(
                        "No se pudo vincular al expediente de Cursos; el archivo quedó en el historial de movimientos.",
                        "error",
                    )
                else:
                    flash("Constancia generada con éxito. El archivo ya quedó en el historial de movimientos.", "success")
                return redirect(url_for("descargar", record_id=record_id))
            except Exception as exc:
                flash(str(exc), "error")

        dt = now_in_app_tz()
        return render_template(
            "new_format.html",
            current_time=dt.strftime("%H:%M"),
            current_date=dt.strftime("%Y-%m-%d"),
        )

    @app.route("/checkid")
    @login_required
    def checkid_consulta():
        if not can_access_checkid(normalized_role(g.user)):
            abort(403)
        ensure_row_id = None
        raw_focus = request.args.get("focus")
        if raw_focus is not None and str(raw_focus).strip() != "":
            try:
                ensure_row_id = int(str(raw_focus).strip())
            except ValueError:
                ensure_row_id = None
        checkid_history = list_checkid_queries_global(
            str(DB_PATH), limit=200, ensure_row_id=ensure_row_id
        )
        return render_template(
            "checkid.html",
            checkid_history=checkid_history,
            checkid_admin=g.user["role"] == "admin",
        )

    @app.route("/historial")
    @login_required
    def historial():
        records = [_history_row_to_template_dict(r) for r in list_history()]
        return render_template("history.html", records=records)

    @app.route("/descargar/<int:record_id>")
    @login_required
    def descargar(record_id: int):
        record = get_history_record(record_id)
        if not record:
            abort(404)
        pdf_path = Path(record["pdf_path"])
        if not pdf_path.exists():
            abort(404)
        return send_file(pdf_path, as_attachment=True, download_name=record["filename"])

    @app.route("/historial/descargar/<int:record_id>", methods=["POST"])
    @login_required
    def descargar_desde_historial(record_id: int):
        record = get_history_record(record_id)
        if not record:
            abort(404)

        output_format = (request.form.get("output_format") or "pdf").strip().lower()
        if output_format not in {"pdf", "png"}:
            flash("Formato de salida no válido. Usa PDF o PNG.", "error")
            return redirect(url_for("historial"))
        first_page_only = request.form.get("first_page_only") in {"1", "on", "true", "yes"}

        movimientos_payload = json.loads(record["payload_json"])
        movimientos = parse_movimientos({"movimientos": movimientos_payload})

        try:
            fecha_lote = str(record["created_at"]).split(" ")[0]
            hora_lote = str(record["created_at"]).split(" ")[1][:5]
            result = generate_constancia(
                template_path=Path(app.config["DOCX_TEMPLATES_DIR"]),
                output_dir=Path(app.config["GENERATED_DIR"]),
                movimientos=movimientos,
                keep_docx=False,
                fecha_lote=fecha_lote,
                hora_lote=hora_lote,
                output_format=output_format,
                first_page_only=first_page_only,
            )
            out_path = Path(result["pdf"])
            return send_file(out_path, as_attachment=True, download_name=out_path.name)
        except Exception as exc:
            flash(str(exc), "error")
            return redirect(url_for("historial"))

    @app.route("/historial/eliminar/<int:record_id>", methods=["POST"])
    @login_required
    @role_required("admin")
    def eliminar_historial(record_id: int):
        removed = delete_history_record(record_id)
        if not removed:
            flash("Registro no encontrado.", "error")
        else:
            log_app_activity(
                str(DB_PATH),
                user_id=int(g.user["id"]),
                module="movimientos_imss",
                action="eliminar_historial",
                status="ok",
                ref=str(record_id),
            )
            flash("Registro eliminado del historial.", "success")
        return redirect(url_for("historial"))

    @app.route("/admin/usuarios", methods=["GET", "POST"])
    @login_required
    @role_required("admin")
    def admin_usuarios():
        generated_password = None
        if request.method == "POST":
            action = (request.form.get("action") or "create").strip()

            if action == "update_user":
                try:
                    user_id = int(request.form.get("user_id") or 0)
                except ValueError:
                    user_id = 0
                username = (request.form.get("edit_username") or "").strip()
                role = (request.form.get("edit_role") or "usuario").strip()
                new_password = request.form.get("edit_password") or ""

                if not user_id:
                    flash("Usuario no válido.", "error")
                elif not username:
                    flash("El usuario es obligatorio.", "error")
                elif role not in VALID_USER_ROLES:
                    flash("Rol no válido.", "error")
                elif g.user["id"] == user_id and role != "admin":
                    flash("No puedes quitarte el rol de administrador a ti mismo.", "error")
                else:
                    try:
                        update_user(
                            user_id=user_id,
                            username=username,
                            role=role,
                            new_password=new_password if new_password.strip() else None,
                        )
                        log_app_activity(
                            str(DB_PATH),
                            user_id=int(g.user["id"]),
                            module="admin",
                            action="actualizar_usuario",
                            status="ok",
                            ref=str(user_id),
                            detail=username,
                        )
                        flash(f"Usuario {username} actualizado.", "success")
                        return redirect(url_for("admin_usuarios"))
                    except sqlite3.IntegrityError:
                        flash("Ese nombre de usuario ya existe.", "error")
            else:
                username = (request.form.get("username") or "").strip()
                password = request.form.get("password") or ""
                role = (request.form.get("role") or "usuario").strip()

                if not username:
                    flash("El usuario es obligatorio.", "error")
                elif role not in VALID_USER_ROLES:
                    flash("Rol no válido.", "error")
                else:
                    if not password:
                        password = make_random_password()
                        generated_password = password
                    try:
                        create_user(username=username, password=password, role=role)
                        log_app_activity(
                            str(DB_PATH),
                            user_id=int(g.user["id"]),
                            module="admin",
                            action="crear_usuario",
                            status="ok",
                            ref=username,
                            detail=role,
                        )
                        msg = f"Usuario {username} creado correctamente con rol {role}."
                        if generated_password:
                            msg += f" Contraseña generada: {generated_password}"
                        flash(msg, "success")
                        return redirect(url_for("admin_usuarios"))
                    except sqlite3.IntegrityError:
                        flash("Ese nombre de usuario ya existe.", "error")

        users = list_users()
        return render_template("users.html", users=users)

    # --- Pruebas manuales CheckID (POST /api/checkid/buscar, sesión autenticada) ---
    # Sustituir HOST y COOKIE. No incluir ApiKey en el cuerpo; va por CHECKID_API_KEY.
    #
    # JSON vacío (objeto sin campos de término → validación en cliente):
    #   curl -s -X POST "http://HOST/api/checkid/buscar" -H "Content-Type: application/json" -H "Cookie: session=COOKIE" -d "{}"
    #
    # RFC inválido (formato incorrecto; CheckID suele responder E201 u otro E*):
    #   curl -s -X POST "http://HOST/api/checkid/buscar" -H "Content-Type: application/json" -H "Cookie: session=COOKIE" -d "{\"rfc\":\"XXX\"}"
    #
    # CURP inválido:
    #   curl -s -X POST "http://HOST/api/checkid/buscar" -H "Content-Type: application/json" -H "Cookie: session=COOKIE" -d "{\"curp\":\"CURP00\"}"
    #
    # RFC válido (12 caracteres de ejemplo; reemplazar por uno real de prueba):
    #   curl -s -X POST "http://HOST/api/checkid/buscar" -H "Content-Type: application/json" -H "Cookie: session=COOKIE" -d "{\"rfc\":\"ABCD010101ABC\"}"
    #
    # CURP válido (18 caracteres; reemplazar por uno real de prueba):
    #   curl -s -X POST "http://HOST/api/checkid/buscar" -H "Content-Type: application/json" -H "Cookie: session=COOKIE" -d "{\"curp\":\"CURP000000HDFXXX00\"}"
    #
    # Habilitar logs DEBUG: LOG_LEVEL=DEBUG o logging del root en DEBUG para ver trazas temporales.
    @app.post("/api/checkid/buscar")
    @login_required
    @limiter.limit(CHECKID_BUSCAR_RATE_LIMIT)
    def api_checkid_buscar():
        if not can_access_checkid(normalized_role(g.user)):
            abort(403)
        payload = request.get_json(silent=True)
        if payload is None:
            _log_checkid_struct(
                "info",
                "buscar_validation",
                reason="missing_json",
                user_id=g.user["id"] if g.user else None,
            )
            _bad_json = {
                "ok": False,
                "internal": True,
                "error_code": "VALIDATION_ERROR",
                "message": "Se esperaba un cuerpo JSON.",
                "http_status": 400,
                "data": None,
            }
            _safe_persist_checkid_history("", _bad_json)
            return jsonify(_bad_json), 400
        termino = (
            (payload.get("termino") or payload.get("rfc") or payload.get("curp") or payload.get("termino_busqueda") or "")
            .strip()
        )
        termino_norm = normalize_termino_busqueda(termino)
        if not is_valid_checkid_term(termino_norm):
            log_app_activity(
                str(DB_PATH),
                user_id=int(g.user["id"]),
                module="checkid",
                action="consulta",
                status="fail",
                ref="VALIDATION_VALUE",
            )
            return jsonify(
                {
                    "ok": False,
                    "internal": True,
                    "error_code": "VALIDATION_ERROR",
                    "message": "Valor no válido",
                    "http_status": 400,
                    "data": None,
                }
            ), 400
        termino_log = termino_norm[:512] if termino_norm else ""
        cache_key = termino_norm
        logger.debug(
            "checkid_buscar request meta: json_keys=%s termino_field=%s termino_len=%s",
            sorted(payload.keys()) if isinstance(payload, dict) else None,
            _checkid_payload_source(payload),
            len(termino),
        )
        try:
            try:
                client = CheckIDClient()
            except CheckIDConfigurationError as exc:
                _log_checkid_struct(
                    "error",
                    "buscar_config",
                    user_id=g.user["id"],
                    error="CheckIDConfigurationError",
                )
                _cfg_body = {
                    "ok": False,
                    "internal": True,
                    "error_code": "CONFIG_ERROR",
                    "message": str(exc) or "Configure CHECKID_API_KEY.",
                    "http_status": 503,
                    "data": None,
                }
                _safe_persist_checkid_history(termino_log, _cfg_body)
                return jsonify(_cfg_body), 503

            hist_row_id = find_checkid_history_match_by_rfc_curp(str(DB_PATH), termino)
            if hist_row_id is not None:
                log_app_activity(
                    str(DB_PATH),
                    user_id=int(g.user["id"]),
                    module="checkid",
                    action="consulta",
                    status="ok",
                    ref=f"history:{hist_row_id}",
                    detail="cache_historial",
                )
                return jsonify(
                    {
                        "ok": True,
                        "internal": False,
                        "history_focus_row_id": hist_row_id,
                        "data": None,
                        "message": "Valor ya en historial",
                        "error_code": None,
                        "http_status": 200,
                    }
                ), 200

            cached = get_cached_busqueda(cache_key) if cache_key else None
            if cached is not None:
                _log_checkid_struct(
                    "info",
                    "buscar_cache_hit",
                    user_id=g.user["id"],
                    termino_len=len(termino),
                    termino_field=_checkid_payload_source(payload),
                )
                body, status = checkid_http_response(cached)
                _safe_persist_checkid_history(termino_log, body)
                return jsonify(body), status

            result = client.buscar(termino_norm)
            if result.get("ok") is True:
                set_cached_busqueda(cache_key, result)

            _log_checkid_struct(
                "info",
                "buscar_result",
                user_id=g.user["id"],
                termino_len=len(termino),
                termino_field=_checkid_payload_source(payload),
                ok=result.get("ok"),
                internal=result.get("internal"),
                error_code=result.get("error_code"),
                http_status=result.get("http_status"),
            )
            logger.debug(
                "checkid_buscar response (sanitized): %s",
                json.dumps(_strip_secrets_for_log(result), ensure_ascii=False, default=str),
            )
            body, status = checkid_http_response(result)
            _safe_persist_checkid_history(termino_log, body)
            return jsonify(body), status
        except Exception as exc:
            logger.error(
                "%s",
                json.dumps(
                    {
                        "event": "checkid_buscar_exception",
                        "user_id": g.user["id"] if g.user else None,
                        "error_type": type(exc).__name__,
                    },
                    default=str,
                ),
                exc_info=True,
            )
            _err_body = {
                "ok": False,
                "internal": True,
                "error_code": "INTERNAL_ERROR",
                "message": "Error interno al consultar CheckID.",
                "http_status": 500,
                "data": None,
            }
            _safe_persist_checkid_history(termino_log, _err_body)
            return jsonify(_err_body), 500

    @app.get("/api/checkid/historial/<int:entry_id>/detalle")
    @login_required
    def api_checkid_historial_detalle(entry_id: int):
        if not can_access_checkid(normalized_role(g.user)):
            abort(403)
        bundle = get_checkid_detail_bundle_for_row(str(DB_PATH), entry_id)
        if bundle is None:
            return jsonify({"ok": False, "message": "Registro no encontrado."}), 404
        return jsonify({"ok": True, "detail": bundle}), 200

    @app.post("/api/checkid/historial/<int:entry_id>/actualizar")
    @login_required
    @limiter.limit(CHECKID_BUSCAR_RATE_LIMIT)
    def api_checkid_historial_actualizar(entry_id: int):
        if not can_access_checkid(normalized_role(g.user)):
            abort(403)
        enriched_preview = get_checkid_success_row_by_id(str(DB_PATH), entry_id)
        if not enriched_preview:
            return jsonify({"ok": False, "message": "Registro no encontrado."}), 404
        curp_raw = (enriched_preview.get("curp") or "").strip()
        curp_norm = normalize_termino_busqueda(curp_raw)
        if not curp_norm or len(curp_norm) != 18 or not is_valid_checkid_term(curp_norm):
            return jsonify(
                {
                    "ok": False,
                    "message": "Este registro no tiene un CURP válido para actualizar.",
                }
            ), 400
        try:
            client = CheckIDClient()
        except CheckIDConfigurationError as exc:
            return jsonify(
                {
                    "ok": False,
                    "internal": True,
                    "error_code": "CONFIG_ERROR",
                    "message": str(exc) or "Configure CHECKID_API_KEY.",
                    "http_status": 503,
                    "data": None,
                }
            ), 503
        result = client.buscar(curp_norm)
        if result.get("ok") is True:
            set_cached_busqueda(curp_norm, result)
        body, status = checkid_http_response(result)
        if not body.get("ok"):
            return jsonify(body), status if status else 200
        if not update_checkid_success_row_from_response(
            str(DB_PATH), int(entry_id), curp_norm, body
        ):
            return jsonify(
                {"ok": False, "message": "No se pudo guardar la actualización en el historial."}
            ), 500
        updated = get_checkid_success_row_by_id(str(DB_PATH), int(entry_id))
        log_app_activity(
            str(DB_PATH),
            user_id=int(g.user["id"]),
            module="checkid",
            action="actualizar_registro",
            status="ok",
            ref=str(entry_id),
        )
        return jsonify(
            {
                "ok": True,
                "message": "Registro actualizado correctamente.",
                "consulta": body,
                "row": updated,
            }
        ), 200

    @app.delete("/api/checkid/historial/<int:entry_id>")
    @login_required
    @role_required("admin")
    def api_checkid_historial_delete(entry_id: int):
        if delete_checkid_query_by_id(str(DB_PATH), entry_id):
            log_app_activity(
                str(DB_PATH),
                user_id=int(g.user["id"]),
                module="checkid",
                action="eliminar_historial",
                status="ok",
                ref=str(entry_id),
            )
            return jsonify({"ok": True}), 200
        return jsonify({"ok": False, "message": "Registro no encontrado."}), 404

    @app.get("/api/checkid/solicitudes-restantes")
    @login_required
    @role_required("admin")
    def api_checkid_solicitudes_restantes():
        try:
            client = CheckIDClient()
        except CheckIDConfigurationError as exc:
            return (
                jsonify(
                    {
                        "ok": False,
                        "internal": True,
                        "error_code": "CONFIG_ERROR",
                        "message": str(exc) or "Configure CHECKID_API_KEY.",
                        "http_status": 503,
                        "data": None,
                    }
                ),
                503,
            )
        result = client.solicitudes_restantes()
        body, status = checkid_http_response(result)
        return jsonify(body), status

    from modules.vitroflex_docs.blueprint import register_vitroflex
    from modules.finiquitos.blueprint import register_finiquitos
    from modules.carrier.blueprint import register_carrier
    from modules.examenes_medicos.blueprint import register_examenes_medicos
    from modules.comparativo.routes import comparativo_bp
    from modules.exportacion_imss.routes import exportacion_imss_bp
    from modules.nomina.blueprint import register_nomina
    from modules.facturacion.blueprint import register_facturacion

    register_vitroflex(app)
    register_finiquitos(app)
    register_carrier(app)
    register_examenes_medicos(app)
    register_nomina(app)
    register_facturacion(app)
    from modules.headcount.blueprint import register_headcount

    register_headcount(app)
    app.register_blueprint(comparativo_bp)
    app.register_blueprint(exportacion_imss_bp)

    return app


def ensure_forced_admin_from_env() -> None:
    username = (os.environ.get("PROCLEAN_FORCE_ADMIN_USERNAME") or "").strip()
    password = os.environ.get("PROCLEAN_FORCE_ADMIN_PASSWORD") or ""
    if not username or not password:
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        password_hash = generate_password_hash(password)
        if existing:
            conn.execute(
                "UPDATE users SET password_hash = ?, role = 'admin' WHERE id = ?",
                (password_hash, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, 'admin', ?)",
                (username, password_hash, now_iso()),
            )
        conn.commit()
        print(f"forced admin ensured: {username} | db_path: {DB_PATH}")
    finally:
        conn.close()


def ensure_default_templates() -> None:
    from modules.finiquitos.finiquito_template_patch import patch_finiquito_docx_template_bytes

    DOCX_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    for filename in TEMPLATE_FILENAMES.values():
        src = BUNDLED_TEMPLATES_DIR / filename
        dst = DOCX_TEMPLATES_DIR / filename
        if src.exists() and not dst.exists():
            dst.write_bytes(src.read_bytes())
    fin_src = BUNDLED_TEMPLATES_DIR / "FINIQUITO FORMATO.docx"
    fin_dst = DOCX_TEMPLATES_DIR / "FINIQUITO FORMATO.docx"
    if fin_src.exists():
        fin_dst.parent.mkdir(parents=True, exist_ok=True)
        # Misma plantilla parcheada que usa el render en memoria: evita divergencia bundle vs volumen.
        fin_dst.write_bytes(patch_finiquito_docx_template_bytes(fin_src.read_bytes()))


def get_or_create_secret_key() -> str:
    if SECRET_KEY_PATH.exists():
        return SECRET_KEY_PATH.read_text(encoding="utf-8").strip()
    secret = secrets.token_hex(32)
    SECRET_KEY_PATH.write_text(secret, encoding="utf-8")
    return secret


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


def _migrate_checkid_query_log_schema(conn: sqlite3.Connection) -> None:
    """Añade columnas nuevas a checkid_query_log en bases ya existentes."""
    cur = conn.execute("PRAGMA table_info(checkid_query_log)")
    cols = {row[1] for row in cur.fetchall()}
    for name, typ in (
        ("apellido_paterno", "TEXT"),
        ("apellido_materno", "TEXT"),
        ("nombres", "TEXT"),
        ("fecha_nacimiento", "TEXT"),
        ("detail_json", "TEXT"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE checkid_query_log ADD COLUMN {name} {typ}")


def _migrate_users_role_constraint(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'"
    ).fetchone()
    table_sql = str(row[0] if row else "")
    if "cobranza" in table_sql:
        return

    conn.execute("DROP TABLE IF EXISTS users_new")
    conn.execute(
        """
        CREATE TABLE users_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','usuario','coordinador','nomina','cobranza')),
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO users_new (id, username, password_hash, role, created_at)
        SELECT id, username, password_hash, role, created_at
        FROM users
        """
    )
    conn.execute("DROP TABLE users")
    conn.execute("ALTER TABLE users_new RENAME TO users")


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','usuario','coordinador','nomina','cobranza')),
                created_at TEXT NOT NULL
            )
            """
        )
        _migrate_users_role_constraint(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS format_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                pdf_path TEXT NOT NULL,
                folio TEXT NOT NULL,
                lote TEXT NOT NULL,
                movement_count INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkid_query_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                termino_busqueda TEXT NOT NULL,
                ok INTEGER NOT NULL CHECK (ok IN (0, 1)),
                error_code TEXT,
                error_message TEXT,
                rfc TEXT,
                curp TEXT,
                nombre TEXT,
                nss TEXT,
                regimen_fiscal TEXT,
                codigo_postal TEXT,
                estado_69 TEXT,
                apellido_paterno TEXT,
                apellido_materno TEXT,
                nombres TEXT,
                fecha_nacimiento TEXT,
                detail_json TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        _migrate_checkid_query_log_schema(conn)
        from services.app_activity import ensure_app_activity_schema

        ensure_app_activity_schema(conn)
        from modules.examenes_medicos.db import ensure_examenes_medicos_tables

        ensure_examenes_medicos_tables(conn)
        from modules.nomina.db import ensure_nomina_tables

        ensure_nomina_tables(conn)
        from modules.facturacion.db import ensure_facturacion_tables

        ensure_facturacion_tables(conn)
        conn.commit()

        count = conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()[0]
        if count == 0:
            username = f"admin_{secrets.token_hex(2)}"
            password = make_random_password()
            conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, 'admin', ?)",
                (username, generate_password_hash(password), now_iso()),
            )
            conn.commit()
            ADMIN_CREDENTIALS_PATH.write_text(
                f"ProClean App\nUsuario admin: {username}\nContraseña admin: {password}\n",
                encoding="utf-8",
            )
    finally:
        conn.close()


def current_user():
    return getattr(g, "user", None)


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(**kwargs)

    return wrapped_view


def role_required(role: str):
    def decorator(view):
        @wraps(view)
        def wrapped_view(**kwargs):
            if g.user is None:
                return redirect(url_for("login"))
            if g.user["role"] != role:
                abort(403)
            return view(**kwargs)

        return wrapped_view

    return decorator


def get_user_by_id(user_id: int | None):
    if not user_id:
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()


def get_user_by_username(username: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    finally:
        conn.close()


def create_user(username: str, password: str, role: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (username, generate_password_hash(password), role, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def list_users():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT id, username, role, created_at FROM users ORDER BY created_at DESC").fetchall()
    finally:
        conn.close()


def insert_history(user_id: int, filename: str, pdf_path: str, folio: str, lote: str, movement_count: int, payload_json: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.execute(
            """
            INSERT INTO format_history (user_id, filename, pdf_path, folio, lote, movement_count, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, filename, pdf_path, folio, lote, movement_count, payload_json, now_iso()),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def _formato_realizado_display(created_at: str | None) -> str:
    """Fecha/hora en que se guardó el formato en la app (columna format_history.created_at)."""
    if not created_at or not str(created_at).strip():
        return "—"
    s = str(created_at).strip()
    try:
        dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            dt = datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            return s
    return dt.strftime("%d/%m/%Y %H:%M")


def _history_movement_display_fields(payload_json: str | None) -> dict[str, str]:
    """Textos para historial: fechas y sueldos desde payload_json (lista de movimientos)."""
    movement_dates_line = "—"
    salarios_line = "—"
    if not payload_json:
        return {"movement_dates_line": movement_dates_line, "salarios_line": salarios_line}
    try:
        movs = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {"movement_dates_line": movement_dates_line, "salarios_line": salarios_line}
    if not isinstance(movs, list):
        return {"movement_dates_line": movement_dates_line, "salarios_line": salarios_line}
    fechas: list[str] = []
    sueldos: list[str] = []
    for m in movs:
        if not isinstance(m, dict):
            continue
        f = m.get("fecha")
        if f is not None and str(f).strip():
            fechas.append(str(f).strip())
        s = m.get("salario")
        if s is not None and str(s).strip():
            sueldos.append(str(s).strip())
    if fechas:
        movement_dates_line = "; ".join(fechas)
    if sueldos:
        salarios_line = "; ".join(sueldos)
    return {"movement_dates_line": movement_dates_line, "salarios_line": salarios_line}


def _history_search_blob_from_row(row: sqlite3.Row) -> str:
    """Texto en minúsculas para filtrado en cliente (columnas visibles + movimientos en JSON)."""
    created_raw = str(row["created_at"] or "")
    parts: list[str] = [
        created_raw,
        _formato_realizado_display(row["created_at"]),
        str(row["filename"] or ""),
        str(row["username"] or ""),
        str(row["movement_count"] or ""),
        str(row["folio"] or ""),
        str(row["lote"] or ""),
    ]
    raw = row["payload_json"]
    if raw:
        try:
            movs = json.loads(raw)
            if isinstance(movs, list):
                for m in movs:
                    if isinstance(m, dict):
                        parts.extend(
                            [
                                str(m.get("tipo") or ""),
                                str(m.get("nss") or ""),
                                str(m.get("nombre") or ""),
                                str(m.get("fecha") or ""),
                                str(m.get("salario") or ""),
                            ]
                        )
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return " ".join(parts).casefold()


def _history_row_to_template_dict(row: sqlite3.Row) -> dict:
    d = {k: row[k] for k in row.keys()}
    d["search_blob"] = _history_search_blob_from_row(row)
    d.update(_history_movement_display_fields(d.get("payload_json")))
    d["formato_realizado_display"] = _formato_realizado_display(d.get("created_at"))
    return d


def list_history(limit: int | None = None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        query = """
            SELECT h.*, u.username
            FROM format_history h
            JOIN users u ON u.id = h.user_id
            ORDER BY h.created_at DESC
        """
        if limit:
            query += f" LIMIT {int(limit)}"
        return conn.execute(query).fetchall()
    finally:
        conn.close()


def get_history_record(record_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT h.*, u.username
            FROM format_history h
            JOIN users u ON u.id = h.user_id
            WHERE h.id = ?
            """,
            (record_id,),
        ).fetchone()
    finally:
        conn.close()


def delete_history_record(record_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT pdf_path FROM format_history WHERE id = ?",
            (record_id,),
        ).fetchone()
        if not row:
            return False
        file_path = Path(row["pdf_path"])
        conn.execute("DELETE FROM format_history WHERE id = ?", (record_id,))
        conn.commit()
        if file_path.exists():
            try:
                file_path.unlink()
            except OSError:
                pass
        return True
    finally:
        conn.close()


def update_user(user_id: int, username: str, role: str, new_password: str | None) -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        if new_password:
            conn.execute(
                """
                UPDATE users
                SET username = ?, role = ?, password_hash = ?
                WHERE id = ?
                """,
                (username, role, generate_password_hash(new_password), user_id),
            )
        else:
            conn.execute(
                """
                UPDATE users
                SET username = ?, role = ?
                WHERE id = ?
                """,
                (username, role, user_id),
            )
        conn.commit()
    finally:
        conn.close()


def make_random_password() -> str:
    return secrets.token_urlsafe(9)


def now_iso() -> str:
    return now_in_app_tz().strftime("%Y-%m-%d %H:%M:%S")


app = create_app()


import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
