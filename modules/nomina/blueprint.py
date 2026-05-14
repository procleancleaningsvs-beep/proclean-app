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
    nomina_history_rows_for_headcount_fallback,
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


def _available_clientes_headcount() -> tuple[list[str], dict[str, list[str]], str | None, str]:
    try:
        clientes = sorted({str(a.get("cliente") or "").strip() for a in obtener_activos() if str(a.get("cliente") or "").strip()})
        agrupaciones_raw = alias_service.obtener_agrupaciones()
        agrupaciones: dict[str, list[str]] = {}
        for name, members in (agrupaciones_raw or {}).items():
            group_clients = [str(c).strip() for c in (members or []) if str(c).strip() in clientes]
            if group_clients:
                agrupaciones[str(name).strip()] = group_clients
        return clientes, agrupaciones, None, "headcount"
    except Exception as exc:
        fallback = nomina_clientes_from_history(_db_path())
        if fallback:
            return (
                fallback,
                {},
                f"Headcount no disponible ({exc}). Se muestran clientes detectados en historial de Nóminas.",
                "historial_fallback",
            )
        return [], {}, str(exc), "sin_fuente"


def _normalize_name(value: str) -> str:
    return " ".join(str(value or "").replace("\u00a0", " ").upper().split()).strip()


def _enrich_rows_with_headcount(rows: list[dict], db_path: str) -> tuple[list[dict], str, int]:
    indexed_by_cliente_nombre: dict[tuple[str, str], dict] = {}
    indexed_by_nombre: dict[str, dict] = {}
    source = "headcount"
    try:
        activos = obtener_activos()
        for item in activos:
            nombre = _normalize_name(str(item.get("nombre_completo") or ""))
            cliente = str(item.get("cliente") or "").strip().casefold()
            if not nombre:
                continue
            indexed_by_nombre.setdefault(nombre, item)
            indexed_by_cliente_nombre.setdefault((cliente, nombre), item)
    except Exception:
        source = "historial_fallback"
        for item in nomina_history_rows_for_headcount_fallback(db_path):
            nombre = _normalize_name(item.get("nombre_empleado") or "")
            cliente = str(item.get("cliente") or "").strip().casefold()
            if not nombre:
                continue
            indexed_by_cliente_nombre.setdefault(
                (cliente, nombre),
                {"nss": item.get("nss") or "", "nombre_completo": item.get("nombre_empleado") or "", "cliente": item.get("cliente") or ""},
            )
            indexed_by_nombre.setdefault(
                nombre,
                {"nss": item.get("nss") or "", "nombre_completo": item.get("nombre_empleado") or "", "cliente": item.get("cliente") or ""},
            )

    pending = 0
    for row in rows:
        nombre = _normalize_name(str(row.get("nombre_empleado") or ""))
        cliente = str(row.get("cliente") or "").strip().casefold()
        matched = indexed_by_cliente_nombre.get((cliente, nombre))
        match_status = "pending_review"
        score = 0.0
        nss = ""
        if matched:
            nss = str(matched.get("nss") or "").strip()
            if nss:
                match_status = "exact_nss"
                score = 1.0
            else:
                match_status = "name_match"
                score = 0.75
        else:
            generic = indexed_by_nombre.get(nombre)
            if generic:
                nss = str(generic.get("nss") or "").strip()
                if nss:
                    match_status = "headcount_ok"
                    score = 0.9
                else:
                    match_status = "name_match"
                    score = 0.7
        if match_status == "pending_review":
            pending += 1
        row["nss"] = nss
        row["headcount_match_status"] = match_status
        row["headcount_match_score"] = score
        row["headcount_source"] = source
    return rows, source, pending


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
    clientes, agrupaciones, headcount_error, headcount_source = _available_clientes_headcount()
    if role in _NOMINA_DASHBOARD_ROLES:
        dash = nomina_dashboard_overview(_db_path(), recent_limit=12)
        return render_template(
            "nomina/dashboard.html",
            dash=dash,
            headcount_error=headcount_error,
            headcount_source=headcount_source,
        )
    return render_template(
        "nomina/index.html",
        coordinador_display=_coordinador_display_name(),
        clientes=clientes,
        agrupaciones=agrupaciones,
        headcount_error=headcount_error,
        headcount_source=headcount_source,
    )


@nomina_bp.get("/master")
@_nomina_access_required
def master_hub():
    clientes, agrupaciones, headcount_error, headcount_source = _available_clientes_headcount()
    return render_template(
        "nomina/index.html",
        coordinador_display=_coordinador_display_name(),
        clientes=clientes,
        agrupaciones=agrupaciones,
        headcount_error=headcount_error,
        headcount_source=headcount_source,
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
    duplicate_base_warnings: list[str] = []
    if len(clientes) == 1:
        base_rows = get_latest_import_base_rows(_db_path(), clientes[0], fecha_inicio)
    else:
        base_rows, duplicate_base_warnings = get_latest_import_base_rows_multi(_db_path(), clientes, fecha_inicio)

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
    if duplicate_base_warnings:
        flash(
            "Se omitieron posibles duplicados en base previa: " + " | ".join(duplicate_base_warnings[:3]),
            "warning",
        )
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

    parsed_rows = parsed.get("rows") or []
    parsed_rows, headcount_source, pending_matches = _enrich_rows_with_headcount(parsed_rows, _db_path())
    clientes_detectados = sorted(
        {str(r.get("cliente") or "").strip() for r in parsed_rows if str(r.get("cliente") or "").strip()}
    )

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
        "rows": parsed_rows,
        "clientes": clientes_detectados,
        "headcount_source": headcount_source,
        "raw_json": {
            "dias_headers": parsed.get("dias_headers") or [],
            "semana": parsed.get("semana") or "",
            "cliente": parsed.get("cliente") or "",
            "coordinador_archivo": parsed.get("coordinador") or "",
            "coordinador_session": _coordinador_display_name(),
            "clientes_detectados": clientes_detectados,
            "headcount_source": headcount_source,
            "pending_headcount_matches": pending_matches,
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

