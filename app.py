from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
from datetime import datetime
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

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

from generator import TEMPLATE_FILENAMES, generate_constancia, movimientos_from_form, parse_movimientos, replace_default_template
from services.checkid_cache import get_cached_busqueda, set_cached_busqueda
from services.checkid_client import CheckIDClient, CheckIDConfigurationError, normalize_termino_busqueda
from services.checkid_history import (
    delete_checkid_query_by_id,
    list_checkid_queries_global,
    persist_checkid_query,
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


def _checkid_payload_source(payload: dict) -> str:
    """Indica qué campo aportó el término (sin registrar el valor)."""
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
        MAX_CONTENT_LENGTH=4 * 1024 * 1024,
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
        return {"app_name": APP_NAME, "current_user": current_user()}

    @app.before_request
    def load_logged_in_user():
        user_id = session.get("user_id")
        g.user = get_user_by_id(user_id) if user_id else None

    @app.route("/")
    def index():
        if g.user:
            if g.user["role"] == "admin":
                return redirect(url_for("dashboard"))
            return redirect(url_for("nuevo_formato"))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            if g.user["role"] == "admin":
                return redirect(url_for("dashboard"))
            return redirect(url_for("nuevo_formato"))

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
            if user["role"] == "admin":
                return redirect(url_for("dashboard"))
            return redirect(url_for("nuevo_formato"))

        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        session.clear()
        flash("Sesión cerrada.", "success")
        return redirect(url_for("login"))

    @app.route("/dashboard")
    @login_required
    @role_required("admin")
    def dashboard():
        stats = get_dashboard_stats()
        recent_history = list_history(limit=5)
        return render_template("dashboard.html", stats=stats, recent_history=recent_history)

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
                flash("Constancia generada con éxito. El archivo ya quedó en el historial de movimientos.", "success")
                return redirect(url_for("descargar", record_id=record_id))
            except Exception as exc:
                flash(str(exc), "error")

        return render_template("new_format.html", current_time=datetime.now().strftime("%H:%M"), current_date=datetime.now().strftime("%Y-%m-%d"))

    @app.route("/checkid")
    @login_required
    def checkid_consulta():
        checkid_history = list_checkid_queries_global(str(DB_PATH), limit=200)
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
                elif role not in {"admin", "usuario"}:
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
                elif role not in {"admin", "usuario"}:
                    flash("Rol no válido.", "error")
                else:
                    if not password:
                        password = make_random_password()
                        generated_password = password
                    try:
                        create_user(username=username, password=password, role=role)
                        msg = f"Usuario {username} creado correctamente con rol {role}."
                        if generated_password:
                            msg += f" Contraseña generada: {generated_password}"
                        flash(msg, "success")
                        return redirect(url_for("admin_usuarios"))
                    except sqlite3.IntegrityError:
                        flash("Ese nombre de usuario ya existe.", "error")

        users = list_users()
        return render_template("users.html", users=users)

    @app.route("/admin/plantilla", methods=["GET", "POST"])
    @login_required
    @role_required("admin")
    def admin_plantilla():
        if request.method == "POST":
            updated = []
            for count, filename in TEMPLATE_FILENAMES.items():
                incoming = request.files.get(f"template_docx_{count}")
                if incoming and incoming.filename and incoming.filename.lower().endswith(".docx"):
                    temp_path = INSTANCE_DIR / f"uploaded_template_{count}.docx"
                    incoming.save(temp_path)
                    replace_default_template(temp_path, DOCX_TEMPLATES_DIR / filename)
                    temp_path.unlink(missing_ok=True)
                    updated.append(filename)
            if updated:
                flash("Plantillas actualizadas: " + ", ".join(updated), "success")
                return redirect(url_for("admin_plantilla"))
            flash("Debes subir al menos una plantilla .docx válida.", "error")

        template_info = []
        for count, filename in TEMPLATE_FILENAMES.items():
            path = DOCX_TEMPLATES_DIR / filename
            template_info.append(
                {
                    "count": count,
                    "filename": filename,
                    "path": str(path),
                    "modified": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M") if path.exists() else "No disponible",
                }
            )
        return render_template("template_admin.html", template_info=template_info)

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
            (payload.get("rfc") or payload.get("curp") or payload.get("termino_busqueda") or "")
            .strip()
        )
        termino_log = normalize_termino_busqueda(termino) or (termino.strip()[:512] if termino else "")
        cache_key = normalize_termino_busqueda(termino)
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

            result = client.buscar(termino)
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

    @app.delete("/api/checkid/historial/<int:entry_id>")
    @login_required
    @role_required("admin")
    def api_checkid_historial_delete(entry_id: int):
        if delete_checkid_query_by_id(str(DB_PATH), entry_id):
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
    DOCX_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    for filename in TEMPLATE_FILENAMES.values():
        src = BUNDLED_TEMPLATES_DIR / filename
        dst = DOCX_TEMPLATES_DIR / filename
        if src.exists() and not dst.exists():
            dst.write_bytes(src.read_bytes())


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


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','usuario')),
                created_at TEXT NOT NULL
            )
            """
        )
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
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
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


def _history_search_blob_from_row(row: sqlite3.Row) -> str:
    """Texto en minúsculas para filtrado en cliente (columnas visibles + movimientos en JSON)."""
    parts: list[str] = [
        str(row["created_at"] or ""),
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
                            ]
                        )
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return " ".join(parts).casefold()


def _history_row_to_template_dict(row: sqlite3.Row) -> dict:
    d = {k: row[k] for k in row.keys()}
    d["search_blob"] = _history_search_blob_from_row(row)
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


def get_dashboard_stats():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(movement_count), 0) AS movimientos FROM format_history"
        ).fetchone()
        users_total = conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()[0]
        return {
            "formatos": row["total"],
            "movimientos": row["movimientos"],
            "usuarios": users_total,
            "plantilla": "Set 1-4 movimientos",
        }
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
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


app = create_app()


import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
