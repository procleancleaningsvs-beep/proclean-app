from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from functools import wraps
from io import BytesIO
from pathlib import Path

from flask import (
    Blueprint,
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
    save_asistencia_import,
)
from modules.nomina.validators import ValidationError, parse_and_validate_asistencia_excel

_BASE = Path(__file__).resolve().parent.parent.parent
_TEMPLATE_DIR = _BASE / "templates" / "nomina"

nomina_bp = Blueprint(
    "nomina",
    __name__,
    url_prefix="/nomina",
    template_folder=str(_TEMPLATE_DIR),
)


def _login_required_page(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
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


@nomina_bp.get("/")
@_login_required_page
def index():
    return render_template("nomina/index.html")


@nomina_bp.post("/descargar-plantilla")
@_login_required_page
def descargar_plantilla():
    fecha_inicio = _parse_fecha_inicio(request.form.get("fecha_inicio") or "")
    cliente = (request.form.get("cliente") or "").strip()
    coordinador = (request.form.get("coordinador") or "").strip()
    if fecha_inicio is None:
        flash("La fecha inicio del periodo es obligatoria.", "error")
        return redirect(url_for("nomina.index"))
    if not cliente:
        flash("El cliente es obligatorio.", "error")
        return redirect(url_for("nomina.index"))
    if not coordinador:
        flash("El coordinador es obligatorio.", "error")
        return redirect(url_for("nomina.index"))

    fecha_fin = fecha_inicio + timedelta(days=6)
    base_rows = get_latest_import_base_rows(_db_path(), cliente, fecha_inicio)
    payload = build_asistencia_template_file(
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        cliente=cliente,
        coordinador=coordinador,
        base_rows=base_rows,
    )
    output = BytesIO(payload)
    output.seek(0)
    filename = f"Master_Asistencia_{fecha_inicio.strftime('%Y%m%d')}_{cliente.replace(' ', '_')}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@nomina_bp.post("/importar")
@_login_required_page
def importar_asistencia():
    file = request.files.get("excel_file")
    if file is None or not (file.filename or "").strip():
        flash("Debes seleccionar un archivo .xlsx para importar.", "error")
        return redirect(url_for("nomina.index"))

    filename = file.filename or "asistencia.xlsx"
    if not filename.lower().endswith(".xlsx"):
        flash("Solo se permiten archivos .xlsx en este flujo.", "error")
        return redirect(url_for("nomina.index"))

    try:
        file_bytes = file.read()
        parsed = parse_and_validate_asistencia_excel(file_bytes, filename=filename)
    except ValidationError as exc:
        flash(str(exc), "error")
        return redirect(url_for("nomina.index"))
    except Exception as exc:
        flash(f"No se pudo leer el archivo: {exc}", "error")
        return redirect(url_for("nomina.index"))

    if parsed["blocking_errors"]:
        joined = " | ".join(parsed["blocking_errors"][:6])
        if len(parsed["blocking_errors"]) > 6:
            joined += " | ..."
        flash(
            "Importación bloqueada por errores graves (clave diaria inválida o números negativos): "
            + joined,
            "error",
        )
        return redirect(url_for("nomina.index"))

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
        "coordinador": parsed.get("coordinador") or "",
        "filename": filename,
        "status": "draft",
        "total_rows": parsed.get("total_rows") or 0,
        "error_count": parsed.get("error_count") or 0,
        "warning_count": parsed.get("warning_count") or 0,
        "rows": parsed.get("rows") or [],
        "raw_json": {
            "dias_headers": parsed.get("dias_headers") or [],
            "semana": parsed.get("semana") or "",
            "cliente": parsed.get("cliente") or "",
            "coordinador": parsed.get("coordinador") or "",
        },
    }
    created_by = int(g.user["id"]) if g.user is not None else None
    import_id = save_asistencia_import(_db_path(), payload, created_by=created_by, now_iso=_now_iso())
    flash("Archivo importado y guardado como borrador.", "success")
    return redirect(url_for("nomina.asistencia_importada", import_id=import_id))


@nomina_bp.get("/imports/<int:import_id>")
@_login_required_page
def asistencia_importada(import_id: int):
    imp = get_asistencia_import(_db_path(), import_id)
    if imp is None:
        flash("No se encontró la importación solicitada.", "error")
        return redirect(url_for("nomina.index"))
    return render_template("nomina/asistencia_importada.html", imp=imp)


def register_nomina(app) -> None:
    app.register_blueprint(nomina_bp)

