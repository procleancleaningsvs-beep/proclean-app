from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from io import BytesIO
from pathlib import Path
import unicodedata

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
    get_vacaciones_stats,
    get_vacaciones_stats_by_import,
    get_latest_vacaciones_import_id,
    list_vacaciones_imports,
    list_vacaciones_empleados,
    save_vacaciones_import,
    get_vacaciones_import,
    get_vacaciones_empleado,
    update_vacaciones_empleado,
    get_infonavit_stats,
    get_infonavit_stats_by_import,
    get_latest_infonavit_import_id,
    list_infonavit_imports,
    list_infonavit_rows,
    save_infonavit_import,
    get_infonavit_import,
    get_infonavit_row,
    update_infonavit_row,
    save_asistencia_import,
    save_parametros_import,
    upsert_empleado_parametros,
    list_empleado_parametros,
    get_empleado_parametro,
    update_empleado_parametro,
    get_parametros_stats,
    list_parametros_imports,
    upsert_localidades_frontera,
    list_localidades_frontera,
)
from modules.nomina.validators import ValidationError, parse_and_validate_asistencia_excel
from modules.nomina.vacaciones_excel import parse_vacaciones_historico_excel
from modules.nomina.headcount_bridge import obtener_headcount_completo
from modules.nomina.infonavit_pdf import parse_infonavit_pdf
from modules.nomina.config import (
    get_exento_he_for_year,
    get_smg_for_year,
    get_umi_for_year,
)
from modules.nomina.parametros_excel import parse_nomina_actual
from modules.nomina.contpaq_excel import parse_contpaq
from modules.nomina.parametros_match import (
    build_headcount_index,
    build_parametro_row_from_contpaq,
    build_parametro_row_from_nomina,
)
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


def _nomina_dashboard_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        role = _current_role()
        if role not in _NOMINA_DASHBOARD_ROLES:
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


def _normalize_name(value: str) -> str:
    s = " ".join(str(value or "").replace("\u00a0", " ").upper().split()).strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return " ".join(s.split()).strip()


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
        vac_stats = get_vacaciones_stats(_db_path())
        inf_stats = get_infonavit_stats(_db_path())
        param_stats = get_parametros_stats(_db_path())
        param_localidades = list_localidades_frontera(_db_path())
        return render_template(
            "nomina/dashboard.html",
            dash=dash,
            vac_stats=vac_stats,
            inf_stats=inf_stats,
            param_stats=param_stats,
            param_localidades_count=len(param_localidades),
            param_localidades_frontera_count=sum(1 for it in param_localidades if it.get("es_frontera")),
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


def _iso_to_ordinal(value: str) -> int | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date().toordinal()
    except ValueError:
        return None


def _build_headcount_indices(db_path: str) -> tuple[dict[str, dict], dict[str, list[dict]], str]:
    try:
        activos = obtener_headcount_completo()
        source = "headcount"
    except Exception:
        activos = []
        source = "historial_fallback"
        for r in nomina_history_rows_for_headcount_fallback(db_path):
            activos.append(
                {
                    "nss": r.get("nss") or "",
                    "nombre_completo": r.get("nombre_empleado") or "",
                    "cliente": r.get("cliente") or "",
                    "fecha_ingreso": "",
                    "status_imss": "NO_DISPONIBLE",
                    "status_operacion": "NO_DISPONIBLE",
                    "patron": "",
                    "sueldo_diario": None,
                    "puesto": "",
                }
            )
    by_nss: dict[str, dict] = {}
    by_name: dict[str, list[dict]] = {}
    for item in activos:
        nss = str(item.get("nss") or "").strip()
        if nss and nss not in by_nss:
            by_nss[nss] = item
        n = _normalize_name(str(item.get("nombre_completo") or ""))
        if not n:
            continue
        by_name.setdefault(n, []).append(item)
    return by_nss, by_name, source


def _is_headcount_active(item: dict | None) -> bool:
    if not item:
        return False
    status_op = str(item.get("status_operacion") or "").strip().upper()
    status_imss = str(item.get("status_imss") or "").strip().upper()
    if "BAJA" in status_op:
        return False
    if status_op == "ALTA":
        return True
    return "ACTIV" in status_imss


def _match_vacaciones_rows(rows: list[dict], db_path: str) -> tuple[list[dict], str, int, int]:
    by_nss, by_name, source = _build_headcount_indices(db_path)
    matched = 0
    warnings_total = 0
    for row in rows:
        row_warnings = list(row.get("warnings") or [])
        match_status = "no_match"
        match_score = 0.0
        hc_row: dict | None = None
        nss = str(row.get("nss") or "").strip()
        nombre_norm = _normalize_name(str(row.get("nombre_historico") or ""))
        cliente_norm = str(row.get("cliente") or "").strip().casefold()

        if nss and nss in by_nss:
            hc_row = by_nss[nss]
            match_status = "exact_nss"
            match_score = 1.0
        else:
            candidates = by_name.get(nombre_norm) or []
            if len(candidates) == 1:
                hc_row = candidates[0]
                match_status = "match_name"
                match_score = 0.92
            elif len(candidates) > 1:
                maybe = None
                for cand in candidates:
                    if str(cand.get("cliente") or "").strip().casefold() == cliente_norm:
                        maybe = cand
                        break
                if maybe is not None:
                    hc_row = maybe
                    match_status = "probable_match"
                    match_score = 0.6
                    row_warnings.append("Match probable por nombre y cliente; requiere revisión manual.")
                else:
                    match_status = "pending_review"
                    match_score = 0.35
                    row_warnings.append("Múltiples candidatos por nombre en Headcount; requiere revisión.")

        if hc_row is None:
            if match_status == "pending_review":
                row_warnings.append("No se logró resolver match automático; revisión manual requerida.")
                row["estatus_headcount"] = "PENDIENTE_REVISION"
                row["match_status"] = "pending_review"
                row["match_score"] = max(match_score, 0.3)
                row["headcount_source"] = source
                row["headcount_raw_status"] = ""
                row["warnings"] = row_warnings
                warnings_total += len(row_warnings)
                continue
            row_warnings.append("Sin match en Headcount; posible inactivo/baja.")
            row["estatus_headcount"] = "INACTIVO_O_NO_ENCONTRADO"
            row["match_status"] = "no_match"
            row["match_score"] = 0.0
            row["headcount_source"] = source
            row["headcount_raw_status"] = ""
            row["warnings"] = row_warnings
            warnings_total += len(row_warnings)
            continue

        matched += 1
        row["nombre_headcount"] = str(hc_row.get("nombre_completo") or "").strip()
        row["planta_headcount"] = str(hc_row.get("patron") or "").strip()
        row["fecha_ingreso_headcount"] = str(hc_row.get("fecha_ingreso") or "").strip()
        row["estatus_headcount"] = str(hc_row.get("status_imss") or "DESCONOCIDO").strip() or "DESCONOCIDO"
        row["sueldo_headcount"] = hc_row.get("sueldo_diario")
        row["sueldo_usado"] = row["sueldo_headcount"] if row.get("sueldo_headcount") not in (None, "") else row.get("sueldo_historico")
        if not row.get("nss"):
            row["nss"] = str(hc_row.get("nss") or "").strip()
        active = _is_headcount_active(hc_row)
        if not active:
            match_status = "inactive_match"
            row_warnings.append("Trabajador encontrado en Headcount pero con estatus inactivo/baja.")
        row["match_status"] = match_status
        row["match_score"] = match_score
        row["headcount_source"] = source
        row["headcount_raw_status"] = f"{str(hc_row.get('status_operacion') or '').strip()}|{str(hc_row.get('status_imss') or '').strip()}"
        if row.get("monto_total_historico") not in (None, "") and row.get("sueldo_usado") in (None, ""):
            row_warnings.append("Sueldo usado vacío; no se puede recalcular monto total de forma confiable.")

        hist_ord = _iso_to_ordinal(str(row.get("fecha_ingreso_historica") or ""))
        hc_ord = _iso_to_ordinal(str(row.get("fecha_ingreso_headcount") or ""))
        if hc_ord is not None:
            row["fecha_ingreso_usada"] = row.get("fecha_ingreso_headcount")
        if hist_ord is not None and hc_ord is not None and hist_ord != hc_ord:
            row_warnings.append(
                "Fecha de ingreso en histórico no coincide con Headcount. Se usará Headcount salvo revisión manual."
            )
            if hc_ord - hist_ord > 30:
                row_warnings.append("Posible reingreso / validar saldo histórico.")
                row["match_status"] = "possible_reentry"

        if source == "historial_fallback":
            row_warnings.append("Datos cruzados con historial interno (fallback), no con Headcount en vivo.")

        row["warnings"] = row_warnings
        warnings_total += len(row_warnings)
    return rows, source, matched, warnings_total


def _year_from_fecha_corte(fecha_corte: str) -> int | None:
    txt = (fecha_corte or "").strip()
    if not txt:
        return None
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", txt)
    if m:
        return int(m.group(3))
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", txt)
    if m:
        return int(m.group(1))
    return None


def _decimal_or_none(raw: str) -> Decimal | None:
    s = (raw or "").strip().replace(",", "")
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _infonavit_descuento_logic(row: dict, umi: Decimal | None) -> None:
    warnings = list(row.get("warnings") or [])
    estatus = str(row.get("estatus_infonavit") or "").strip().upper()
    descuento_raw = str(row.get("descuento_raw") or "").strip()
    tipo_desc = str(row.get("tipo_descuento") or "").strip()

    row["tipo_descuento"] = tipo_desc
    row["tipo_valor_descuento"] = "SIN_MONTO"
    row["descuento_monto_pesos"] = None
    row["descuento_factor_vsm"] = None
    row["umi_usada"] = None
    row["descuento_cf_calculada"] = None

    editable: dict = {
        "revision_status": "pending_revision",
        "aplicar_descuento": False,
        "listo_para_calculo": False,
    }

    if estatus == "SUSPENDIDO":
        if "suspension" not in _normalize_name(str(row.get("motivo_aviso") or "")).lower():
            warnings.append("Suspension sin motivo claro; requiere revision.")
        row["editable_json"] = editable
        row["warnings"] = warnings
        return

    m_pesos = re.search(r"\$\s*([0-9,]+(?:\.[0-9]{1,4})?)", descuento_raw)
    m_vsm = re.search(r"([0-9]+(?:\.[0-9]{1,6})?)\s*VSM", descuento_raw, flags=re.IGNORECASE)
    tipo_desc_norm = _normalize_name(tipo_desc)
    if m_pesos:
        monto = _decimal_or_none(m_pesos.group(1))
        if monto is not None:
            monto = monto.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            row["tipo_valor_descuento"] = "PESOS"
            row["descuento_monto_pesos"] = float(monto)
            row["descuento_cf_calculada"] = float(monto)
        else:
            warnings.append("No se pudo convertir el monto en pesos.")
    elif m_vsm or "VSM" in tipo_desc_norm:
        factor_txt = m_vsm.group(1) if m_vsm else ""
        factor = _decimal_or_none(factor_txt)
        row["tipo_valor_descuento"] = "VSM"
        warnings.append("Descuento en VSM detectado.")
        if factor is None:
            warnings.append("Conversion VSM no pudo realizarse.")
            editable["revision_status"] = "pending_review"
        elif umi is None:
            warnings.append("UMI no configurada para el anio del reporte; CF en pesos no calculada.")
            row["descuento_factor_vsm"] = float(factor)
            editable["revision_status"] = "pending_review"
            editable["umi_no_configurada"] = True
        else:
            cf = (factor * umi).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            row["descuento_factor_vsm"] = float(factor)
            row["umi_usada"] = float(umi)
            row["descuento_cf_calculada"] = float(cf)
            row["descuento_monto_pesos"] = float(cf)
            warnings.append("VSM convertido a pesos para revision.")
    else:
        if estatus in {"ACTIVO", "ACTIVO_MODIFICADO"}:
            warnings.append("Retencion/Modificacion sin monto de descuento.")
        if tipo_desc and "PESOS" not in tipo_desc_norm and "VSM" not in tipo_desc_norm:
            warnings.append("Tipo de descuento no reconocido.")

    row["editable_json"] = editable
    row["warnings"] = warnings


def _match_infonavit_rows(rows: list[dict], db_path: str) -> tuple[list[dict], str]:
    by_nss, by_name, source = _build_headcount_indices(db_path)
    for row in rows:
        warnings = list(row.get("warnings") or [])
        nss = str(row.get("nss") or "").strip()
        nombre_norm = _normalize_name(str(row.get("nombre_trabajador") or ""))
        match_status = "no_match"
        match_score = 0.0
        hc_row: dict | None = None
        if nss and nss in by_nss:
            hc_row = by_nss[nss]
            match_status = "exact_nss"
            match_score = 1.0
        else:
            candidates = by_name.get(nombre_norm) or []
            if len(candidates) == 1:
                hc_row = candidates[0]
                match_status = "match_name"
                match_score = 0.88
            elif len(candidates) > 1:
                match_status = "probable_match"
                match_score = 0.5
                warnings.append("Multiples candidatos por nombre en Headcount.")
                hc_row = candidates[0]
            else:
                warnings.append("No se encontro trabajador en Headcount.")

        if hc_row is not None:
            row["nombre_headcount"] = str(hc_row.get("nombre_completo") or "").strip()
            row["cliente_headcount"] = str(hc_row.get("cliente") or "").strip()
            row["planta_headcount"] = str(hc_row.get("patron") or "").strip()
            row["estatus_headcount"] = str(hc_row.get("status_imss") or "").strip()
            if not _is_headcount_active(hc_row):
                warnings.append("Trabajador con aviso INFONAVIT, pero Headcount lo marca inactivo/baja.")
                match_status = "inactive_match"
                match_score = min(match_score, 0.7) if match_score > 0 else 0.7
            if not row.get("nss"):
                row["nss"] = str(hc_row.get("nss") or "").strip()
        else:
            row["nombre_headcount"] = ""
            row["cliente_headcount"] = ""
            row["planta_headcount"] = ""
            row["estatus_headcount"] = "NO_ENCONTRADO"
            if match_status == "probable_match":
                match_status = "pending_review"
        row["match_status"] = match_status
        row["match_score"] = match_score
        row["warnings"] = warnings
    return rows, source


def _prepare_infonavit_rows(parsed_rows: list[dict], fecha_corte: str, db_path: str) -> tuple[list[dict], str]:
    year = _year_from_fecha_corte(fecha_corte)
    umi = get_umi_for_year(year)
    prepared = [dict(r) for r in parsed_rows]
    for row in prepared:
        _infonavit_descuento_logic(row, umi)
    prepared, headcount_source = _match_infonavit_rows(prepared, db_path)
    return prepared, headcount_source


@nomina_bp.get("/vacaciones")
@_nomina_dashboard_required
def vacaciones_index():
    latest_import_id = get_latest_vacaciones_import_id(_db_path())
    import_id_raw = (request.args.get("import_id") or "").strip()
    selected_import_id = int(import_id_raw) if import_id_raw.isdigit() else latest_import_id
    cliente = (request.args.get("cliente") or "").strip() or None
    match_status = (request.args.get("match_status") or "").strip() or None
    activo = (request.args.get("activo") or "").strip() or None
    prima_pagada = (request.args.get("prima_pagada") or "").strip() or None
    revision_status = (request.args.get("revision_status") or "").strip() or None
    con_alerta = request.args.get("con_alerta")
    con_alerta_bool = True if con_alerta == "1" else None
    rows = list_vacaciones_empleados(
        _db_path(),
        cliente=cliente,
        match_status=match_status,
        activo=activo,
        con_alerta=con_alerta_bool,
        prima_pagada=prima_pagada,
        revision_status=revision_status,
        import_id=selected_import_id,
        limit=700,
    )
    stats = get_vacaciones_stats_by_import(_db_path(), selected_import_id)
    imports = list_vacaciones_imports(_db_path(), limit=100)
    clientes = sorted({str(r.get("cliente") or "").strip() for r in rows if str(r.get("cliente") or "").strip()})
    return render_template(
        "nomina/vacaciones_index.html",
        stats=stats,
        rows=rows,
        clientes=clientes,
        filtros={
            "import_id": str(selected_import_id or ""),
            "cliente": cliente or "",
            "match_status": match_status or "",
            "activo": activo or "",
            "prima_pagada": prima_pagada or "",
            "revision_status": revision_status or "",
            "con_alerta": con_alerta or "",
        },
        imports=imports,
        latest_import_id=latest_import_id,
        selected_import_id=selected_import_id,
    )


@nomina_bp.post("/vacaciones/importar")
@_nomina_dashboard_required
def vacaciones_importar():
    file = request.files.get("excel_file")
    if file is None or not (file.filename or "").strip():
        flash("Debes seleccionar un Excel histórico de vacaciones.", "error")
        return redirect(url_for("nomina.vacaciones_index"))
    filename = file.filename or "vacaciones.xlsx"
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        flash("Formato no soportado. Usa .xlsx/.xlsm", "error")
        return redirect(url_for("nomina.vacaciones_index"))

    try:
        file_bytes = file.read()
        parsed = parse_vacaciones_historico_excel(file_bytes, filename=filename)
    except Exception as exc:
        flash(f"No se pudo leer el archivo de vacaciones: {exc}", "error")
        return redirect(url_for("nomina.vacaciones_index"))

    rows = parsed.rows
    rows, source, matched_count, warning_count = _match_vacaciones_rows(rows, _db_path())
    error_count = len(parsed.errors)
    if error_count > 0:
        flash("Se omitieron filas con error de estructura en importación histórica.", "warning")
    if not rows:
        flash("No se detectaron filas válidas para importar vacaciones.", "error")
        return redirect(url_for("nomina.vacaciones_index"))

    payload = {
        "cliente": parsed.cliente or "Carrier",
        "source_filename": filename,
        "file_hash": hashlib.sha256(file_bytes).hexdigest(),
        "total_rows": len(rows),
        "matched_count": matched_count,
        "warning_count": warning_count,
        "error_count": error_count,
        "rows": rows,
        "raw_json": {
            "source": source,
            "parse_warnings": parsed.warnings,
            "parse_errors": parsed.errors,
            "created_for": "nomina_vacaciones",
        },
    }
    uid = int(g.user["id"]) if g.user is not None else None
    import_id = save_vacaciones_import(_db_path(), payload, created_by=uid, now_iso=_now_iso())
    flash("Importación histórica de vacaciones procesada.", "success")
    return redirect(url_for("nomina.vacaciones_import_detail", import_id=import_id))


@nomina_bp.get("/vacaciones/imports/<int:import_id>")
@_nomina_dashboard_required
def vacaciones_import_detail(import_id: int):
    imp = get_vacaciones_import(_db_path(), import_id)
    if imp is None:
        flash("Importación de vacaciones no encontrada.", "error")
        return redirect(url_for("nomina.vacaciones_index"))
    return render_template("nomina/vacaciones_import_detail.html", imp=imp)


@nomina_bp.route("/vacaciones/<int:row_id>/editar", methods=["GET", "POST"])
@_nomina_dashboard_required
def vacaciones_editar(row_id: int):
    row = get_vacaciones_empleado(_db_path(), row_id)
    if row is None:
        flash("Registro de vacaciones no encontrado.", "error")
        return redirect(url_for("nomina.vacaciones_index"))
    if request.method == "POST":
        def _f(name: str) -> float | None:
            raw = (request.form.get(name) or "").strip().replace(",", "")
            if not raw:
                return None
            try:
                return float(raw)
            except ValueError:
                return None

        dias_utilizados = _f("dias_utilizados") or 0.0
        vacaciones_laboradas = _f("vacaciones_laboradas") or 0.0
        dias_pagados = _f("dias_pagados") or 0.0
        dias_vac = _f("dias_vacaciones_historico")
        if dias_vac is None:
            dias_vac = float(row.get("dias_vacaciones_historico") or 0.0)
        rest_manual = _f("dias_restantes_calculado")
        consumed = max(dias_pagados, dias_utilizados + vacaciones_laboradas)
        rest_calc = rest_manual if rest_manual is not None else (dias_vac - consumed)

        updates = {
            "fecha_ingreso_usada": (request.form.get("fecha_ingreso_usada") or "").strip(),
            "sueldo_usado": _f("sueldo_usado"),
            "dias_utilizados": dias_utilizados,
            "vacaciones_laboradas": vacaciones_laboradas,
            "dias_pagados": dias_pagados,
            "dias_restantes_calculado": rest_calc,
            "prima_2025_pagada": (request.form.get("prima_2025_pagada") or "") in {"1", "on", "true"},
            "prima_2026_pagada": (request.form.get("prima_2026_pagada") or "") in {"1", "on", "true"},
            "fecha_pago_prima_2026": (request.form.get("fecha_pago_prima_2026") or "").strip(),
            "comentarios": (request.form.get("comentarios") or "").strip(),
            "editable_json": {
                "revision_status": (request.form.get("revision_status") or "").strip() or "pending_revision",
            },
        }
        uid = int(g.user["id"]) if g.user is not None else None
        ok = update_vacaciones_empleado(
            _db_path(),
            row_id,
            updates,
            updated_by=uid,
            updated_at=_now_iso(),
        )
        if ok:
            flash("Registro de vacaciones actualizado.", "success")
        else:
            flash("No se pudo actualizar el registro.", "error")
        return redirect(url_for("nomina.vacaciones_editar", row_id=row_id))

    return render_template("nomina/vacaciones_edit.html", row=row)


@nomina_bp.get("/infonavit")
@_nomina_dashboard_required
def infonavit_index():
    latest_import_id = get_latest_infonavit_import_id(_db_path())
    import_id_raw = (request.args.get("import_id") or "").strip()
    selected_import_id = int(import_id_raw) if import_id_raw.isdigit() else latest_import_id
    match_status = (request.args.get("match_status") or "").strip() or None
    estatus_infonavit = (request.args.get("estatus_infonavit") or "").strip() or None
    revision_status = (request.args.get("revision_status") or "").strip() or None
    rows = list_infonavit_rows(
        _db_path(),
        import_id=selected_import_id,
        match_status=match_status,
        estatus_infonavit=estatus_infonavit,
        revision_status=revision_status,
        limit=900,
    )
    stats = get_infonavit_stats_by_import(_db_path(), selected_import_id)
    imports = list_infonavit_imports(_db_path(), limit=100)
    return render_template(
        "nomina/infonavit_index.html",
        rows=rows,
        stats=stats,
        imports=imports,
        latest_import_id=latest_import_id,
        selected_import_id=selected_import_id,
        filtros={
            "import_id": str(selected_import_id or ""),
            "match_status": match_status or "",
            "estatus_infonavit": estatus_infonavit or "",
            "revision_status": revision_status or "",
        },
    )


@nomina_bp.post("/infonavit/importar")
@_nomina_dashboard_required
def infonavit_importar():
    file = request.files.get("pdf_file")
    if file is None or not (file.filename or "").strip():
        flash("Debes seleccionar un PDF de INFONAVIT.", "error")
        return redirect(url_for("nomina.infonavit_index"))
    filename = file.filename or "infonavit.pdf"
    if not filename.lower().endswith(".pdf"):
        flash("Formato no soportado. Usa PDF.", "error")
        return redirect(url_for("nomina.infonavit_index"))

    try:
        file_bytes = file.read()
        parsed = parse_infonavit_pdf(file_bytes, filename=filename)
    except Exception as exc:
        flash(f"No se pudo leer el PDF INFONAVIT: {exc}", "error")
        return redirect(url_for("nomina.infonavit_index"))

    if parsed.errors:
        flash("No fue posible extraer avisos validos del PDF.", "error")
        return redirect(url_for("nomina.infonavit_index"))

    rows, headcount_source = _prepare_infonavit_rows(parsed.rows, str(parsed.metadata.get("fecha_corte") or ""), _db_path())
    warning_count = sum(len(r.get("warnings") or []) for r in rows) + len(parsed.warnings)
    active_count = sum(1 for r in rows if str(r.get("estatus_infonavit") or "") == "ACTIVO")
    modified_count = sum(1 for r in rows if str(r.get("estatus_infonavit") or "") == "ACTIVO_MODIFICADO")
    suspended_count = sum(1 for r in rows if str(r.get("estatus_infonavit") or "") == "SUSPENDIDO")
    vsm_count = sum(1 for r in rows if str(r.get("tipo_valor_descuento") or "") == "VSM")

    payload = {
        "registro_patronal": parsed.metadata.get("registro_patronal") or "",
        "fecha_corte": parsed.metadata.get("fecha_corte") or "",
        "total_avisos_reportado": parsed.metadata.get("total_avisos_reportado") or 0,
        "total_rows": len(rows),
        "active_count": active_count,
        "modified_count": modified_count,
        "suspended_count": suspended_count,
        "vsm_count": vsm_count,
        "warning_count": warning_count,
        "error_count": len(parsed.errors),
        "source_filename": filename,
        "file_hash": hashlib.sha256(file_bytes).hexdigest(),
        "rows": rows,
        "raw_json": {
            "metadata": parsed.metadata,
            "parse_warnings": parsed.warnings,
            "parse_errors": parsed.errors,
            "headcount_source": headcount_source,
        },
    }
    uid = int(g.user["id"]) if g.user is not None else None
    import_id = save_infonavit_import(_db_path(), payload, created_by=uid, now_iso=_now_iso())
    if parsed.warnings:
        flash("Importacion realizada con warnings: " + " | ".join(parsed.warnings[:2]), "warning")
    flash("Importacion INFONAVIT procesada.", "success")
    return redirect(url_for("nomina.infonavit_import_detail", import_id=import_id))


@nomina_bp.get("/infonavit/imports/<int:import_id>")
@_nomina_dashboard_required
def infonavit_import_detail(import_id: int):
    imp = get_infonavit_import(_db_path(), import_id)
    if imp is None:
        flash("Importacion INFONAVIT no encontrada.", "error")
        return redirect(url_for("nomina.infonavit_index"))
    return render_template("nomina/infonavit_import_detail.html", imp=imp)


@nomina_bp.route("/infonavit/<int:row_id>/editar", methods=["GET", "POST"])
@_nomina_dashboard_required
def infonavit_editar(row_id: int):
    row = get_infonavit_row(_db_path(), row_id)
    if row is None:
        flash("Registro INFONAVIT no encontrado.", "error")
        return redirect(url_for("nomina.infonavit_index"))
    if request.method == "POST":
        def _f(name: str) -> float | None:
            raw = (request.form.get(name) or "").strip().replace(",", "")
            if not raw:
                return None
            try:
                return float(raw)
            except ValueError:
                return None

        editable_json = dict(row.get("editable_json") or {})
        editable_json["revision_status"] = (request.form.get("revision_status") or "").strip() or "pending_review"
        editable_json["comentarios_revision"] = (request.form.get("comentarios_revision") or "").strip()
        updates = {
            "estatus_infonavit": (request.form.get("estatus_infonavit") or "").strip() or "REVISION",
            "descuento_monto_pesos": _f("descuento_monto_pesos"),
            "descuento_factor_vsm": _f("descuento_factor_vsm"),
            "umi_usada": _f("umi_usada"),
            "descuento_cf_calculada": _f("descuento_cf_calculada"),
            "tipo_valor_descuento": (request.form.get("tipo_valor_descuento") or "").strip() or "SIN_MONTO",
            "match_status": (request.form.get("match_status") or "").strip() or "pending_review",
            "editable_json": editable_json,
        }
        uid = int(g.user["id"]) if g.user is not None else None
        ok = update_infonavit_row(
            _db_path(),
            row_id,
            updates,
            updated_by=uid,
            updated_at=_now_iso(),
        )
        if ok:
            flash("Registro INFONAVIT actualizado.", "success")
        else:
            flash("No se pudo actualizar el registro INFONAVIT.", "error")
        return redirect(url_for("nomina.infonavit_editar", row_id=row_id))
    return render_template("nomina/infonavit_edit.html", row=row)


# ---------------------------------------------------------------------------
# Parámetros de Nómina (Microfase 4.0)
# ---------------------------------------------------------------------------

def _parametros_year_default() -> int:
    return date.today().year


def _headcount_for_match() -> tuple[list[dict], str | None]:
    try:
        return obtener_headcount_completo(), None
    except Exception as exc:  # pragma: no cover - depends on external excel
        return [], str(exc)


@nomina_bp.get("/parametros")
@_nomina_dashboard_required
def parametros_index():
    db_path = _db_path()
    cliente = (request.args.get("cliente") or "").strip() or None
    status_filter = (request.args.get("status") or "").strip().lower()
    only_missing_salary = request.args.get("missing_salario") == "1"
    only_missing_he = request.args.get("missing_he") == "1"

    match_filters: list[str] = []
    if status_filter == "pendientes":
        match_filters = [
            "no_match_headcount",
            "no_match_contpaq",
            "probable_match",
            "multiple_candidates",
            "pending_review",
        ]

    rows = list_empleado_parametros(
        db_path,
        cliente=cliente,
        match_status_any=match_filters or None,
        only_missing_salary=only_missing_salary,
        only_missing_valor_he=only_missing_he,
        limit=2000,
    )
    stats = get_parametros_stats(db_path)
    imports = list_parametros_imports(db_path, limit=20)
    localidades = list_localidades_frontera(db_path)
    return render_template(
        "nomina/parametros_index.html",
        rows=rows,
        stats=stats,
        imports=imports,
        localidades=localidades,
        filtros={
            "cliente": cliente or "",
            "status": status_filter,
            "missing_salario": only_missing_salary,
            "missing_he": only_missing_he,
        },
    )


@nomina_bp.post("/parametros/importar-nomina")
@_nomina_dashboard_required
def parametros_importar_nomina():
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Selecciona un archivo Excel de nómina actual.", "error")
        return redirect(url_for("nomina.parametros_index"))
    cliente_hint = (request.form.get("cliente") or "").strip()
    file_bytes = file.read()
    if not file_bytes:
        flash("Archivo vacío.", "error")
        return redirect(url_for("nomina.parametros_index"))
    if not file.filename.lower().endswith((".xlsx", ".xlsm")):
        flash("Solo se permiten archivos Excel (.xlsx / .xlsm).", "error")
        return redirect(url_for("nomina.parametros_index"))
    try:
        parsed = parse_nomina_actual(file_bytes, filename=file.filename, cliente_hint=cliente_hint)
    except ValueError as exc:
        flash(f"No se pudo importar: {exc}", "error")
        return redirect(url_for("nomina.parametros_index"))

    db_path = _db_path()
    hc_rows, hc_err = _headcount_for_match()
    if hc_err:
        parsed["warnings"].append(f"No se pudo cargar Headcount: {hc_err}")
    hc_index = build_headcount_index(hc_rows)

    # Save detected localidades first so subsequent rows benefit.
    if parsed.get("localidades"):
        upsert_localidades_frontera(db_path, parsed["localidades"], now_iso=_now_iso())

    year = _parametros_year_default()
    payload_rows: list[dict] = []
    matched = 0
    for parsed_row in parsed["rows"]:
        p = build_parametro_row_from_nomina(
            parsed_row,
            hc_index=hc_index,
            db_path=db_path,
            year=year,
            source_filename=file.filename,
        )
        if p["headcount_match_status"] in {"exact_nss", "exact_name"}:
            matched += 1
        payload_rows.append(p)

    file_hash = hashlib.sha256(file_bytes).hexdigest()
    import_payload = {
        "tipo_importacion": "NOMINA_ACTUAL",
        "cliente": cliente_hint,
        "source_filename": file.filename,
        "file_hash": file_hash,
        "total_rows": parsed["total_rows"],
        "matched_count": matched,
        "warning_count": sum(len(r.get("warnings") or []) for r in payload_rows) + len(parsed.get("warnings") or []),
        "error_count": len(parsed.get("errors") or []),
        "raw_json": {
            "sheet": parsed.get("sheet"),
            "file_warnings": parsed.get("warnings") or [],
            "file_errors": parsed.get("errors") or [],
        },
    }
    uid = int(g.user["id"]) if g.user is not None else None
    import_id = save_parametros_import(db_path, import_payload, created_by=uid, now_iso=_now_iso())
    inserted, updated = upsert_empleado_parametros(
        db_path,
        payload_rows,
        import_id=import_id,
        now_iso=_now_iso(),
        overwrite_keys={
            "salario_operativo",
            "valor_x_he",
            "banco",
            "cuenta",
            "planta",
            "localidad",
            "localidad_normalizada",
            "es_frontera",
            "salario_minimo_usado",
            "exento_he_usado",
        },
    )
    flash(
        f"Nómina importada: {parsed['total_rows']} filas. Nuevos {inserted}, actualizados {updated}. "
        f"Match Headcount exacto: {matched}.",
        "success",
    )
    return redirect(url_for("nomina.parametros_index"))


@nomina_bp.post("/parametros/importar-contpaq")
@_nomina_dashboard_required
def parametros_importar_contpaq():
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Selecciona un archivo Excel exportado de CONTPAQ.", "error")
        return redirect(url_for("nomina.parametros_index"))
    file_bytes = file.read()
    if not file_bytes:
        flash("Archivo CONTPAQ vacío.", "error")
        return redirect(url_for("nomina.parametros_index"))
    if not file.filename.lower().endswith((".xlsx", ".xlsm")):
        flash("Solo se permiten archivos Excel (.xlsx / .xlsm).", "error")
        return redirect(url_for("nomina.parametros_index"))
    try:
        parsed = parse_contpaq(file_bytes, filename=file.filename)
    except ValueError as exc:
        flash(f"No se pudo importar CONTPAQ: {exc}", "error")
        return redirect(url_for("nomina.parametros_index"))

    db_path = _db_path()
    hc_rows, hc_err = _headcount_for_match()
    if hc_err:
        parsed["warnings"].append(f"No se pudo cargar Headcount: {hc_err}")
    hc_index = build_headcount_index(hc_rows)

    payload_rows: list[dict] = []
    matched = 0
    for parsed_row in parsed["rows"]:
        p = build_parametro_row_from_contpaq(
            parsed_row,
            hc_index=hc_index,
            source_filename=file.filename,
        )
        if p["headcount_match_status"] in {"exact_nss", "exact_name"}:
            matched += 1
        payload_rows.append(p)

    file_hash = hashlib.sha256(file_bytes).hexdigest()
    import_payload = {
        "tipo_importacion": "CONTPAQ",
        "cliente": "",
        "source_filename": file.filename,
        "file_hash": file_hash,
        "total_rows": parsed["total_rows"],
        "matched_count": matched,
        "warning_count": sum(len(r.get("warnings") or []) for r in payload_rows) + len(parsed.get("warnings") or []),
        "error_count": len(parsed.get("errors") or []),
        "raw_json": {
            "sheet": parsed.get("sheet"),
            "file_warnings": parsed.get("warnings") or [],
            "file_errors": parsed.get("errors") or [],
        },
    }
    uid = int(g.user["id"]) if g.user is not None else None
    import_id = save_parametros_import(db_path, import_payload, created_by=uid, now_iso=_now_iso())
    inserted, updated = upsert_empleado_parametros(
        db_path,
        payload_rows,
        import_id=import_id,
        now_iso=_now_iso(),
        overwrite_keys={"codigo_contpaq", "numero_empleado", "zona_salario_raw"},
    )
    flash(
        f"CONTPAQ importado: {parsed['total_rows']} filas. Nuevos {inserted}, actualizados {updated}. "
        f"Match Headcount exacto: {matched}.",
        "success",
    )
    return redirect(url_for("nomina.parametros_index"))


@nomina_bp.route("/parametros/<int:row_id>/editar", methods=["GET", "POST"])
@_nomina_dashboard_required
def parametros_editar(row_id: int):
    row = get_empleado_parametro(_db_path(), row_id)
    if not row:
        abort(404)

    if request.method == "POST":
        def _f(name: str) -> float | None:
            raw = (request.form.get(name) or "").strip().replace(",", "")
            if not raw:
                return None
            try:
                return float(raw)
            except ValueError:
                return None

        localidad = (request.form.get("localidad") or "").strip()
        from modules.nomina.parametros_excel import _norm_locality
        localidad_norm = _norm_locality(localidad)
        es_frontera_raw = (request.form.get("es_frontera") or "").strip().upper()
        es_frontera: bool | None
        if es_frontera_raw in {"1", "TRUE", "VERDADERO", "SI"}:
            es_frontera = True
        elif es_frontera_raw in {"0", "FALSE", "FALSO", "NO"}:
            es_frontera = False
        else:
            es_frontera = bool(row.get("es_frontera")) if row.get("es_frontera") is not None else None

        year = _parametros_year_default()
        if es_frontera is True:
            smg = get_smg_for_year(year, "FRONTERA")
            exento = get_exento_he_for_year(year, "FRONTERA")
        else:
            smg = get_smg_for_year(year, "GENERAL")
            exento = get_exento_he_for_year(year, "GENERAL")

        editable_json = dict(row.get("editable_json") or {})
        comentario = (request.form.get("comentario") or "").strip()
        if comentario:
            historial = editable_json.setdefault("comentarios", [])
            historial.append({
                "by": int(g.user["id"]) if g.user is not None else None,
                "at": _now_iso(),
                "text": comentario,
            })
        editable_json["last_manual_edit_at"] = _now_iso()

        updates = {
            "numero_empleado": (request.form.get("numero_empleado") or "").strip() or None,
            "salario_operativo": _f("salario_operativo"),
            "valor_x_he": _f("valor_x_he"),
            "localidad": localidad or None,
            "localidad_normalizada": localidad_norm or None,
            "es_frontera": es_frontera,
            "salario_minimo_usado": _f("salario_minimo_usado") or (float(smg) if smg is not None else None),
            "exento_he_usado": _f("exento_he_usado") or (float(exento) if exento is not None else None),
            "editable_json": editable_json,
        }
        uid = int(g.user["id"]) if g.user is not None else None
        update_empleado_parametro(
            _db_path(),
            row_id,
            updates,
            updated_by=uid,
            updated_at=_now_iso(),
        )
        flash("Parámetros actualizados.", "success")
        return redirect(url_for("nomina.parametros_editar", row_id=row_id))

    return render_template("nomina/parametros_edit.html", row=row)


def register_nomina(app) -> None:
    app.register_blueprint(nomina_bp)

