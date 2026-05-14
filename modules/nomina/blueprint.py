from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from functools import wraps
from io import BytesIO
from pathlib import Path

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
from zoneinfo import ZoneInfo

from modules.nomina.asistencia_excel import build_asistencia_template_file
from modules.nomina.db import (
    get_asistencia_import,
    get_latest_import_base_rows,
    get_latest_import_base_rows_multi,
    nomina_dashboard_overview,
    nomina_clientes_from_history,
    save_asistencia_import,
)
from modules.nomina.validators import ValidationError, parse_and_validate_asistencia_excel
from modules.comparativo import alias_service
from modules.comparativo.headcount_service import obtener_activos

_BASE = Path(__file__).resolve().parent.parent.parent
_TEMPLATE_DIR = _BASE / "templates" / "nomina"

nomina_bp = Blueprint(
    "nomina",
    __name__,
    url_prefix="/nomina",
    template_folder=str(_TEMPLATE_DIR),
)

_NOMINA_ALLOWED_ROLES = {"admin", "nomina", "coordinador"}
_NOMINA_DASHBOARD_ROLES = {"admin", "nomina"}


def _login_required_page(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def _nomina_access_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        role = str(g.user.get("role") if isinstance(g.user, dict) else g.user["role"]).strip().lower()
        if role not in _NOMINA_ALLOWED_ROLES:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _now_iso() -> str:
    return datetime.now(ZoneInfo("America/Mexico_City")).strftime("%Y-%m-%d %H:%M:%S")


def _parse_fecha_inicio(raw: str) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_semana_range(semana: str) -> tuple[date, date] | None:
    txt = (semana or "").strip()
    m = re.search(r"(\d{2}/\d{2}/\d{4})\s+al\s+(\d{2}/\d{2}/\d{4})", txt)
    if not m:
        return None
    try:
        start = datetime.strptime(m.group(1), "%d/%m/%Y").date()
        end = datetime.strptime(m.group(2), "%d/%m/%Y").date()
        return start, end
    except ValueError:
        return None


def _db_path() -> str:
    return str(current_app.config["DATABASE"])


def _current_role() -> str:
    if g.user is None:
        return ""
    try:
        return str(g.user["role"] or "").strip().lower()
    except Exception:
        return ""


def _coordinador_display_name() -> str:
    if g.user is None:
        return ""
    keys = ("display_name", "nombre", "full_name", "username", "email")
    for key in keys:
        try:
            value = str(g.user[key] or "").strip()
        except Exception:
            value = str(getattr(g.user, key, "") or "").strip()
        if value:
            return value
    return "Coordinador"


def _available_clientes_headcount() -> tuple[list[str], dict[str, list[str]], str | None]:
    try:
        clientes = sorted({str(a.get("cliente") or "").strip() for a in obtener_activos() if str(a.get("cliente") or "").strip()})
        agrupaciones_raw = alias_service.obtener_agrupaciones()
        agrupaciones: dict[str, list[str]] = {}
        for name, members in (agrupaciones_raw or {}).items():
            group_clients = [str(c).strip() for c in (members or []) if str(c).strip() in clientes]
            if group_clients:
                agrupaciones[str(name).strip()] = group_clients
        return clientes, agrupaciones, None
    except Exception as exc:
        fallback = nomina_clientes_from_history(_db_path())
        if fallback:
            return fallback, {}, f"Headcount no disponible ({exc}). Se muestran clientes detectados en historial de Nóminas."
        return [], {}, str(exc)


def _extract_selected_clientes_from_form() -> list[str]:
    selected = [str(c).strip() for c in request.form.getlist("clientes") if str(c).strip()]
    fallback = str(request.form.get("cliente") or "").strip()
    if fallback and fallback not in selected:
        selected.append(fallback)
    unique: list[str] = []
    seen: set[str] = set()
    for item in selected:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _cliente_header_label(clientes: list[str]) -> str:
    if not clientes:
        return ""
    if len(clientes) == 1:
        return clientes[0]
    return "MULTICLIENTE: " + " + ".join(clientes)


@nomina_bp.get("/")
@_nomina_access_required
def index():
    role = _current_role()
    if role in _NOMINA_DASHBOARD_ROLES:
        dash = nomina_dashboard_overview(_db_path(), recent_limit=12)
        return render_template("nomina/dashboard.html", dash=dash)
    clientes, agrupaciones, headcount_error = _available_clientes_headcount()
    return render_template(
        "nomina/index.html",
        coordinador_display=_coordinador_display_name(),
        clientes=clientes,
        agrupaciones=agrupaciones,
        headcount_error=headcount_error,
    )


@nomina_bp.get("/master")
@_nomina_access_required
def master_hub():
    clientes, agrupaciones, headcount_error = _available_clientes_headcount()
    return render_template(
        "nomina/index.html",
        coordinador_display=_coordinador_display_name(),
        clientes=clientes,
        agrupaciones=agrupaciones,
        headcount_error=headcount_error,
    )


@nomina_bp.post("/descargar-plantilla")
@_nomina_access_required
def descargar_plantilla():
    fecha_inicio = _parse_fecha_inicio(request.form.get("fecha_inicio") or "")
    clientes = _extract_selected_clientes_from_form()
    coordinador = _coordinador_display_name()
    if fecha_inicio is None:
        flash("La fecha inicio del periodo es obligatoria.", "error")
        return redirect(url_for("nomina.master_hub"))
    if not clientes:
        flash("Debes seleccionar al menos un cliente.", "error")
        return redirect(url_for("nomina.master_hub"))

    fecha_fin = fecha_inicio + timedelta(days=6)
    if len(clientes) == 1:
        base_rows = get_latest_import_base_rows(_db_path(), clientes[0], fecha_inicio)
    else:
        base_rows = get_latest_import_base_rows_multi(_db_path(), clientes, fecha_inicio)

    cliente_header = _cliente_header_label(clientes)
    payload = build_asistencia_template_file(
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        cliente=cliente_header,
        coordinador=coordinador,
        base_rows=base_rows,
    )
    output = BytesIO(payload)
    output.seek(0)
    filename = f"Master_Asistencia_{fecha_inicio.strftime('%Y%m%d')}_{cliente_header.replace(' ', '_')}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@nomina_bp.post("/importar")
@_nomina_access_required
def importar_asistencia():
    file = request.files.get("excel_file")
    if file is None or not (file.filename or "").strip():
        flash("Debes seleccionar un archivo .xlsx para importar.", "error")
        return redirect(url_for("nomina.master_hub"))

    filename = file.filename or "asistencia.xlsx"
    if not filename.lower().endswith(".xlsx"):
        flash("Solo se permiten archivos .xlsx en este flujo.", "error")
        return redirect(url_for("nomina.master_hub"))

    try:
        file_bytes = file.read()
        parsed = parse_and_validate_asistencia_excel(file_bytes, filename=filename)
    except ValidationError as exc:
        flash(str(exc), "error")
        return redirect(url_for("nomina.master_hub"))
    except Exception as exc:
        flash(f"No se pudo leer el archivo: {exc}", "error")
        return redirect(url_for("nomina.master_hub"))

    if parsed["blocking_errors"]:
        joined = " | ".join(parsed["blocking_errors"][:6])
        if len(parsed["blocking_errors"]) > 6:
            joined += " | ..."
        flash(
            "Importación bloqueada por errores graves (clave diaria inválida o números negativos): "
            + joined,
            "error",
        )
        return redirect(url_for("nomina.master_hub"))

    range_detected = _parse_semana_range(parsed.get("semana") or "")
    if range_detected is None:
        fecha_inicio = date.today()
        fecha_fin = fecha_inicio + timedelta(days=6)
    else:
        fecha_inicio, fecha_fin = range_detected

    payload = {
        "semana": parsed.get("semana") or "",
        "fecha_inicio": fecha_inicio.isoformat(),
        "fecha_fin": fecha_fin.isoformat(),
        "cliente": parsed.get("cliente") or "",
        "coordinador": _coordinador_display_name(),
        "filename": filename,
        "original_filename": filename,
        "file_hash": hashlib.sha256(file_bytes).hexdigest(),
        "status": "draft",
        "total_rows": parsed.get("total_rows") or 0,
        "error_count": parsed.get("error_count") or 0,
        "warning_count": parsed.get("warning_count") or 0,
        "rows": parsed.get("rows") or [],
        "raw_json": {
            "dias_headers": parsed.get("dias_headers") or [],
            "semana": parsed.get("semana") or "",
            "cliente": parsed.get("cliente") or "",
            "coordinador_archivo": parsed.get("coordinador") or "",
            "coordinador_session": _coordinador_display_name(),
        },
    }
    created_by = int(g.user["id"]) if g.user is not None else None
    import_id = save_asistencia_import(_db_path(), payload, created_by=created_by, now_iso=_now_iso())
    flash("Archivo importado y guardado como borrador.", "success")
    return redirect(url_for("nomina.asistencia_importada", import_id=import_id))


@nomina_bp.get("/imports/<int:import_id>")
@_nomina_access_required
def asistencia_importada(import_id: int):
    imp = get_asistencia_import(_db_path(), import_id)
    if imp is None:
        flash("No se encontró la importación solicitada.", "error")
        return redirect(url_for("nomina.index"))
    return render_template("nomina/asistencia_importada.html", imp=imp)


def register_nomina(app) -> None:
    app.register_blueprint(nomina_bp)

