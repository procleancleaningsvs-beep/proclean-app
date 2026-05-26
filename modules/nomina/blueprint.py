from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from difflib import SequenceMatcher
from functools import wraps
from io import BytesIO
from pathlib import Path
import unicodedata
from typing import Any

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from zoneinfo import ZoneInfo

from modules.nomina.asistencia_excel import build_asistencia_template_file
from modules.nomina.asistencia_metrics import compute_operative_metrics
from modules.nomina.asistencia_palette import css_vars_for_json
from modules.nomina.db import (
    get_asistencia_import,
    get_latest_import_base_rows,
    get_latest_import_base_rows_multi,
    list_asistencia_imports_master_hub,
    delete_asistencia_import,
    fetch_asistencia_original_file,
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
    update_vacaciones_empleado_calculo,
    save_vacaciones_events,
    list_vacaciones_eventos,
    create_vacaciones_backup,
    archive_vacaciones_import_empleados,
    list_vacaciones_empleados_all,
    validar_vacaciones_base,
    mark_vacaciones_event_reviewed,
    preview_limpieza_vacaciones_base,
    ejecutar_limpieza_base_vacaciones,
    archive_all_active_vacaciones_data,
    log_vacaciones_auditoria,
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
    list_asistencia_imports_for_calculo,
    insert_nomina_calculo_run,
    insert_nomina_calculo_rows_batch,
    get_nomina_calculo_run,
    list_nomina_calculo_rows,
    delete_nomina_calculo_rows,
    get_nomina_calculo_row,
    update_nomina_calculo_run,
    update_nomina_calculo_row_manual,
    recount_calculo_run_totals,
    nomina_calculo_dashboard_kpis,
    soft_delete_nomina_asistencia_import,
)
from modules.nomina.calc_service import (
    build_calculo_payload,
    index_overrides_from_calculo_rows,
    resync_row_totales,
)
from modules.nomina.validators import ValidationError, parse_and_validate_asistencia_excel
from modules.nomina.vacaciones_excel import parse_vacaciones_historico_excel
from modules.nomina.vacaciones_logic import (
    aplicar_calculo_a_fila,
    build_migration_events_from_row,
    calcular_balance_vacaciones_trabajador,
    detect_headcount_diff_warnings,
    resolve_fecha_corte,
)
from modules.nomina.vacaciones_util import (
    MATCH_AMBIGUO,
    MATCH_METHOD_NOMBRE,
    MATCH_METHOD_NSS,
    MATCH_METHOD_SIN_MATCH,
    MATCH_OK,
    PENDIENTE_REVISION,
    SIN_MATCH,
    enrich_vacaciones_row_for_display,
    resolve_status_headcount,
    sanitize_display_value,
)
from modules.nomina.headcount_bridge import obtener_headcount_completo
from modules.roles_access import normalized_role
from modules.nomina.infonavit_pdf import parse_infonavit_pdf
from modules.nomina.config import (
    get_exento_he_for_year,
    get_smg_for_year,
    get_umi_for_year,
)
from modules.nomina.parametros_excel import parse_nomina_actual
from modules.nomina.parametros_consolidado import (
    apply_manual_headcount_link,
    borrar_vinculos_manuales,
    build_consolidado_view,
    build_legacy_parametros_view,
    limpiar_importaciones_contpaq,
    limpiar_importaciones_nomina,
    preview_depuracion_parametros,
    rebuild_consolidado_parametros,
    search_active_headcount,
)
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

_NOMINA_ALLOWED_ROLES = {"admin", "nomina", "coordinador"}
_NOMINA_DASHBOARD_ROLES = {"admin", "nomina"}

nomina_bp = Blueprint(
    "nomina",
    __name__,
    url_prefix="/nomina",
    template_folder=str(_TEMPLATE_DIR),
)


@nomina_bp.before_request
def _nomina_module_access_guard() -> None:
    """Bloquea módulo Nóminas a roles no autorizados (no solo el menú)."""
    ep = str(getattr(request, "endpoint", "") or "")
    if not ep.startswith("nomina."):
        return
    if g.user is None:
        return
    r = normalized_role(g.user)
    if r not in _NOMINA_ALLOWED_ROLES:
        abort(403)


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


def _user_can_view_asistencia_import(imp: dict | None) -> bool:
    if imp is None or g.user is None:
        return False
    role = _current_role()
    if role in _NOMINA_DASHBOARD_ROLES:
        return True
    if role != "coordinador":
        return False
    try:
        uid = int(g.user.get("id"))
    except Exception:
        return False
    owner = imp.get("created_by")
    if owner is None:
        return False
    return int(owner) == uid


def _maybe_load_asistencia_import_for_hub(import_id: int | None) -> dict | None:
    if not import_id:
        return None
    imp = get_asistencia_import(_db_path(), import_id)
    if imp is None or not _user_can_view_asistencia_import(imp):
        return None
    imp["operative_metrics"] = compute_operative_metrics(imp.get("rows") or [])
    imp["has_original_file"] = fetch_asistencia_original_file(_db_path(), import_id) is not None
    return imp


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

def _normalize_planta_key(value: str) -> str:
    return " ".join(str(value or "").replace("\u00a0", " ").upper().split()).strip()


def _cliente_key_loose(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _nss_digits(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _cliente_label_for_import(clientes: list[str]) -> str:
    c = [str(x).strip() for x in clientes if str(x).strip()]
    if not c:
        return "NO_DETECTADO"
    if len(c) == 1:
        return c[0]
    return "MULTICLIENTE: " + " + ".join(c)


def _enrich_rows_with_headcount(
    rows: list[dict], db_path: str
) -> tuple[list[dict], str, int, list[str]]:
    """Match NSS → nombre → cliente+planta; Headcount como fuente de verdad.

    Cliente y trabajador se comparan con normalización robusta (mayúsculas, acentos, espacios).
    Si Headcount no está disponible (fallback interno), no se generan warnings masivos por fila.
    """
    hc_rows: list[dict] = []
    source = "headcount"
    try:
        hc_rows = list(obtener_headcount_completo())
    except Exception:
        source = "historial_fallback"
        for item in nomina_history_rows_for_headcount_fallback(db_path):
            hc_rows.append(
                {
                    "nss": item.get("nss") or "",
                    "nombre_completo": item.get("nombre_empleado") or "",
                    "cliente": item.get("cliente") or "",
                    "patron": "",
                    "puesto": "",
                    "status_operacion": "ALTA",
                    "status_imss": "NO_DISPONIBLE",
                }
            )

    hc_unavailable = source != "headcount"

    by_nss: dict[str, dict] = {}
    by_name: dict[str, list[dict]] = {}
    hc_client_norm_to_canonical: dict[str, str] = {}
    for item in hc_rows:
        cliente_txt = str(item.get("cliente") or "").strip()
        if cliente_txt:
            cn = _normalize_name(cliente_txt)
            if cn and cn not in hc_client_norm_to_canonical:
                hc_client_norm_to_canonical[cn] = cliente_txt
        nss_key = _nss_digits(str(item.get("nss") or ""))
        if len(nss_key) >= 8 and nss_key not in by_nss:
            by_nss[nss_key] = item
        nombre = _normalize_name(str(item.get("nombre_completo") or ""))
        if nombre:
            by_name.setdefault(nombre, []).append(item)

    hc_cliente_keys = set(hc_client_norm_to_canonical.keys())

    # Resolver cliente del archivo → clave normalizada de catálogo Headcount (exacta o fuzzy ≥ 0.88).
    cliente_file_norm_resolve: dict[str, str] = {}
    for nk in hc_cliente_keys:
        cliente_file_norm_resolve[nk] = nk

    clientes_en_archivo = sorted(
        {str(r.get("cliente") or "").strip() for r in rows if str(r.get("cliente") or "").strip()}
    )
    for c in clientes_en_archivo:
        fnk = _normalize_name(c)
        if not fnk or fnk in cliente_file_norm_resolve:
            continue
        if fnk in hc_cliente_keys:
            cliente_file_norm_resolve[fnk] = fnk
            continue
        best_hk: str | None = None
        best_r = 0.0
        for hk in hc_cliente_keys:
            r = SequenceMatcher(None, fnk, hk).ratio()
            if r > best_r:
                best_r = r
                best_hk = hk
        if best_hk is not None and best_r >= 0.88:
            cliente_file_norm_resolve[fnk] = best_hk

    clientes_fuera: list[str] = []
    seen_fuera: set[str] = set()
    for c in clientes_en_archivo:
        fnk = _normalize_name(c)
        if not fnk:
            continue
        if fnk not in cliente_file_norm_resolve:
            if fnk not in seen_fuera:
                seen_fuera.add(fnk)
                clientes_fuera.append(c)

    pending = 0
    for row in rows:
        warnings = list(row.get("warnings") or [])
        nombre_n = _normalize_name(str(row.get("nombre_empleado") or ""))
        file_nss = _nss_digits(str(row.get("nss") or ""))
        file_cliente_k = _normalize_name(str(row.get("cliente") or ""))
        resolved_cliente_k = cliente_file_norm_resolve.get(file_cliente_k, file_cliente_k)
        file_planta = _normalize_planta_key(str(row.get("planta") or ""))

        hc: dict | None = None
        match_status = "pending_review"
        score = 0.0

        if file_nss and file_nss in by_nss:
            hc = by_nss[file_nss]
            match_status = "exact_nss"
            score = 1.0
        else:
            candidates = by_name.get(nombre_n) or []
            if len(candidates) == 1:
                hc = candidates[0]
                match_status = "name_match"
                score = 0.86
            elif len(candidates) > 1:
                same_cliente = [
                    x
                    for x in candidates
                    if _normalize_name(str(x.get("cliente") or "")) == resolved_cliente_k
                    and resolved_cliente_k
                ]
                if len(same_cliente) == 1:
                    hc = same_cliente[0]
                    match_status = "probable_match"
                    score = 0.72
                    if not hc_unavailable:
                        warnings.append("Match por nombre y cliente; confirmar con NSS si aplica.")
                else:
                    same_cp = [
                        x
                        for x in candidates
                        if _normalize_name(str(x.get("cliente") or "")) == resolved_cliente_k
                        and _normalize_planta_key(str(x.get("patron") or "")) == file_planta
                        and file_planta
                    ]
                    if len(same_cp) == 1:
                        hc = same_cp[0]
                        match_status = "probable_match"
                        score = 0.68
                        if not hc_unavailable:
                            warnings.append("Match por nombre, cliente y planta (Headcount); revisar.")
                    else:
                        if not hc_unavailable:
                            warnings.append(
                                "Múltiples candidatos en Headcount por nombre; no se aplicó match automático."
                            )
            else:
                if hc_unavailable:
                    match_status = "pending_headcount_unavailable"
                    score = 0.0
                else:
                    match_status = "no_match_headcount"
                    warnings.append(
                        "Trabajador no encontrado en Headcount por nombre completo/NSS. Revisar manualmente."
                    )

        nss_out = file_nss or ""
        if hc is not None:
            nss_out = _nss_digits(str(hc.get("nss") or "")) or nss_out
            hc_cliente = str(hc.get("cliente") or "").strip()
            hc_planta = str(hc.get("patron") or "").strip()
            hc_puesto = str(hc.get("puesto") or "").strip()
            if (
                not hc_unavailable
                and file_cliente_k
                and hc_cliente
                and _normalize_name(hc_cliente) != file_cliente_k
            ):
                warnings.append(
                    f"Cliente en archivo difiere del cliente en Headcount ({hc_cliente})."
                )
            if not hc_unavailable and file_planta and hc_planta and _normalize_planta_key(hc_planta) != file_planta:
                warnings.append("Planta en archivo difiere del PATRON en Headcount.")
            if (
                not hc_unavailable
                and hc_puesto
                and str(row.get("puesto") or "").strip()
                and _normalize_planta_key(str(row.get("puesto") or "")) != _normalize_planta_key(hc_puesto)
            ):
                warnings.append("Puesto en archivo difiere del puesto en Headcount.")
            if not hc_unavailable and not _is_headcount_active(hc):
                warnings.append("Headcount indica trabajador inactivo o baja; revisar estatus.")
                match_status = "inactive_match"
                score = min(score, 0.55) if score > 0 else 0.55
        else:
            if hc is None and not (hc_unavailable and match_status == "pending_headcount_unavailable"):
                pending += 1

        row["warnings"] = warnings
        row["nss"] = nss_out
        row["headcount_match_status"] = match_status
        row["headcount_match_score"] = score
        row["headcount_source"] = source

    return rows, source, pending, clientes_fuera


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
        hc_rows, hc_err = _headcount_for_match()
        param_stats = get_parametros_stats(_db_path(), hc_rows if not hc_err else None)
        param_localidades = list_localidades_frontera(_db_path())
        calc_kpis = nomina_calculo_dashboard_kpis(_db_path())
        return render_template(
            "nomina/dashboard.html",
            dash=dash,
            vac_stats=vac_stats,
            inf_stats=inf_stats,
            param_stats=param_stats,
            param_localidades_count=len(param_localidades),
            param_localidades_frontera_count=sum(1 for it in param_localidades if it.get("es_frontera")),
            calc_kpis=calc_kpis,
            headcount_error=headcount_error,
            headcount_source=headcount_source,
        )
    import_id = request.args.get("import_id", type=int)
    imp = _maybe_load_asistencia_import_for_hub(import_id)
    if import_id and imp is None:
        flash("No se encontró la importación o no tienes permiso para verla.", "error")
    return render_template(
        "nomina/index.html",
        coordinador_display=_coordinador_display_name(),
        clientes=clientes,
        agrupaciones=agrupaciones,
        headcount_error=headcount_error,
        headcount_source=headcount_source,
        asistencia_history=list_asistencia_imports_master_hub(
            _db_path(),
            viewer_user_id=int(g.user["id"]) if g.user else None,
            role=_current_role(),
            limit=50,
        ),
        imp=imp,
        asistencia_key_styles=css_vars_for_json(),
    )


@nomina_bp.get("/master")
@_nomina_access_required
def master_hub():
    import_id = request.args.get("import_id", type=int)
    imp = _maybe_load_asistencia_import_for_hub(import_id)
    if import_id and imp is None:
        flash("No se encontró la importación o no tienes permiso para verla.", "error")
    clientes, agrupaciones, headcount_error, headcount_source = _available_clientes_headcount()
    return render_template(
        "nomina/index.html",
        coordinador_display=_coordinador_display_name(),
        clientes=clientes,
        agrupaciones=agrupaciones,
        headcount_error=headcount_error,
        headcount_source=headcount_source,
        asistencia_history=list_asistencia_imports_master_hub(
            _db_path(),
            viewer_user_id=int(g.user["id"]) if g.user else None,
            role=_current_role(),
            limit=50,
        ),
        imp=imp,
        asistencia_key_styles=css_vars_for_json(),
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
        all_clientes, _, _, _ = _available_clientes_headcount()
        if all_clientes:
            clientes = list(all_clientes)
            flash(
                "Sin clientes seleccionados: se usaron todos los clientes disponibles en Headcount para la plantilla.",
                "info",
            )
        else:
            flash(
                "Selecciona al menos un cliente o captura uno en opciones avanzadas (no hay lista desde Headcount).",
                "error",
            )
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
    parsed_rows, headcount_source, pending_matches, clientes_fuera = _enrich_rows_with_headcount(
        parsed_rows, _db_path()
    )
    clientes_detectados = sorted(
        {str(r.get("cliente") or "").strip() for r in parsed_rows if str(r.get("cliente") or "").strip()}
    )
    error_count = sum(len(r.get("errors") or []) for r in parsed_rows)
    warning_count = sum(len(r.get("warnings") or []) for r in parsed_rows)

    payload = {
        "semana": parsed.get("semana") or "",
        "fecha_inicio": fecha_inicio.isoformat(),
        "fecha_fin": fecha_fin.isoformat(),
        "cliente": _cliente_label_for_import(clientes_detectados),
        "coordinador": _coordinador_display_name(),
        "filename": filename,
        "original_filename": filename,
        "file_hash": hashlib.sha256(file_bytes).hexdigest(),
        "status": "draft",
        "total_rows": parsed.get("total_rows") or 0,
        "error_count": error_count,
        "warning_count": warning_count,
        "rows": parsed_rows,
        "clientes": clientes_detectados,
        "headcount_source": headcount_source,
        "original_file_blob": file_bytes,
        "raw_json": {
            "dias_headers": parsed.get("dias_headers") or [],
            "semana": parsed.get("semana") or "",
            "cliente": parsed.get("cliente") or "",
            "coordinador_archivo": parsed.get("coordinador") or "",
            "coordinador_session": _coordinador_display_name(),
            "clientes_detectados": clientes_detectados,
            "clientes_fuera_headcount": clientes_fuera,
            "headcount_source": headcount_source,
            "pending_headcount_matches": pending_matches,
        },
    }
    created_by = int(g.user["id"]) if g.user is not None else None
    import_id = save_asistencia_import(_db_path(), payload, created_by=created_by, now_iso=_now_iso())
    flash("Archivo importado y guardado como borrador.", "success")
    if clientes_fuera:
        flash(
            "Clientes en el archivo sin coincidencia en catálogo Headcount: "
            + ", ".join(clientes_fuera[:6])
            + ("…" if len(clientes_fuera) > 6 else ""),
            "warning",
        )
    return redirect(url_for("nomina.master_hub", import_id=import_id))


@nomina_bp.post("/dashboard/asistencia-import/<int:import_id>/archivar")
def dashboard_archivar_asistencia_import(import_id: int):
    """Soft delete desde Historial / Auditoría del dashboard. Solo admin (JSON)."""
    if g.user is None:
        return jsonify({"success": False, "message": "Sesión requerida."}), 401
    role = _current_role()
    if role not in _NOMINA_DASHBOARD_ROLES:
        return jsonify({"success": False, "message": "No autorizado."}), 403
    if role != "admin":
        return jsonify({"success": False, "message": "No autorizado."}), 403
    try:
        uid = int(g.user.get("id") or 0)
    except (TypeError, ValueError):
        uid = 0
    if uid <= 0:
        return jsonify({"success": False, "message": "Sesión inválida."}), 403
    ok = soft_delete_nomina_asistencia_import(
        _db_path(),
        import_id,
        deleted_by_user_id=uid,
        deleted_at_iso=_now_iso(),
    )
    if ok:
        return jsonify({"success": True, "message": "Registro eliminado correctamente."})
    return jsonify({"success": False, "message": "No se pudo eliminar el registro."}), 400


@nomina_bp.get("/imports/<int:import_id>")
@_nomina_access_required
def asistencia_importada(import_id: int):
    return redirect(url_for("nomina.master_hub", import_id=import_id))


@nomina_bp.get("/imports/<int:import_id>/archivo")
@_nomina_access_required
def descargar_asistencia_original(import_id: int):
    imp = get_asistencia_import(_db_path(), import_id)
    if imp is None or not _user_can_view_asistencia_import(imp):
        abort(404)
    got = fetch_asistencia_original_file(_db_path(), import_id)
    if got is None:
        flash("No hay archivo original almacenado para esta carga.", "warning")
        return redirect(url_for("nomina.master_hub", import_id=import_id))
    name, blob = got
    bio = BytesIO(blob)
    bio.seek(0)
    return send_file(
        bio,
        as_attachment=True,
        download_name=name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@nomina_bp.post("/imports/<int:import_id>/eliminar")
@_nomina_access_required
def eliminar_asistencia_import(import_id: int):
    if _current_role() != "admin":
        abort(403)
    if delete_asistencia_import(_db_path(), import_id):
        flash("Importación eliminada.", "success")
    else:
        flash("No se pudo eliminar la importación.", "error")
    return redirect(url_for("nomina.master_hub"))


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
        match_status = SIN_MATCH
        match_method = MATCH_METHOD_SIN_MATCH
        match_notes = ""
        hc_row: dict | None = None
        nss = sanitize_display_value(row.get("nss"))
        nombre_norm = _normalize_name(str(row.get("excel_nombre_original") or row.get("nombre_historico") or ""))

        if nss and nss in by_nss:
            hc_row = by_nss[nss]
            match_status = MATCH_OK
            match_method = MATCH_METHOD_NSS
            match_notes = "Match por NSS"
        else:
            candidates = by_name.get(nombre_norm) or []
            if len(candidates) == 1:
                hc_row = candidates[0]
                match_status = MATCH_OK
                match_method = MATCH_METHOD_NOMBRE
                match_notes = "Match exacto por nombre completo normalizado"
            elif len(candidates) > 1:
                match_status = MATCH_AMBIGUO
                match_method = MATCH_METHOD_NOMBRE
                match_notes = f"Múltiples candidatos en Headcount ({len(candidates)}) para el mismo nombre"
                row_warnings.append("Múltiples candidatos por nombre en Headcount; requiere revisión.")
                row["status_headcount"] = "SIN STATUS HEADCOUNT"
                row["estatus_headcount"] = "SIN STATUS HEADCOUNT"
                row["match_status"] = match_status
                row["match_method"] = match_method
                row["match_notes"] = match_notes
                row["match_score"] = None
                row["headcount_source"] = source
                row["headcount_raw_status"] = ""
                row["warnings"] = row_warnings
                warnings_total += len(row_warnings)
                continue

        if hc_row is None:
            row_warnings.append("Trabajador no encontrado en Headcount")
            row["status_headcount"] = "SIN STATUS HEADCOUNT"
            row["estatus_headcount"] = "SIN STATUS HEADCOUNT"
            row["match_status"] = SIN_MATCH
            row["match_method"] = MATCH_METHOD_SIN_MATCH
            row["match_notes"] = "Sin coincidencia por nombre completo en Headcount"
            row["match_score"] = None
            row["headcount_source"] = source
            row["headcount_raw_status"] = ""
            row["warnings"] = row_warnings
            warnings_total += len(row_warnings)
            continue

        matched += 1
        hc_nombre = sanitize_display_value(hc_row.get("nombre_completo"))
        row["headcount_nombre_original"] = hc_nombre
        row["headcount_nombre_normalizado"] = _normalize_name(hc_nombre)
        row["nombre_headcount"] = hc_nombre
        row["cliente"] = sanitize_display_value(hc_row.get("cliente")) or row.get("cliente")
        row["planta_headcount"] = sanitize_display_value(hc_row.get("patron") or hc_row.get("planta"))
        row["fecha_ingreso_headcount"] = _to_date_iso_headcount(hc_row.get("fecha_ingreso"))
        status_hc = resolve_status_headcount(hc_row)
        row["status_headcount"] = status_hc
        row["estatus_headcount"] = status_hc
        row["sueldo_headcount"] = hc_row.get("sueldo_diario")
        row["sueldo_usado"] = row["sueldo_headcount"] if row.get("sueldo_headcount") not in (None, "") else row.get("sueldo_historico")
        row["fecha_ingreso_usada"] = row.get("fecha_ingreso_headcount") or row.get("fecha_ingreso_historica")
        if not row.get("nss"):
            row["nss"] = sanitize_display_value(hc_row.get("nss"))
        if not _is_headcount_active(hc_row):
            row_warnings.append("Trabajador encontrado en Headcount con estatus inactivo/baja.")

        row["match_status"] = match_status
        row["match_method"] = match_method
        row["match_notes"] = match_notes
        row["match_score"] = None
        row["headcount_source"] = source
        row["headcount_raw_status"] = (
            f"{sanitize_display_value(hc_row.get('status_operacion'))}|{sanitize_display_value(hc_row.get('status_imss'))}"
        )

        hist_ord = _iso_to_ordinal(str(row.get("fecha_ingreso_historica") or ""))
        hc_ord = _iso_to_ordinal(str(row.get("fecha_ingreso_headcount") or ""))
        if hc_ord is not None:
            row["fecha_ingreso_usada"] = row.get("fecha_ingreso_headcount")
        if hist_ord is not None and hc_ord is not None and hist_ord != hc_ord:
            row_warnings.append("Fecha de ingreso del Excel difiere de Headcount")
            if hc_ord - hist_ord > 30:
                row_warnings.append("Posible reingreso / validar saldo histórico.")

        if source == "historial_fallback":
            row_warnings.append("Datos cruzados con historial interno (fallback), no con Headcount en vivo.")

        row_warnings.extend(detect_headcount_diff_warnings(row))
        row["warnings"] = row_warnings
        warnings_total += len(row_warnings)
    return rows, source, matched, warnings_total


def _to_date_iso_headcount(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s = str(value).strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    return s


def _vacaciones_fecha_corte_from_request() -> date:
    return resolve_fecha_corte(fecha_corte_ui=(request.args.get("fecha_corte") or request.form.get("fecha_corte") or "").strip() or None)


def _enrich_vacaciones_rows_post_match(rows: list[dict], fecha_corte: date) -> list[dict]:
    enriched: list[dict] = []
    for row in rows:
        enriched.append(aplicar_calculo_a_fila(row, fecha_corte=fecha_corte))
    return enriched


def _persist_vacaciones_events_for_import(
    db_path: str,
    import_id: int,
    *,
    source_filename: str,
    created_by: int | None,
    now_iso: str,
) -> int:
    saved_rows = list_vacaciones_empleados_all(db_path, import_id=import_id, include_inactive=False)
    all_events: list[dict[str, Any]] = []
    for row in saved_rows:
        row_events = build_migration_events_from_row(
            row,
            import_batch_id=import_id,
            imported_from_file=source_filename,
            created_at=now_iso,
        )
        for ev in row_events:
            ev["empleado_id"] = row.get("id")
        all_events.extend(row_events)
    return save_vacaciones_events(db_path, all_events, created_by=created_by)


def _recalcular_vacaciones_import(db_path: str, import_id: int, fecha_corte: date, now_iso: str) -> int:
    rows = list_vacaciones_empleados_all(db_path, import_id=import_id, include_inactive=False)
    count = 0
    for row in rows:
        calc_row = aplicar_calculo_a_fila(row, fecha_corte=fecha_corte)
        if update_vacaciones_empleado_calculo(db_path, int(row["id"]), calc_row, updated_at=now_iso):
            count += 1
    return count


def _admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if _current_role() != "admin":
            abort(403)
        return view(*args, **kwargs)

    return wrapped


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
    fecha_corte = _vacaciones_fecha_corte_from_request()
    is_admin = _current_role() == "admin"
    rows = [enrich_vacaciones_row_for_display(r) for r in rows]
    return render_template(
        "nomina/vacaciones_index.html",
        stats=stats,
        rows=rows,
        clientes=clientes,
        fecha_corte=fecha_corte.isoformat(),
        is_admin=is_admin,
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
    fecha_corte = _vacaciones_fecha_corte_from_request()
    rows = _enrich_vacaciones_rows_post_match(rows, fecha_corte)
    error_count = len(parsed.errors)
    if error_count > 0:
        flash("Se omitieron filas con error de estructura en importación histórica.", "warning")
    if not rows:
        flash("No se detectaron filas válidas para importar vacaciones.", "error")
        return redirect(url_for("nomina.vacaciones_index"))

    # Evitar duplicar eventos activos: archivar importaciones previas antes de cargar una nueva
    archived_prev = archive_all_active_vacaciones_data(_db_path())
    if any(archived_prev.values()):
        log_vacaciones_auditoria(
            _db_path(),
            action="archivo_pre_reimportacion",
            usuario_id=int(g.user["id"]) if g.user is not None else None,
            registros_afectados=sum(archived_prev.values()),
            backup_id=None,
            detalle={"archived": archived_prev, "motivo": "nueva_importacion_vacaciones"},
            now_iso=_now_iso(),
        )

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
            "weekly_events_total": parsed.weekly_events_total,
            "created_for": "nomina_vacaciones",
        },
    }
    uid = int(g.user["id"]) if g.user is not None else None
    now_iso = _now_iso()
    import_id = save_vacaciones_import(_db_path(), payload, created_by=uid, now_iso=now_iso)
    events_saved = _persist_vacaciones_events_for_import(
        _db_path(),
        import_id,
        source_filename=filename,
        created_by=uid,
        now_iso=now_iso,
    )
    sin_match = sum(1 for r in rows if r.get("match_status") == SIN_MATCH)
    flash(
        f"Importación procesada: {len(rows)} trabajadores, {matched_count} match OK, "
        f"{sin_match} sin match, {parsed.weekly_events_total} eventos semanales, "
        f"{events_saved} eventos guardados.",
        "success",
    )
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

        merged = dict(row)
        merged.update({
            "fecha_ingreso_usada": (request.form.get("fecha_ingreso_usada") or "").strip(),
            "sueldo_usado": _f("sueldo_usado"),
            "dias_utilizados": dias_utilizados,
            "vacaciones_laboradas": vacaciones_laboradas,
            "dias_pagados": dias_pagados,
            "dias_restantes_calculado": rest_calc,
            "dias_vacaciones_historico": dias_vac,
            "prima_2025_pagada": (request.form.get("prima_2025_pagada") or "") in {"1", "on", "true"},
            "prima_2026_pagada": (request.form.get("prima_2026_pagada") or "") in {"1", "on", "true"},
            "fecha_pago_prima_2026": (request.form.get("fecha_pago_prima_2026") or "").strip(),
            "comentarios": (request.form.get("comentarios") or "").strip(),
            "editable_json": {
                "revision_status": (request.form.get("revision_status") or "").strip() or "pending_revision",
            },
        })
        calc_row = aplicar_calculo_a_fila(merged, fecha_corte=_vacaciones_fecha_corte_from_request())
        updates = {
            "fecha_ingreso_usada": calc_row.get("fecha_ingreso_usada"),
            "sueldo_usado": calc_row.get("sueldo_usado"),
            "dias_utilizados": calc_row.get("dias_utilizados"),
            "vacaciones_laboradas": calc_row.get("vacaciones_laboradas"),
            "dias_pagados": calc_row.get("dias_pagados"),
            "dias_restantes_calculado": calc_row.get("dias_restantes_calculado"),
            "dias_generados": calc_row.get("dias_generados"),
            "saldo_calculado": calc_row.get("saldo_calculado"),
            "prima_pendiente": calc_row.get("prima_pendiente"),
            "dias_vacaciones_historico": calc_row.get("dias_vacaciones_historico"),
            "warnings": calc_row.get("warnings") or [],
            "prima_2025_pagada": merged.get("prima_2025_pagada"),
            "prima_2026_pagada": merged.get("prima_2026_pagada"),
            "fecha_pago_prima_2026": merged.get("fecha_pago_prima_2026"),
            "comentarios": merged.get("comentarios"),
            "editable_json": calc_row.get("editable_json") or merged.get("editable_json"),
            "monto_total_recalculado": calc_row.get("monto_total_recalculado"),
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

    return render_template("nomina/vacaciones_edit.html", row=row, calc=row.get("editable_json", {}).get("calc_json"))


@nomina_bp.get("/vacaciones/<int:row_id>/detalle")
@_nomina_dashboard_required
def vacaciones_detalle(row_id: int):
    row = get_vacaciones_empleado(_db_path(), row_id)
    if row is None:
        flash("Registro de vacaciones no encontrado.", "error")
        return redirect(url_for("nomina.vacaciones_index"))
    row = enrich_vacaciones_row_for_display(row)
    fecha_corte = _vacaciones_fecha_corte_from_request()
    calc = calcular_balance_vacaciones_trabajador(row, fecha_corte=fecha_corte)
    eventos = list_vacaciones_eventos(_db_path(), empleado_id=row_id, active_only=True)
    eventos_semanales = [e for e in eventos if e.get("event_type") == "vacaciones_tomadas"]
    return render_template(
        "nomina/vacaciones_detalle.html",
        row=row,
        calc=calc,
        eventos=eventos,
        eventos_semanales=eventos_semanales,
        fecha_corte=fecha_corte.isoformat(),
    )


@nomina_bp.post("/vacaciones/<int:row_id>/recalcular")
@_nomina_dashboard_required
def vacaciones_recalcular_trabajador(row_id: int):
    row = get_vacaciones_empleado(_db_path(), row_id)
    if row is None:
        flash("Registro no encontrado.", "error")
        return redirect(url_for("nomina.vacaciones_index"))
    fecha_corte = _vacaciones_fecha_corte_from_request()
    calc_row = aplicar_calculo_a_fila(row, fecha_corte=fecha_corte)
    update_vacaciones_empleado_calculo(_db_path(), row_id, calc_row, updated_at=_now_iso())
    flash("Saldo recalculado para el trabajador.", "success")
    return redirect(url_for("nomina.vacaciones_detalle", row_id=row_id, fecha_corte=fecha_corte.isoformat()))


@nomina_bp.post("/vacaciones/<int:row_id>/marcar-revisado")
@_nomina_dashboard_required
def vacaciones_marcar_revisado(row_id: int):
    row = get_vacaciones_empleado(_db_path(), row_id)
    if row is None:
        flash("Registro no encontrado.", "error")
        return redirect(url_for("nomina.vacaciones_index"))
    editable = dict(row.get("editable_json") or {})
    editable["revision_status"] = "revisado"
    update_vacaciones_empleado(
        _db_path(),
        row_id,
        {"editable_json": editable},
        updated_by=int(g.user["id"]) if g.user is not None else None,
        updated_at=_now_iso(),
    )
    flash("Registro marcado como revisado.", "success")
    return redirect(request.referrer or url_for("nomina.vacaciones_detalle", row_id=row_id))


@nomina_bp.get("/vacaciones/admin")
@_nomina_dashboard_required
@_admin_required
def vacaciones_admin():
    latest_import_id = get_latest_vacaciones_import_id(_db_path())
    import_id_raw = (request.args.get("import_id") or "").strip()
    selected_import_id = int(import_id_raw) if import_id_raw.isdigit() else latest_import_id
    resumen = validar_vacaciones_base(_db_path(), import_id=selected_import_id)
    preview_limpieza = preview_limpieza_vacaciones_base(_db_path())
    imports = list_vacaciones_imports(_db_path(), limit=100)
    return render_template(
        "nomina/vacaciones_admin.html",
        resumen=resumen,
        preview_limpieza=preview_limpieza,
        imports=imports,
        selected_import_id=selected_import_id,
    )


@nomina_bp.post("/vacaciones/admin/validar")
@_nomina_dashboard_required
@_admin_required
def vacaciones_admin_validar():
    import_id_raw = (request.form.get("import_id") or "").strip()
    import_id = int(import_id_raw) if import_id_raw.isdigit() else get_latest_vacaciones_import_id(_db_path())
    resumen = validar_vacaciones_base(_db_path(), import_id=import_id)
    flash(
        f"Validación: {resumen['total_registros']} registros, "
        f"{resumen['conflictos_match']} conflictos, {resumen['saldos_negativos']} saldos negativos.",
        "success",
    )
    return redirect(url_for("nomina.vacaciones_admin", import_id=import_id or ""))


@nomina_bp.post("/vacaciones/admin/recalcular")
@_nomina_dashboard_required
@_admin_required
def vacaciones_admin_recalcular():
    import_id_raw = (request.form.get("import_id") or "").strip()
    import_id = int(import_id_raw) if import_id_raw.isdigit() else get_latest_vacaciones_import_id(_db_path())
    if import_id is None:
        flash("No hay importación de vacaciones para recalcular.", "error")
        return redirect(url_for("nomina.vacaciones_admin"))
    fecha_corte = _vacaciones_fecha_corte_from_request()
    now_iso = _now_iso()
    count = _recalcular_vacaciones_import(_db_path(), int(import_id), fecha_corte, now_iso)
    flash(f"Recalculados {count} registros.", "success")
    return redirect(url_for("nomina.vacaciones_admin", import_id=import_id))


@nomina_bp.post("/vacaciones/admin/archivar")
@_nomina_dashboard_required
@_admin_required
def vacaciones_admin_archivar():
    import_id_raw = (request.form.get("import_id") or "").strip()
    latest = get_latest_vacaciones_import_id(_db_path())
    if import_id_raw == "older_than_latest" and latest is not None:
        archived = archive_vacaciones_import_empleados(_db_path(), import_id=None, exclude_import_id=int(latest))
        flash(f"Archivados {archived} registros de importaciones anteriores.", "success")
        return redirect(url_for("nomina.vacaciones_admin", import_id=latest))
    import_id = int(import_id_raw) if import_id_raw.isdigit() else None
    if import_id is None:
        flash("Selecciona una importación para archivar.", "error")
        return redirect(url_for("nomina.vacaciones_admin"))
    archived = archive_vacaciones_import_empleados(_db_path(), import_id=int(import_id), exclude_import_id=None)
    flash(f"Archivados {archived} registros (inactivos, no eliminados).", "success")
    return redirect(url_for("nomina.vacaciones_admin", import_id=import_id))


@nomina_bp.post("/vacaciones/admin/limpiar-base")
@_nomina_dashboard_required
@_admin_required
def vacaciones_admin_limpiar_base():
    confirmacion = (request.form.get("confirmacion") or "").strip()
    if confirmacion != "LIMPIAR VACACIONES":
        flash("Confirmación incorrecta. Debes escribir exactamente: LIMPIAR VACACIONES", "error")
        return redirect(url_for("nomina.vacaciones_admin"))
    uid = int(g.user["id"]) if g.user is not None else None
    now_iso = _now_iso()
    result = ejecutar_limpieza_base_vacaciones(_db_path(), created_by=uid, now_iso=now_iso)
    flash(
        f"Base limpiada. Backup #{result['backup_id']}; "
        f"archivados {result['archived']['empleados']} empleados, "
        f"{result['archived']['eventos']} eventos, "
        f"{result['archived']['importaciones']} importaciones.",
        "success",
    )
    return redirect(url_for("nomina.vacaciones_admin"))


@nomina_bp.post("/vacaciones/admin/confirmar-limpieza")
@_nomina_dashboard_required
@_admin_required
def vacaciones_admin_confirmar_limpieza():
    import_id_raw = (request.form.get("import_id") or "").strip()
    import_id = int(import_id_raw) if import_id_raw.isdigit() else get_latest_vacaciones_import_id(_db_path())
    if import_id is None:
        flash("No hay importación activa.", "error")
        return redirect(url_for("nomina.vacaciones_admin"))
    uid = int(g.user["id"]) if g.user is not None else None
    now_iso = _now_iso()
    rows = list_vacaciones_empleados_all(_db_path(), import_id=int(import_id), include_inactive=True)
    eventos = list_vacaciones_eventos(_db_path(), import_batch_id=int(import_id), active_only=False, limit=5000)
    backup_id = create_vacaciones_backup(
        _db_path(),
        backup_type="pre_limpieza",
        summary={"import_id": import_id, "rows": len(rows), "eventos": len(eventos)},
        payload={"empleados": rows, "eventos": eventos},
        created_by=uid,
        now_iso=now_iso,
    )
    archived_old = archive_vacaciones_import_empleados(_db_path(), import_id=None, exclude_import_id=int(import_id))
    fecha_corte = _vacaciones_fecha_corte_from_request()
    recalculated = _recalcular_vacaciones_import(_db_path(), int(import_id), fecha_corte, now_iso)
    flash(
        f"Limpieza confirmada. Backup #{backup_id}; archivados {archived_old} registros viejos; "
        f"recalculados {recalculated} registros vigentes.",
        "success",
    )
    return redirect(url_for("nomina.vacaciones_admin", import_id=import_id))


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
            "pending_headcount_unavailable",
            "no_match_contpaq",
            "probable_match",
            "multiple_candidates",
            "pending_review",
            "manual_link",
            "inactive_headcount",
        ]

    hc_rows, headcount_match_error = _headcount_for_match()
    if headcount_match_error:
        rows = build_legacy_parametros_view(
            db_path,
            cliente=cliente,
            match_status_any=match_filters or None,
            only_missing_salary=only_missing_salary,
            only_missing_valor_he=only_missing_he,
            limit=2000,
        )
        stats = get_parametros_stats(db_path, None)
    else:
        rows = build_consolidado_view(
            db_path,
            hc_rows,
            cliente=cliente,
            match_status_any=match_filters or None,
            only_missing_salary=only_missing_salary,
            only_missing_valor_he=only_missing_he,
            limit=2000,
        )
        stats = get_parametros_stats(db_path, hc_rows)
    imports = list_parametros_imports(db_path, limit=20)
    localidades = list_localidades_frontera(db_path)
    dep_preview = preview_depuracion_parametros(db_path)
    return render_template(
        "nomina/parametros_index.html",
        rows=rows,
        stats=stats,
        imports=imports,
        localidades=localidades,
        dep_preview=dep_preview,
        headcount_match_error=headcount_match_error,
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
    cliente_fallback = (request.form.get("cliente_fallback") or request.form.get("cliente") or "").strip()
    file_bytes = file.read()
    if not file_bytes:
        flash("Archivo vacío.", "error")
        return redirect(url_for("nomina.parametros_index"))
    if not file.filename.lower().endswith((".xlsx", ".xlsm")):
        flash("Solo se permiten archivos Excel (.xlsx / .xlsm).", "error")
        return redirect(url_for("nomina.parametros_index"))
    try:
        parsed = parse_nomina_actual(
            file_bytes,
            filename=file.filename,
            cliente_hint=cliente_fallback,
        )
    except ValueError as exc:
        flash(f"No se pudo importar: {exc}", "error")
        return redirect(url_for("nomina.parametros_index"))

    if parsed.get("cliente_requiere_seleccion") and not cliente_fallback:
        flash(
            "No se pudo detectar el cliente. Selecciona cliente para esta importación e intenta de nuevo.",
            "error",
        )
        return redirect(url_for("nomina.parametros_index"))

    db_path = _db_path()
    hc_rows, hc_err = _headcount_for_match()
    hc_index = build_headcount_index(hc_rows, unavailable_reason=hc_err)
    if hc_err:
        parsed["warnings"].append(f"No se pudo cargar Headcount: {hc_err}")

    # Save detected localidades first so subsequent rows benefit.
    if parsed.get("localidades"):
        _, _, loc_warnings = upsert_localidades_frontera(db_path, parsed["localidades"], now_iso=_now_iso())
        parsed["warnings"].extend(loc_warnings)

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
        if p["headcount_match_status"] in {"exact_nss", "exact_name", "manual_link"}:
            matched += 1
        payload_rows.append(p)

    file_hash = hashlib.sha256(file_bytes).hexdigest()
    import_payload = {
        "tipo_importacion": "NOMINA_ACTUAL",
        "cliente": parsed.get("cliente") or cliente_fallback,
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
            "headcount_unavailable": bool(hc_err),
            "headcount_error": hc_err,
            "cliente_detectado": parsed.get("cliente_detectado"),
            "cliente_detection_source": parsed.get("cliente_detection_source"),
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
        f"Match Headcount exacto: {matched}."
        + (
            " Headcount no disponible: revisar match pendiente (pending_headcount_unavailable)."
            if hc_err
            else ""
        ),
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
    hc_index = build_headcount_index(hc_rows, unavailable_reason=hc_err)
    if hc_err:
        parsed["warnings"].append(f"No se pudo cargar Headcount: {hc_err}")

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
            "headcount_unavailable": bool(hc_err),
            "headcount_error": hc_err,
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
        f"Match Headcount exacto: {matched}."
        + (
            " Headcount no disponible: revisar match pendiente (pending_headcount_unavailable)."
            if hc_err
            else ""
        ),
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


@nomina_bp.get("/parametros/depurar")
@_nomina_dashboard_required
def parametros_depurar_preview():
    preview = preview_depuracion_parametros(_db_path())
    return render_template("nomina/parametros_depurar.html", preview=preview)


@nomina_bp.post("/parametros/depurar")
@_nomina_dashboard_required
def parametros_depurar_ejecutar():
    action = (request.form.get("action") or "").strip()
    confirm = (request.form.get("confirm_text") or "").strip().upper()
    db_path = _db_path()
    now_iso = _now_iso()
    uid = int(g.user["id"]) if g.user is not None else None

    if action == "limpiar_nomina":
        result = limpiar_importaciones_nomina(db_path, now_iso=now_iso)
        flash(
            f"Importaciones de nómina limpiadas: {result['limpiados_campos_nomina']} registros afectados, "
            f"{result['desactivados_externos']} externos desactivados.",
            "success",
        )
    elif action == "limpiar_contpaq":
        result = limpiar_importaciones_contpaq(db_path, now_iso=now_iso)
        flash(
            f"Importaciones CONTPAQ limpiadas: {result['limpiados_campos_contpaq']} registros afectados, "
            f"{result['desactivados_externos']} externos desactivados.",
            "success",
        )
    elif action == "rebuild_consolidado":
        hc_rows, hc_err = _headcount_for_match()
        if hc_err:
            flash(f"No se puede reconstruir consolidado: Headcount no disponible ({hc_err}).", "error")
            return redirect(url_for("nomina.parametros_depurar_preview"))
        result = rebuild_consolidado_parametros(db_path, hc_rows, now_iso=now_iso)
        flash(
            f"Consolidado reconstruido desde Headcount activo ({result['activos_headcount']}): "
            f"{result['insertados']} nuevos, {result['actualizados']} actualizados.",
            "success",
        )
    elif action == "borrar_vinculos":
        if confirm not in {"DEPURAR", "BORRAR VINCULOS", "BORRAR VÍNCULOS"}:
            flash("Confirmación incorrecta. Escribe DEPURAR o BORRAR VINCULOS para borrar vínculos manuales.", "error")
            return redirect(url_for("nomina.parametros_depurar_preview"))
        count = borrar_vinculos_manuales(db_path, now_iso=now_iso)
        flash(f"Vínculos manuales eliminados: {count}.", "success")
    else:
        flash("Acción de depuración no reconocida.", "error")
    return redirect(url_for("nomina.parametros_index"))


@nomina_bp.route("/parametros/<int:row_id>/vincular", methods=["GET", "POST"])
@_nomina_dashboard_required
def parametros_vincular(row_id: int):
    row = get_empleado_parametro(_db_path(), row_id)
    if not row:
        abort(404)
    hc_rows, hc_err = _headcount_for_match()
    query = (request.args.get("q") or request.form.get("q") or "").strip()

    if request.method == "POST":
        hc_nss = (request.form.get("headcount_nss") or "").strip()
        if not hc_nss:
            flash("Selecciona un empleado activo de Headcount.", "error")
            return redirect(url_for("nomina.parametros_vincular", row_id=row_id, q=query))
        hc_match = next((h for h in hc_rows if str(h.get("nss") or "").strip() == hc_nss), None)
        if hc_match is None:
            flash("Empleado Headcount no encontrado.", "error")
            return redirect(url_for("nomina.parametros_vincular", row_id=row_id, q=query))
        uid = int(g.user["id"]) if g.user is not None else None
        ok = apply_manual_headcount_link(
            _db_path(),
            row_id,
            headcount_nss=hc_nss,
            headcount_nombre=str(hc_match.get("nombre_completo") or ""),
            headcount_cliente=str(hc_match.get("cliente") or ""),
            linked_by=uid,
            now_iso=_now_iso(),
        )
        if ok:
            flash("Vínculo manual guardado. El registro alimentará al empleado canónico de Headcount.", "success")
            return redirect(url_for("nomina.parametros_index", status="pendientes"))
        flash("No se pudo guardar el vínculo.", "error")

    candidates = search_active_headcount(hc_rows, query, limit=25) if not hc_err else []
    return render_template(
        "nomina/parametros_vincular.html",
        row=row,
        candidates=candidates,
        query=query,
        headcount_match_error=hc_err,
    )


def _calculo_config_from_form() -> dict[str, Any]:
    return {
        "domingo_opcion": (request.form.get("domingo_opcion") or "proporcional").strip(),
        "es_fin_de_mes": bool(request.form.get("es_fin_de_mes")),
        "permitir_negativo_isr": bool(request.form.get("permitir_negativo_isr")),
        "dias_tarifa_isr": request.form.get("dias_tarifa_isr") or 7,
        "dias_tarifa_subs": request.form.get("dias_tarifa_subs") or 7,
    }


def _calculo_cliente_label(clientes: list[str]) -> str:
    c = [str(x).strip() for x in clientes if str(x).strip()]
    if not c:
        return ""
    if len(c) == 1:
        return c[0]
    return "MULTICLIENTE"


@nomina_bp.get("/calculo")
@_nomina_dashboard_required
def calculo_index():
    imports_list = list_asistencia_imports_for_calculo(_db_path(), limit=100)
    clientes, agrupaciones, headcount_error, headcount_source = _available_clientes_headcount()
    return render_template(
        "nomina/calculo_index.html",
        imports_list=imports_list,
        clientes=clientes,
        agrupaciones=agrupaciones,
        headcount_error=headcount_error,
        headcount_source=headcount_source,
    )


@nomina_bp.post("/calculo/generar")
@_nomina_dashboard_required
def calculo_generar():
    db_path = _db_path()
    try:
        import_id = int(request.form.get("asistencia_import_id") or 0)
    except ValueError:
        import_id = 0
    if import_id <= 0:
        flash("Selecciona una importación de asistencia válida.", "error")
        return redirect(url_for("nomina.calculo_index"))
    clientes = _extract_selected_clientes_from_form()
    cfg = _calculo_config_from_form()
    try:
        payload = build_calculo_payload(
            db_path,
            asistencia_import_id=import_id,
            clientes_filter=clientes,
            config_form=cfg,
        )
    except ValueError as exc:
        flash(f"No se pudo generar el cálculo: {exc}", "error")
        return redirect(url_for("nomina.calculo_index"))
    uid = int(g.user["id"]) if g.user is not None else None
    now_iso = _now_iso()
    run_payload = {
        "asistencia_import_id": import_id,
        "cliente": _calculo_cliente_label(clientes),
        "clientes_json": clientes,
        "fecha_inicio": payload["fecha_inicio"],
        "fecha_fin": payload["fecha_fin"],
        "config_json": payload["config_json"],
        "status": "borrador",
        "total_empleados": payload["total_empleados"],
        "warning_count": payload["warning_count"],
        "block_count": payload["block_count"],
        "raw_json": payload["raw_json"],
    }
    calculo_id = insert_nomina_calculo_run(db_path, run_payload, created_by=uid, now_iso=now_iso)
    for r in payload["rows"]:
        r["updated_by"] = uid
        r["updated_at"] = now_iso
    insert_nomina_calculo_rows_batch(db_path, calculo_id, payload["rows"])
    recount_calculo_run_totals(db_path, calculo_id, now_iso=now_iso)
    flash("Cálculo preliminar guardado como borrador.", "success")
    return redirect(url_for("nomina.calculo_ver", calculo_id=calculo_id))


@nomina_bp.get("/calculo/<int:calculo_id>")
@_nomina_dashboard_required
def calculo_ver(calculo_id: int):
    run = get_nomina_calculo_run(_db_path(), calculo_id)
    if not run:
        abort(404)
    rows = list_nomina_calculo_rows(_db_path(), calculo_id)
    return render_template("nomina/calculo_view.html", run=run, rows=rows)


@nomina_bp.post("/calculo/<int:calculo_id>/recalcular")
@_nomina_dashboard_required
def calculo_recalcular(calculo_id: int):
    db_path = _db_path()
    run = get_nomina_calculo_run(db_path, calculo_id)
    if not run:
        abort(404)
    prev_rows = list_nomina_calculo_rows(db_path, calculo_id)
    prev = index_overrides_from_calculo_rows(prev_rows)
    import_id = int(run["asistencia_import_id"])
    clientes = [str(c).strip() for c in (run.get("clientes_json") or []) if str(c).strip()]
    cfg = _calculo_config_from_form()
    try:
        payload = build_calculo_payload(
            db_path,
            asistencia_import_id=import_id,
            clientes_filter=clientes,
            config_form=cfg,
            previous_overrides_by_asistencia_id=prev,
        )
    except ValueError as exc:
        flash(f"No se pudo recalcular: {exc}", "error")
        return redirect(url_for("nomina.calculo_ver", calculo_id=calculo_id))
    uid = int(g.user["id"]) if g.user is not None else None
    now_iso = _now_iso()
    delete_nomina_calculo_rows(db_path, calculo_id)
    for r in payload["rows"]:
        r["updated_by"] = uid
        r["updated_at"] = now_iso
    insert_nomina_calculo_rows_batch(db_path, calculo_id, payload["rows"])
    update_nomina_calculo_run(
        db_path,
        calculo_id,
        {
            "status": "recalculado",
            "total_empleados": payload["total_empleados"],
            "warning_count": payload["warning_count"],
            "block_count": payload["block_count"],
            "config_json": payload["config_json"],
            "raw_json": payload["raw_json"],
        },
        now_iso=now_iso,
    )
    recount_calculo_run_totals(db_path, calculo_id, now_iso=now_iso)
    flash("Borrador recalculado conservando ajustes manuales donde aplica.", "success")
    return redirect(url_for("nomina.calculo_ver", calculo_id=calculo_id))


@nomina_bp.post("/calculo/<int:calculo_id>/guardar-ajustes")
@_nomina_dashboard_required
def calculo_guardar_ajustes(calculo_id: int):
    db_path = _db_path()
    run = get_nomina_calculo_run(db_path, calculo_id)
    if not run:
        abort(404)
    uid = int(g.user["id"]) if g.user is not None else None
    now_iso = _now_iso()
    pat = re.compile(r"^r(\d+)_(.+)$")
    by_row: dict[int, dict[str, Any]] = {}
    for key, val in request.form.items():
        m = pat.match(key)
        if not m:
            continue
        rid = int(m.group(1))
        field = m.group(2)
        by_row.setdefault(rid, {})[field] = val
    for rid, fields in by_row.items():
        row_chk = get_nomina_calculo_row(db_path, rid)
        if not row_chk or int(row_chk.get("calculo_id") or 0) != int(calculo_id):
            continue
        conv: dict[str, Any] = {}
        for fk, fv in fields.items():
            if fk == "observaciones":
                conv[fk] = fv
                continue
            s = (fv or "").strip().replace(",", "")
            if not s:
                conv[fk] = None
                continue
            try:
                conv[fk] = float(s)
            except ValueError:
                conv[fk] = fv
        update_nomina_calculo_row_manual(db_path, rid, conv, updated_by=uid, now_iso=now_iso)
        resync_row_totales(db_path, rid)
    update_nomina_calculo_run(db_path, calculo_id, {"status": "revisado"}, now_iso=now_iso)
    recount_calculo_run_totals(db_path, calculo_id, now_iso=now_iso)
    flash("Ajustes manuales guardados.", "success")
    return redirect(url_for("nomina.calculo_ver", calculo_id=calculo_id))


def register_nomina(app) -> None:
    app.register_blueprint(nomina_bp)

