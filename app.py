from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from generator import TEMPLATE_FILENAMES, generate_constancia, movimientos_from_form, replace_default_template

BASE_DIR = Path(__file__).resolve().parent
BUNDLED_TEMPLATES_DIR = BASE_DIR / "docx_templates"
INSTANCE_DIR = Path(os.environ.get("PROCLEAN_INSTANCE_DIR", str(BASE_DIR / "instance")))
GENERATED_DIR = Path(os.environ.get("PROCLEAN_GENERATED_DIR", str(BASE_DIR / "generated")))
DOCX_TEMPLATES_DIR = Path(os.environ.get("PROCLEAN_TEMPLATES_DIR", str(BUNDLED_TEMPLATES_DIR)))
ADMIN_CREDENTIALS_PATH = INSTANCE_DIR / "admin_credentials.txt"
SECRET_KEY_PATH = INSTANCE_DIR / "secret_key.txt"
DB_PATH = INSTANCE_DIR / "proclean.db"

APP_NAME = "ProClean App"


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

    init_db()

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
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("dashboard"))

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
            return redirect(url_for("dashboard"))

        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        session.clear()
        flash("Sesión cerrada.", "success")
        return redirect(url_for("login"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        stats = get_dashboard_stats(g.user)
        recent_history = list_history(g.user, limit=5)
        return render_template("dashboard.html", stats=stats, recent_history=recent_history)

    @app.route("/formatos/nuevo", methods=["GET", "POST"])
    @login_required
    def nuevo_formato():
        if request.method == "POST":
            try:
                movimientos, fecha_lote, hora_lote = movimientos_from_form(request.form)
                result = generate_constancia(
                    template_path=Path(app.config["DOCX_TEMPLATES_DIR"]),
                    output_dir=Path(app.config["GENERATED_DIR"]),
                    movimientos=movimientos,
                    keep_docx=False,
                    fecha_lote=fecha_lote,
                    hora_lote=hora_lote,
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
                flash("Formato generado con éxito. El PDF ya quedó en historial.", "success")
                return redirect(url_for("descargar", record_id=record_id))
            except Exception as exc:
                flash(str(exc), "error")

        return render_template("new_format.html", current_time=datetime.now().strftime("%H:%M"), current_date=datetime.now().strftime("%Y-%m-%d"))

    @app.route("/historial")
    @login_required
    def historial():
        records = list_history(g.user)
        return render_template("history.html", records=records)

    @app.route("/descargar/<int:record_id>")
    @login_required
    def descargar(record_id: int):
        record = get_history_record(record_id)
        if not record:
            abort(404)
        if g.user["role"] != "admin" and record["user_id"] != g.user["id"]:
            abort(403)
        pdf_path = Path(record["pdf_path"])
        if not pdf_path.exists():
            abort(404)
        return send_file(pdf_path, as_attachment=True, download_name=record["filename"])

    @app.route("/admin/usuarios", methods=["GET", "POST"])
    @login_required
    @role_required("admin")
    def admin_usuarios():
        generated_password = None
        if request.method == "POST":
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

    return app


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


def list_history(user, limit: int | None = None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        params = []
        query = """
            SELECT h.*, u.username
            FROM format_history h
            JOIN users u ON u.id = h.user_id
        """
        if user["role"] != "admin":
            query += " WHERE h.user_id = ?"
            params.append(user["id"])
        query += " ORDER BY h.created_at DESC"
        if limit:
            query += f" LIMIT {int(limit)}"
        return conn.execute(query, params).fetchall()
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


def get_dashboard_stats(user):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        base_query = "SELECT COUNT(*) AS total, COALESCE(SUM(movement_count), 0) AS movimientos FROM format_history"
        user_query = base_query + " WHERE user_id = ?"
        row = conn.execute(base_query if user["role"] == "admin" else user_query, () if user["role"] == "admin" else (user["id"],)).fetchone()

        users_total = conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()[0] if user["role"] == "admin" else None
        return {
            "formatos": row["total"],
            "movimientos": row["movimientos"],
            "usuarios": users_total,
            "plantilla": "Set 1-4 movimientos",
        }
    finally:
        conn.close()


def make_random_password() -> str:
    return secrets.token_urlsafe(9)


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


app = create_app()


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    app.run(host=host, port=port, debug=False)
