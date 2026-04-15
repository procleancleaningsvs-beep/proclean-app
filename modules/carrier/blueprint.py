"""
Rutas Flask: Carrier > Cursos.

La integración con Altas reutiliza la vista `nuevo_formato` y la sesión
`carrier_curso_return_expediente_id` (ver `modules.carrier.integration`).
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import fitz
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
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from modules.carrier.dates import now_in_app_tz, now_iso, today_in_app_tz
from modules.carrier.db import (
    attach_alta_format_history,
    clear_alta_link,
    delete_carrier_curso_export_log,
    ensure_carrier_tables,
    get_carrier_curso_export_log,
    get_expediente,
    get_monthly_base,
    insert_carrier_curso_export_log,
    insert_expediente,
    count_format_history_imss_list_like,
    list_carrier_curso_export_logs,
    list_format_history_imss_list_like_page,
    list_monthly_bases,
    parse_slots,
    dumps_slots,
    sync_expediente_nombre_desde_alta,
    update_expediente_constancia_modo,
    update_expediente_meta,
    update_expediente_slots_json,
    upsert_monthly_base,
)
from modules.carrier.export_naming import (
    curso_export_pdf_display_name,
    worker_name_from_payload_json,
)
from modules.carrier.inhabiles import load_inhabile_dates
from modules.carrier.integration import set_return_expediente_id
from modules.carrier.paquete_mes import (
    paquete_futuro_aun_no_utilizable,
    paquete_siguiente_payment_tuple,
    paquete_vigente_ym_str,
    paquete_vigente_payment_tuple,
    paquetes_utilizables_normal,
    tope_ultimo_mes_pago_utilizable,
    usuario_normal_puede_descargar_paquete,
    usuario_normal_puede_subir_paquete,
    ym_to_str as paquete_ym_to_str,
)
from modules.carrier.pdf_merge import write_merged_pdf
from services.app_activity import log_app_activity

_BASE = Path(__file__).resolve().parent.parent.parent
_TEMPLATE_DIR = _BASE / "templates" / "carrier"

carrier_curso_bp = Blueprint(
    "carrier_curso",
    __name__,
    url_prefix="/carrier/cursos",
    template_folder=str(_TEMPLATE_DIR),
)

FORMS_CURSO_URL = (
    "https://forms.office.com/pages/responsepage.aspx?id=ZZqDNj9_rEuepPVx8QqaA6-8XBB8zdZGm_WELtsNqV5URTVLTFg0MFU0UEs4QVU3VjRDUTE2TjNENS4u&route=shorturl"
)

CORE_FILE_SLOTS = ("curso_evidencia", "foto_persona", "ine_frente", "ine_reverso")
RENOVACION_SLOT = "renovacion_sua"
# Subidas permitidas (incluye extracto SUA en modo renovación).
ALL_UPLOAD_SLOTS = CORE_FILE_SLOTS + (RENOVACION_SLOT,)
ALLOWED_EXTENSIONS = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".webp"})

SLOT_LABELS = {
    "curso_evidencia": "Evidencia del curso / Forms",
    "foto_persona": "Foto de la persona",
    "ine_frente": "INE — primer archivo",
    "ine_reverso": "INE — segundo archivo (opcional)",
    RENOVACION_SLOT: "Renovación — extracto SUA (imagen o PDF)",
}

# Compatibilidad con nombres antiguos en rutas que iteran anexos estándar.
FILE_SLOTS = CORE_FILE_SLOTS

PENDIENTE_NOMBRE_ALTA = "(Pendiente de constancia IMSS)"


def _filter_bases_expediente(all_bases: list[Any], *, today_d: date) -> list[Any]:
    """Meses de paquete que existen en BD y no rebasan el tope operativo (mes calendario − 1)."""
    out: list[Any] = []
    for b in all_bases:
        if not _parse_year_month(b.year_month):
            continue
        if paquete_futuro_aun_no_utilizable(b.year_month, today_d):
            continue
        out.append(b)
    return sorted(out, key=lambda x: x.year_month, reverse=True)


def _bases_disponibles_expediente_usuario(
    db_path: str, all_bases: list[Any], *, today_d: date, inhabiles: set[date]
) -> list[Any]:
    """Usuario normal: solo meses de pago vigente/siguiente (regla día 17) con paquete cargado."""
    allowed = set(paquetes_utilizables_normal(today_d, inhabiles))
    out: list[Any] = []
    for b in all_bases:
        if b.year_month in allowed:
            out.append(b)
    return sorted(out, key=lambda x: x.year_month, reverse=True)


def _user_may_manage_mensual_ym(
    ym: str, *, is_admin: bool, today_d: date, inhabiles: set[date]
) -> bool:
    if not _parse_year_month(ym):
        return False
    if is_admin:
        return not paquete_futuro_aun_no_utilizable(ym, today_d)
    return usuario_normal_puede_subir_paquete(ym, today_d, inhabiles)


def _user_may_access_mensual_download(
    ym: str, _kind: str, *, is_admin: bool, today_d: date, inhabiles: set[date]
) -> bool:
    if not _parse_year_month(ym):
        return False
    if is_admin:
        return not paquete_futuro_aun_no_utilizable(ym, today_d)
    return usuario_normal_puede_descargar_paquete(ym, today_d, inhabiles)


def _expediente_mes_base_permitido(
    ym: str, *, is_admin: bool, today_d: date, inhabiles: set[date], db_path: str
) -> bool:
    if not _parse_year_month(ym) or not get_monthly_base(db_path, ym):
        return False
    if is_admin:
        return not paquete_futuro_aun_no_utilizable(ym, today_d)
    return ym in set(paquetes_utilizables_normal(today_d, inhabiles))


def _stale_expediente_vs_paquete_vigente(base_ym: str, today_d: date, inhabiles: set[date]) -> bool:
    """Aviso no bloqueante: el mes base del expediente es anterior al paquete vigente."""
    pm = _parse_ym_to_date(base_ym)
    if not pm:
        return False
    vy, vm = paquete_vigente_payment_tuple(today_d, inhabiles)
    return pm[0] * 12 + pm[1] < vy * 12 + vm

MES_LABEL_ES = {
    1: "Enero",
    2: "Febrero",
    3: "Marzo",
    4: "Abril",
    5: "Mayo",
    6: "Junio",
    7: "Julio",
    8: "Agosto",
    9: "Septiembre",
    10: "Octubre",
    11: "Noviembre",
    12: "Diciembre",
}


def _is_admin_carrier() -> bool:
    u = getattr(g, "user", None)
    if not u:
        return False
    try:
        return u["role"] == "admin"
    except (TypeError, KeyError, IndexError):
        return False


def _label_paquete_mes(ym: str) -> str:
    p = _parse_year_month(ym)
    if not p:
        return ym
    y, m = map(int, p.split("-", 1))
    return f"{MES_LABEL_ES.get(m, str(m))} {y}"


def _login_required():
    if g.user is None:
        return redirect(url_for("login"))
    return None


def _db_path() -> str:
    return str(current_app.config["DATABASE"])


def _instance_dir() -> Path:
    return Path(current_app.instance_path)


def _storage_root() -> Path:
    root = _instance_dir() / "carrier_storage"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _month_dir(ym: str) -> Path:
    p = _storage_root() / "mensual" / ym
    p.mkdir(parents=True, exist_ok=True)
    return p


def _exp_dir(eid: int) -> Path:
    p = _storage_root() / "expedientes" / str(eid)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _exports_dir() -> Path:
    p = _storage_root() / "exports"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ext_from_filename(name: str) -> str:
    lower = (name or "").lower().strip()
    if "." not in lower:
        return ""
    return Path(lower).suffix


def _validate_upload_ext(filename: str) -> str | None:
    ext = _ext_from_filename(filename)
    if ext not in ALLOWED_EXTENSIONS:
        return None
    return ext


def _parse_year_month(s: str) -> str | None:
    s = (s or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", s):
        return None
    y, m = map(int, s.split("-", 1))
    if y < 2000 or y > 2100 or m < 1 or m > 12:
        return None
    return f"{y:04d}-{m:02d}"


def _parse_ym_to_date(ym: str) -> tuple[int, int] | None:
    p = _parse_year_month(ym)
    if not p:
        return None
    y, m = map(int, p.split("-", 1))
    return y, m


def _get_format_history_row(db_path: str, record_id: int) -> sqlite3.Row | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT id, user_id, filename, pdf_path, payload_json FROM format_history WHERE id = ?",
            (record_id,),
        ).fetchone()
    finally:
        conn.close()


def _slot_meta(slots: dict[str, Any], slot: str) -> dict[str, Any]:
    raw = slots.get(slot)
    if isinstance(raw, dict):
        return raw
    return {}


def _nombres_movimientos_desde_payload(payload_json: str | None) -> list[str]:
    if not payload_json or not str(payload_json).strip():
        return []
    try:
        data = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for i, item in enumerate(data):
        if isinstance(item, dict):
            n = (item.get("nombre") or "").strip()
            out.append(n or f"Movimiento {i + 1}")
        else:
            out.append(f"Movimiento {i + 1}")
    return out


def _slot_to_source_tuple(
    meta: dict[str, Any],
) -> tuple[Any, ...] | None:
    rel = meta.get("rel")
    if not rel:
        return None
    path = _storage_root() / str(rel)
    ext = path.suffix.lower()
    pages = meta.get("pdf_pages")
    plist: list[int] | None
    if isinstance(pages, list) and ext == ".pdf":
        plist = []
        for x in pages:
            try:
                plist.append(int(x))
            except (TypeError, ValueError):
                continue
        if not plist:
            plist = None
    else:
        plist = None if ext == ".pdf" else None
    crop = meta.get("crop_norm")
    cr: tuple[float, float, float, float] | None
    if isinstance(crop, (list, tuple)) and len(crop) == 4:
        try:
            cr = (float(crop[0]), float(crop[1]), float(crop[2]), float(crop[3]))
        except (TypeError, ValueError):
            cr = None
    else:
        cr = None
    scale = 1.0
    rs = meta.get("render_scale")
    if rs is not None:
        try:
            scale = max(0.25, min(3.0, float(rs)))
        except (TypeError, ValueError):
            scale = 1.0
    kind = "pdf" if ext == ".pdf" else "image"
    if kind == "pdf":
        return ("pdf", path, plist, None, scale)
    return ("image", path, None, cr, scale)


def _merge_sources_for_expediente(
    *,
    monthly: Any,
    slots: dict[str, Any],
    constancia_modo: str,
    alta_path: Path | None,
    alta_pages: list[int] | None,
) -> list[tuple[Any, ...]]:
    """
    Orden fijo del expediente: mensual, evidencia, foto, INE (dos imágenes = una hoja),
    luego constancia IMSS (alta) o extracto SUA (renovación).
    """
    sources: list[tuple[Any, ...]] = []

    if monthly and monthly.sipare_relpath:
        p = _storage_root() / monthly.sipare_relpath
        sources.append(("pdf", p, None, None, 1.0))
    if monthly and monthly.pago_imss_relpath:
        p = _storage_root() / monthly.pago_imss_relpath
        sources.append(("pdf", p, None, None, 1.0))

    by_slot: dict[str, tuple[Any, ...]] = {}
    for slot in CORE_FILE_SLOTS:
        meta = _slot_meta(slots, slot)
        tup = _slot_to_source_tuple(meta)
        if tup:
            by_slot[slot] = tup

    for slot in ("curso_evidencia", "foto_persona"):
        if slot in by_slot:
            sources.append(by_slot[slot])

    lf = by_slot.get("ine_frente")
    lr = by_slot.get("ine_reverso")
    if lf and lr and lf[0] == "image" and lr[0] == "image":
        sources.append(("ine_duo", lf[1], lr[1], lf[3], lr[3]))
    else:
        for slot in ("ine_frente", "ine_reverso"):
            if slot in by_slot:
                sources.append(by_slot[slot])

    if constancia_modo == "renovacion":
        rt = _slot_to_source_tuple(_slot_meta(slots, RENOVACION_SLOT))
        if rt:
            sources.append(rt)
    elif alta_path and alta_path.exists():
        sources.append(("pdf", alta_path, alta_pages, None, 1.0))

    return sources


def _slot_editor_state(slots: dict[str, Any], expediente_id: int) -> dict[str, Any]:
    """Metadatos mínimos para vista previa/edición en cliente (sin rutas absolutas)."""
    out: dict[str, Any] = {}
    for slot in ALL_UPLOAD_SLOTS:
        meta = _slot_meta(slots, slot)
        if not meta.get("rel"):
            out[slot] = {"has_file": False}
            continue
        path = _storage_root() / str(meta["rel"])
        ext = path.suffix.lower() if path.exists() else ""
        cn = meta.get("crop_norm")
        rs_val: float | None = None
        rs = meta.get("render_scale")
        if rs is not None and str(rs).strip() != "":
            try:
                rs_val = max(0.25, min(3.0, float(rs)))
            except (TypeError, ValueError):
                rs_val = None
        out[slot] = {
            "has_file": True,
            "orig": str(meta.get("orig") or ""),
            "ext": ext,
            "crop_norm": cn
            if isinstance(cn, (list, tuple)) and len(cn) == 4
            else None,
            "render_scale": rs_val,
            "preview_url": url_for(
                "carrier_curso.expediente_preview_slot",
                expediente_id=expediente_id,
                slot=slot,
            ),
        }
    return out


def _workspace_context(
    db_path: str,
    row: Any,
    *,
    today_d: date,
    inhabiles: set[date],
    is_admin: bool,
) -> dict[str, Any]:
    """Variables de plantilla para el área de trabajo del expediente (pantalla única Cursos)."""
    slots = parse_slots(row.slots_json)
    monthly = get_monthly_base(db_path, row.base_year_month)
    stale = _stale_expediente_vs_paquete_vigente(row.base_year_month, today_d, inhabiles)

    alta_info: dict[str, Any] | None = None
    alta_pdf_page_count = 0
    if row.alta_format_history_id:
        hr = _get_format_history_row(db_path, int(row.alta_format_history_id))
        if hr and int(hr["user_id"]) == int(row.user_id):
            ap = Path(str(hr["pdf_path"]))
            alta_info = {
                "id": int(hr["id"]),
                "filename": str(hr["filename"]),
                "exists": ap.exists(),
            }
            if ap.suffix.lower() == ".pdf" and ap.is_file():
                try:
                    doc = fitz.open(str(ap))
                    try:
                        alta_pdf_page_count = doc.page_count
                    finally:
                        doc.close()
                except Exception:
                    alta_pdf_page_count = 0

    modo = getattr(row, "constancia_modo", "alta") or "alta"
    if modo not in {"alta", "renovacion"}:
        modo = "alta"

    pdf_page_counts: dict[str, int] = {}
    for slot in ALL_UPLOAD_SLOTS:
        if modo != "renovacion" and slot == RENOVACION_SLOT:
            continue
        meta = _slot_meta(slots, slot)
        rel = meta.get("rel")
        if not rel:
            continue
        path = _storage_root() / str(rel)
        if path.suffix.lower() == ".pdf" and path.exists():
            try:
                doc = fitz.open(str(path))
                try:
                    pdf_page_counts[slot] = doc.page_count
                finally:
                    doc.close()
            except Exception:
                pdf_page_counts[slot] = 0

    show_alta_formulario = (request.args.get("alta_form") or "").strip() == "1"

    dt = now_in_app_tz()
    stem = url_for(
        "carrier_curso.expediente_upload_slot",
        expediente_id=int(row.id),
        slot="curso_evidencia",
    )
    upload_prefix = stem.rsplit("/", 1)[0] + "/"
    return {
        "expediente": row,
        "slots": slots,
        "monthly": monthly,
        "stale_month_warning": stale,
        "alta_info": alta_info,
        "pdf_page_counts": pdf_page_counts,
        "alta_pdf_page_count": alta_pdf_page_count,
        "lock_mes_base": not is_admin,
        "movement_current_time": dt.strftime("%H:%M"),
        "movement_current_date": dt.strftime("%Y-%m-%d"),
        "upload_prefix": upload_prefix,
        "constancia_modo": modo,
        "alta_movimiento_idx": int(getattr(row, "alta_movimiento_idx", 0) or 0),
        "slot_editor_state": _slot_editor_state(slots, int(row.id)),
        "show_alta_formulario": show_alta_formulario,
    }


@carrier_curso_bp.app_context_processor
def _inject_forms_url():
    return {"carrier_forms_curso_url": FORMS_CURSO_URL}


@carrier_curso_bp.route("/")
def index():
    if (redir := _login_required()) is not None:
        return redir
    db_path = _db_path()
    all_bases = list_monthly_bases(db_path)
    inhabiles = load_inhabile_dates(_instance_dir())
    today_d = today_in_app_tz()
    is_admin = _is_admin_carrier()
    uid = int(g.user["id"])

    operational_ym = paquete_vigente_ym_str(today_d, inhabiles)
    operational_label = _label_paquete_mes(operational_ym)
    operational_base = get_monthly_base(db_path, operational_ym)

    vig_t = paquete_vigente_payment_tuple(today_d, inhabiles)
    sig_t = paquete_siguiente_payment_tuple(vig_t, today_d, inhabiles)
    siguiente_ym = paquete_ym_to_str(sig_t[0], sig_t[1]) if sig_t else None
    siguiente_label = _label_paquete_mes(siguiente_ym) if siguiente_ym else ""
    cap_t = tope_ultimo_mes_pago_utilizable(today_d)
    cap_ym = paquete_ym_to_str(cap_t[0], cap_t[1])

    active_ym = ""
    active_base = None
    stale_warning = False

    if is_admin:
        active_ym = (request.args.get("mes") or "").strip()
        if not active_ym:
            active_ym = operational_ym
        if not _parse_year_month(active_ym) and all_bases:
            active_ym = all_bases[0].year_month
        if _parse_year_month(active_ym):
            active_base = get_monthly_base(db_path, active_ym)
            stale_warning = active_ym != operational_ym
    else:
        active_ym = operational_ym
        if _parse_year_month(active_ym):
            active_base = operational_base

    if request.args.get("nuevo") == "1":
        vig = operational_ym
        if not get_monthly_base(db_path, vig):
            flash(
                "Primero debe existir el paquete mensual del mes de pago vigente para abrir un expediente.",
                "error",
            )
            return redirect(url_for("carrier_curso.index", mes=active_ym) if is_admin else url_for("carrier_curso.index"))
        if not _expediente_mes_base_permitido(
            vig, is_admin=is_admin, today_d=today_d, inhabiles=inhabiles, db_path=db_path
        ):
            flash("No se puede crear un expediente para ese mes base.", "error")
            return redirect(url_for("carrier_curso.index", mes=active_ym) if is_admin else url_for("carrier_curso.index"))
        ts = now_iso()
        eid = insert_expediente(
            db_path,
            user_id=uid,
            nombre_persona=PENDIENTE_NOMBRE_ALTA,
            base_year_month=vig,
            created_at=ts,
            updated_at=ts,
        )
        flash(
            "Expediente abierto. El nombre se completará al generar la constancia IMSS (Alta). "
            "Puedes cargar anexos y exportar desde esta misma pantalla.",
            "success",
        )
        q: dict[str, Any] = {"e": eid}
        if is_admin and active_ym:
            q["mes"] = active_ym
        return redirect(url_for("carrier_curso.index", **q))

    workspace: dict[str, Any] | None = None
    e_arg = request.args.get("e", type=int)
    if e_arg is not None:
        row = get_expediente(db_path, e_arg)
        if not row or int(row.user_id) != uid:
            flash("Expediente no encontrado.", "error")
            return redirect(url_for("carrier_curso.index", mes=active_ym) if is_admin else url_for("carrier_curso.index"))
        set_return_expediente_id(session, e_arg)
        workspace = _workspace_context(db_path, row, today_d=today_d, inhabiles=inhabiles, is_admin=is_admin)

    bases_filtradas = _filter_bases_expediente(all_bases, today_d=today_d)

    y0 = today_d.year
    year_options = list(range(y0 - 2, y0 + 4))

    return render_template(
        "cursos_index.html",
        is_admin=is_admin,
        all_bases=bases_filtradas if is_admin else None,
        active_ym=active_ym,
        active_base=active_base,
        operational_ym=operational_ym,
        operational_label=operational_label,
        operational_base=operational_base,
        siguiente_ym=siguiente_ym,
        siguiente_label=siguiente_label,
        cap_ym=cap_ym,
        active_label=_label_paquete_mes(active_ym) if active_ym else "",
        stale_warning=stale_warning,
        inhabiles_path=str(_instance_dir() / "carrier_inhabiles.json"),
        mes_labels=MES_LABEL_ES,
        year_options=year_options,
        workspace=workspace,
        slot_labels=SLOT_LABELS,
        pendiente_nombre_alta=PENDIENTE_NOMBRE_ALTA,
    )


@carrier_curso_bp.route("/mensual", methods=["POST"])
def mensual_upload():
    if (redir := _login_required()) is not None:
        return redir
    today_d = today_in_app_tz()
    inhabiles = load_inhabile_dates(_instance_dir())
    is_admin = _is_admin_carrier()

    ym = _parse_year_month(request.form.get("year_month") or "")
    if not ym:
        flash("Mes base inválido. Usa formato AAAA-MM.", "error")
        return redirect(url_for("carrier_curso.index"))

    if not _user_may_manage_mensual_ym(ym, is_admin=is_admin, today_d=today_d, inhabiles=inhabiles):
        flash("No tienes permiso para cargar o editar el paquete de ese mes.", "error")
        return redirect(url_for("carrier_curso.index"))

    row_existing = get_monthly_base(_db_path(), ym)
    sipare_rel = row_existing.sipare_relpath if row_existing else None
    sipare_orig = row_existing.sipare_orig_name if row_existing else None
    pago_rel = row_existing.pago_imss_relpath if row_existing else None
    pago_orig = row_existing.pago_imss_orig_name if row_existing else None

    f_sipare = request.files.get("sipare")
    f_pago = request.files.get("pago_imss")
    has_new_sip = bool(f_sipare and f_sipare.filename)
    has_new_pago = bool(f_pago and f_pago.filename)
    if not has_new_sip and not has_new_pago:
        if not row_existing:
            flash("Sube al menos un archivo (SIPARE / cédula o Pago IMSS).", "error")
            return redirect(url_for("carrier_curso.index", mes=ym) if is_admin else url_for("carrier_curso.index"))
        flash("Sin archivos nuevos; el paquete no cambió.", "success")
        return redirect(url_for("carrier_curso.index", mes=ym) if is_admin else url_for("carrier_curso.index"))

    if not row_existing and (not has_new_sip or not has_new_pago):
        flash("El paquete mensual nuevo debe incluir SIPARE/cédula y Pago IMSS (ambos archivos).", "error")
        return redirect(url_for("carrier_curso.index", mes=ym) if is_admin else url_for("carrier_curso.index"))

    if has_new_sip:
        ext = _validate_upload_ext(f_sipare.filename)
        if not ext:
            flash("SIPARE / cédula: extensión no permitida.", "error")
            return redirect(url_for("carrier_curso.index", mes=ym))
        fn = secure_filename(f"sipare{ext}")
        dest = _month_dir(ym) / fn
        f_sipare.save(str(dest))
        sipare_rel = str(dest.relative_to(_storage_root()))
        sipare_orig = f_sipare.filename

    if has_new_pago:
        ext = _validate_upload_ext(f_pago.filename)
        if not ext:
            flash("Pago IMSS: extensión no permitida.", "error")
            return redirect(url_for("carrier_curso.index", mes=ym))
        fn = secure_filename(f"pago_imss{ext}")
        dest = _month_dir(ym) / fn
        f_pago.save(str(dest))
        pago_rel = str(dest.relative_to(_storage_root()))
        pago_orig = f_pago.filename

    upsert_monthly_base(
        _db_path(),
        ym,
        sipare_relpath=sipare_rel,
        sipare_orig_name=sipare_orig,
        pago_imss_relpath=pago_rel,
        pago_imss_orig_name=pago_orig,
        updated_at=now_iso(),
        updated_by=int(g.user["id"]),
    )
    flash("Paquete mensual base guardado.", "success")
    return redirect(url_for("carrier_curso.index", mes=ym) if is_admin else url_for("carrier_curso.index"))


@carrier_curso_bp.route("/mensual/descargar")
def mensual_descargar():
    """Descarga SIPARE o Pago IMSS del paquete mensual (permisos según rol)."""
    if (redir := _login_required()) is not None:
        return redir
    kind = (request.args.get("kind") or "").strip().lower()
    ym = _parse_year_month(request.args.get("ym") or "")
    if kind not in {"sipare", "pago"} or not ym:
        abort(400)
    today_d = today_in_app_tz()
    inhabiles = load_inhabile_dates(_instance_dir())
    is_admin = _is_admin_carrier()
    if not _user_may_access_mensual_download(ym, kind, is_admin=is_admin, today_d=today_d, inhabiles=inhabiles):
        abort(403)

    row = get_monthly_base(_db_path(), ym)
    if not row:
        abort(404)
    rel = row.sipare_relpath if kind == "sipare" else row.pago_imss_relpath
    orig = row.sipare_orig_name if kind == "sipare" else row.pago_imss_orig_name
    if not rel:
        abort(404)
    path = _storage_root() / str(rel)
    if not path.is_file():
        abort(404)
    dl = orig or path.name
    inline = request.args.get("inline") == "1"
    mt = None
    suf = path.suffix.lower()
    if inline:
        if suf == ".pdf":
            mt = "application/pdf"
        elif suf in {".png", ".jpg", ".jpeg", ".webp"}:
            mt = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}[suf]
    return send_file(path, as_attachment=not inline, download_name=dl, mimetype=mt)


@carrier_curso_bp.route("/expediente/nuevo", methods=["GET", "POST"])
def expediente_nuevo():
    """Compatibilidad: el alta de expediente vive en la pantalla principal (`/?nuevo=1`)."""
    if (redir := _login_required()) is not None:
        return redir
    mes = (request.args.get("mes") or "").strip()
    if mes:
        return redirect(url_for("carrier_curso.index", nuevo=1, mes=mes))
    return redirect(url_for("carrier_curso.index", nuevo=1))


@carrier_curso_bp.route("/expediente/<int:expediente_id>", methods=["GET", "POST"])
def expediente_edit(expediente_id: int):
    if (redir := _login_required()) is not None:
        return redir
    db_path = _db_path()
    row = get_expediente(db_path, expediente_id)
    if not row or int(row.user_id) != int(g.user["id"]):
        abort(404)

    if request.method == "GET":
        return redirect(url_for("carrier_curso.index", e=expediente_id))

    inhabiles = load_inhabile_dates(_instance_dir())
    today_d = today_in_app_tz()
    is_ad = _is_admin_carrier()

    action = (request.form.get("action") or "").strip()
    if action == "save_meta":
        if not is_ad:
            flash(
                "El nombre del trabajador se toma de la constancia IMSS (Alta). "
                "Solo un administrador puede ajustar el mes base manualmente.",
                "info",
            )
            return redirect(url_for("carrier_curso.index", e=expediente_id))
        nombre = (request.form.get("nombre_persona") or "").strip()
        if not nombre:
            flash("El nombre es obligatorio.", "error")
            return redirect(url_for("carrier_curso.index", e=expediente_id))
        ym = _parse_year_month(request.form.get("base_year_month") or "")
        if ym and get_monthly_base(db_path, ym) and _expediente_mes_base_permitido(
            ym, is_admin=True, today_d=today_d, inhabiles=inhabiles, db_path=db_path
        ):
            update_expediente_meta(
                db_path,
                expediente_id,
                nombre_persona=nombre,
                base_year_month=ym,
                updated_at=now_iso(),
            )
            flash("Datos del expediente guardados.", "success")
        else:
            flash("No se pudo guardar: revisa mes base y que exista paquete permitido para ese mes.", "error")
        return redirect(url_for("carrier_curso.index", e=expediente_id))
    if action == "clear_alta":
        if clear_alta_link(db_path, expediente_id, int(g.user["id"]), now_iso()):
            flash("Se quitó el vínculo con la constancia IMSS.", "success")
        return redirect(url_for("carrier_curso.index", e=expediente_id))

    return redirect(url_for("carrier_curso.index", e=expediente_id))


@carrier_curso_bp.route("/expediente/<int:expediente_id>/upload/<slot>", methods=["POST"])
def expediente_upload_slot(expediente_id: int, slot: str):
    if (redir := _login_required()) is not None:
        return redir
    if slot not in ALL_UPLOAD_SLOTS:
        abort(404)
    db_path = _db_path()
    row = get_expediente(db_path, expediente_id)
    if not row or int(row.user_id) != int(g.user["id"]):
        abort(404)
    modo = getattr(row, "constancia_modo", "alta") or "alta"
    if slot == RENOVACION_SLOT and modo != "renovacion":
        flash("El extracto SUA solo aplica en modo Renovación.", "error")
        return redirect(url_for("carrier_curso.index", e=expediente_id))

    f = request.files.get("file")
    if not f or not f.filename:
        flash("Selecciona un archivo.", "error")
        return redirect(url_for("carrier_curso.index", e=expediente_id))

    ext = _validate_upload_ext(f.filename)
    if not ext:
        flash("Tipo de archivo no permitido. Usa PDF, PNG, JPG, JPEG o WEBP.", "error")
        return redirect(url_for("carrier_curso.index", e=expediente_id))

    dest_dir = _exp_dir(expediente_id)
    safe = secure_filename(f"{slot}{ext}")
    dest = dest_dir / safe
    f.save(str(dest))

    slots = parse_slots(row.slots_json)
    prev = _slot_meta(slots, slot)
    old_rel = prev.get("rel")
    if old_rel:
        old_path = _storage_root() / str(old_rel)
        if old_path.exists() and old_path.resolve() != dest.resolve():
            try:
                old_path.unlink()
            except OSError:
                pass

    rel = str(dest.relative_to(_storage_root()))
    crop_norm: list[float] | None = None
    cj = (request.form.get("crop_norm_json") or "").strip()
    if cj:
        try:
            arr = json.loads(cj)
            if isinstance(arr, list) and len(arr) == 4:
                crop_norm = [float(arr[0]), float(arr[1]), float(arr[2]), float(arr[3])]
        except (json.JSONDecodeError, TypeError, ValueError):
            crop_norm = None
    render_scale: float | None = None
    rs = (request.form.get("render_scale") or "").strip()
    if rs:
        try:
            render_scale = max(0.25, min(3.0, float(rs)))
        except (TypeError, ValueError):
            render_scale = None
    slots[slot] = {
        "rel": rel,
        "orig": f.filename,
        "pdf_pages": None,
        "crop_norm": crop_norm,
        "render_scale": render_scale,
    }
    update_expediente_slots_json(db_path, expediente_id, dumps_slots(slots), now_iso())
    flash(f"Archivo cargado: {SLOT_LABELS.get(slot, slot)}.", "success")
    return redirect(url_for("carrier_curso.index", e=expediente_id))


@carrier_curso_bp.route("/expediente/<int:expediente_id>/slot-meta", methods=["POST"])
def expediente_slot_meta(expediente_id: int):
    if (redir := _login_required()) is not None:
        return redir
    xhr = (request.headers.get("X-Carrier-Xhr") or "").strip() == "1"
    db_path = _db_path()
    row = get_expediente(db_path, expediente_id)
    if not row or int(row.user_id) != int(g.user["id"]):
        abort(404)

    slot = (request.form.get("slot") or "").strip()
    if slot not in ALL_UPLOAD_SLOTS:
        if xhr:
            return jsonify({"ok": False, "error": "Apartado no válido."}), 400
        abort(400)

    slots = parse_slots(row.slots_json)
    meta = _slot_meta(slots, slot)
    if not meta.get("rel"):
        msg = "Primero sube un archivo para este apartado."
        if xhr:
            return jsonify({"ok": False, "error": msg}), 400
        flash(msg, "error")
        return redirect(url_for("carrier_curso.index", e=expediente_id))

    path = _storage_root() / str(meta["rel"])
    if path.suffix.lower() == ".pdf":
        raw_pages = request.form.getlist("pdf_pages")
        pages: list[int] = []
        for x in raw_pages:
            try:
                pages.append(int(x))
            except (TypeError, ValueError):
                continue
        if pages:
            meta["pdf_pages"] = sorted(set(pages))
        else:
            meta["pdf_pages"] = None
    else:
        cj = (request.form.get("crop_norm_json") or "").strip()
        if cj:
            try:
                arr = json.loads(cj)
                if isinstance(arr, list) and len(arr) == 4:
                    meta["crop_norm"] = [float(arr[0]), float(arr[1]), float(arr[2]), float(arr[3])]
                else:
                    meta["crop_norm"] = None
            except (json.JSONDecodeError, TypeError, ValueError):
                meta["crop_norm"] = None
        else:
            xs = (request.form.get("crop_x") or "").strip()
            ys = (request.form.get("crop_y") or "").strip()
            ws = (request.form.get("crop_w") or "").strip()
            hs = (request.form.get("crop_h") or "").strip()
            if xs and ys and ws and hs:
                try:
                    x, y, w, h = float(xs), float(ys), float(ws), float(hs)
                    meta["crop_norm"] = [x, y, x + w, y + h]
                except ValueError:
                    meta["crop_norm"] = None
            else:
                meta["crop_norm"] = None

    if "render_scale" in request.form:
        rsv = (request.form.get("render_scale") or "").strip()
        if not rsv:
            meta.pop("render_scale", None)
        else:
            try:
                meta["render_scale"] = max(0.25, min(3.0, float(rsv)))
            except (TypeError, ValueError):
                pass

    slots[slot] = meta
    update_expediente_slots_json(db_path, expediente_id, dumps_slots(slots), now_iso())
    if xhr:
        return jsonify({"ok": True})
    flash("Opciones de recorte / páginas guardadas (opcional).", "success")
    return redirect(url_for("carrier_curso.index", e=expediente_id))


@carrier_curso_bp.route("/expediente/<int:expediente_id>/constancia-modo", methods=["POST"])
def expediente_constancia_modo(expediente_id: int):
    if (redir := _login_required()) is not None:
        return redir
    db_path = _db_path()
    row = get_expediente(db_path, expediente_id)
    if not row or int(row.user_id) != int(g.user["id"]):
        abort(404)
    modo = (request.form.get("constancia_modo") or "").strip().lower()
    if modo not in {"alta", "renovacion"}:
        flash("Modo de constancia no válido.", "error")
        return redirect(url_for("carrier_curso.index", e=expediente_id))
    if update_expediente_constancia_modo(
        db_path, expediente_id, int(g.user["id"]), modo, now_iso()
    ):
        flash("Modo de constancia actualizado.", "success")
    return redirect(url_for("carrier_curso.index", e=expediente_id))


@carrier_curso_bp.route("/expediente/<int:expediente_id>/vincular-formato", methods=["POST"])
def expediente_vincular_formato(expediente_id: int):
    if (redir := _login_required()) is not None:
        return redir
    db_path = _db_path()
    row = get_expediente(db_path, expediente_id)
    if not row or int(row.user_id) != int(g.user["id"]):
        abort(404)
    try:
        fh_id = int((request.form.get("format_history_id") or "").strip())
    except ValueError:
        flash("Identificador de constancia no válido.", "error")
        return redirect(url_for("carrier_curso.index", e=expediente_id))
    try:
        mov_idx = int((request.form.get("movimiento_idx") or "0").strip())
    except ValueError:
        mov_idx = 0
    if mov_idx < 0:
        mov_idx = 0
    ok = attach_alta_format_history(
        db_path,
        expediente_id,
        int(g.user["id"]),
        fh_id,
        now_iso(),
        movimiento_idx=mov_idx,
    )
    if ok:
        sync_expediente_nombre_desde_alta(
            db_path, expediente_id, fh_id, now_iso(), movimiento_idx=mov_idx
        )
        flash("Constancia vinculada al expediente.", "success")
    else:
        flash("No se pudo vincular la constancia al expediente.", "error")
    return redirect(url_for("carrier_curso.index", e=expediente_id))


@carrier_curso_bp.route("/expediente/<int:expediente_id>/formatos-imss.json")
def formatos_imss_json(expediente_id: int):
    """Historial IMSS paginado + búsqueda: misma consulta base que Movimientos IMSS (`format_history` + `users`, sin filtro por usuario). La vinculación POST valida solo que el expediente sea del usuario; la constancia puede ser de cualquier autor."""
    if (redir := _login_required()) is not None:
        return redir
    db_path = _db_path()
    row = get_expediente(db_path, expediente_id)
    if not row or int(row.user_id) != int(g.user["id"]):
        abort(404)
    uid = int(g.user["id"])
    q = (request.args.get("q") or "").strip()
    page = request.args.get("page", default=1, type=int) or 1
    per_page = request.args.get("per_page", default=12, type=int) or 12
    page = max(1, page)
    per_page = max(5, min(40, per_page))
    offset = (page - 1) * per_page
    total = count_format_history_imss_list_like(db_path, q or None)
    raw = list_format_history_imss_list_like_page(
        db_path, q=q or None, offset=offset, limit=per_page
    )
    rows_out: list[dict[str, Any]] = []
    for rec in raw:
        oid = int(rec["owner_user_id"])
        rows_out.append(
            {
                "id": rec["id"],
                "filename": rec["filename"],
                "created_at": rec["created_at"],
                "movement_count": int(rec["movement_count"] or 0),
                "nombres": _nombres_movimientos_desde_payload(rec["payload_json"]),
                "folio": str(rec.get("folio") or ""),
                "lote": str(rec.get("lote") or ""),
                "username": str(rec.get("username") or ""),
                "owner_user_id": oid,
                "can_vincular": True,
            }
        )
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    return jsonify(
        {
            "ok": True,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "rows": rows_out,
            "current_user_id": uid,
        }
    )


@carrier_curso_bp.route("/expediente/<int:expediente_id>/preview/<slot>")
def expediente_preview_slot(expediente_id: int, slot: str):
    if (redir := _login_required()) is not None:
        return redir
    if slot not in ALL_UPLOAD_SLOTS:
        abort(404)
    row = get_expediente(_db_path(), expediente_id)
    if not row or int(row.user_id) != int(g.user["id"]):
        abort(404)
    slots = parse_slots(row.slots_json)
    meta = _slot_meta(slots, slot)
    rel = meta.get("rel")
    if not rel:
        abort(404)
    path = _storage_root() / str(rel)
    if not path.exists():
        abort(404)

    page_idx = int(request.args.get("page", 0) or 0)
    ext = path.suffix.lower()
    if ext == ".pdf":
        doc = fitz.open(str(path))
        try:
            if page_idx < 0 or page_idx >= doc.page_count:
                abort(404)
            pix = doc.load_page(page_idx).get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            png = pix.tobytes("png")
        finally:
            doc.close()
        return current_app.response_class(png, mimetype="image/png")

    if ext in {".png", ".jpg", ".jpeg", ".webp"}:
        return send_file(path)

    abort(400)


@carrier_curso_bp.route("/expediente/<int:expediente_id>/export", methods=["POST"])
def expediente_export(expediente_id: int):
    if (redir := _login_required()) is not None:
        return redir
    db_path = _db_path()
    row = get_expediente(db_path, expediente_id)
    if not row or int(row.user_id) != int(g.user["id"]):
        abort(404)

    monthly = get_monthly_base(db_path, row.base_year_month)
    slots = parse_slots(row.slots_json)
    modo = getattr(row, "constancia_modo", "alta") or "alta"
    if modo not in {"alta", "renovacion"}:
        modo = "alta"

    alta_path: Path | None = None
    alta_pages: list[int] | None = None
    nombre_desde_alta: str | None = None
    if modo == "alta" and row.alta_format_history_id:
        hr = _get_format_history_row(db_path, int(row.alta_format_history_id))
        if hr and int(hr["user_id"]) == int(g.user["id"]):
            m_idx = int(getattr(row, "alta_movimiento_idx", 0) or 0)
            nombre_desde_alta = worker_name_from_payload_json(hr["payload_json"], m_idx)
            alta_path = Path(str(hr["pdf_path"]))
            if alta_path.suffix.lower() != ".pdf":
                flash(
                    "La constancia vinculada no es PDF (p. ej. PNG o ZIP). "
                    "Genera de nuevo el movimiento en formato PDF para incluirlo en el expediente.",
                    "error",
                )
                return redirect(url_for("carrier_curso.index", e=expediente_id))
            raw_pages = request.form.getlist("alta_pdf_pages")
            if raw_pages:
                try:
                    doc = fitz.open(str(alta_path))
                    try:
                        n = doc.page_count
                    finally:
                        doc.close()
                    alta_pages = sorted({int(x) for x in raw_pages if str(x).strip().isdigit() and 0 <= int(x) < n})
                    if not alta_pages:
                        alta_pages = None
                except Exception:
                    alta_pages = None

    sources = _merge_sources_for_expediente(
        monthly=monthly,
        slots=slots,
        constancia_modo=modo,
        alta_path=alta_path,
        alta_pages=alta_pages,
    )
    if not sources:
        flash("No hay contenido para exportar. Revisa documentos base y archivos del expediente.", "error")
        return redirect(url_for("carrier_curso.index", e=expediente_id))

    pdf_display_name = curso_export_pdf_display_name(
        worker_name_from_alta=nombre_desde_alta,
        expediente_nombre=row.nombre_persona,
    )
    token = uuid.uuid4().hex
    rel = f"exports/{token}.pdf"
    out_path = _exports_dir() / f"{token}.pdf"
    try:
        write_merged_pdf(sources, out_path)
    except Exception as exc:
        flash(f"No se pudo generar el PDF: {exc}", "error")
        return redirect(url_for("carrier_curso.index", e=expediente_id))

    ts = now_iso()
    insert_carrier_curso_export_log(
        db_path,
        user_id=int(g.user["id"]),
        expediente_id=expediente_id,
        created_at=ts,
        nombre_persona=row.nombre_persona,
        pdf_stored_relpath=rel,
        pdf_display_name=pdf_display_name,
        alta_format_history_id=row.alta_format_history_id,
    )
    log_app_activity(
        db_path,
        user_id=int(g.user["id"]),
        module="carrier_cursos",
        action="exportar_pdf",
        status="ok",
        ref=f"exp:{expediente_id}",
    )

    flash("PDF del expediente generado y guardado en el historial de Cursos.", "success")
    return send_file(out_path, as_attachment=True, download_name=pdf_display_name)


@carrier_curso_bp.route("/historial")
def historial_exportaciones():
    if (redir := _login_required()) is not None:
        return redir
    db_path = _db_path()
    uid = int(g.user["id"])
    is_admin = _is_admin_carrier()
    rows = list_carrier_curso_export_logs(db_path, user_id=None if is_admin else uid)
    return render_template(
        "cursos_historial.html",
        rows=rows,
        is_admin=is_admin,
    )


@carrier_curso_bp.route("/historial/borrar-export/<int:log_id>", methods=["POST"])
def historial_borrar_export(log_id: int):
    if (redir := _login_required()) is not None:
        return redir
    if not _is_admin_carrier():
        abort(403)
    db_path = _db_path()
    log = get_carrier_curso_export_log(db_path, log_id)
    if not log:
        abort(404)
    path = _storage_root() / log.pdf_stored_relpath
    delete_carrier_curso_export_log(db_path, log_id)
    log_app_activity(
        db_path,
        user_id=int(g.user["id"]),
        module="carrier_cursos",
        action="eliminar_export",
        status="ok",
        ref=str(log_id),
    )
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass
    flash("Registro de exportación eliminado.", "success")
    return redirect(url_for("carrier_curso.historial_exportaciones"))


@carrier_curso_bp.route("/descargar/<int:log_id>")
def descargar_export_log(log_id: int):
    if (redir := _login_required()) is not None:
        return redir
    log = get_carrier_curso_export_log(_db_path(), log_id)
    if not log:
        abort(404)
    if int(log.user_id) != int(g.user["id"]) and not _is_admin_carrier():
        abort(403)
    path = _storage_root() / log.pdf_stored_relpath
    if not path.is_file():
        flash("El archivo ya no está disponible en el servidor.", "error")
        return redirect(url_for("carrier_curso.historial_exportaciones"))
    return send_file(path, as_attachment=True, download_name=log.pdf_display_name)


@carrier_curso_bp.route("/expediente/<int:expediente_id>/ir-alta")
def ir_al_generador_alta(expediente_id: int):
    """Compatibilidad: el Alta se captura en Cursos; ya no redirige a otra pantalla."""
    if (redir := _login_required()) is not None:
        return redir
    row = get_expediente(_db_path(), expediente_id)
    if not row or int(row.user_id) != int(g.user["id"]):
        abort(404)
    set_return_expediente_id(session, expediente_id)
    return redirect(
        url_for("carrier_curso.index", e=expediente_id, alta_form=1) + "#carrier-alta-imss"
    )


def register_carrier(app):
    import sqlite3

    db_path = str(app.config["DATABASE"])
    conn = sqlite3.connect(db_path)
    try:
        ensure_carrier_tables(conn)
    finally:
        conn.close()
    from modules.carrier.inhabiles import ensure_inhabiles_file

    ensure_inhabiles_file(Path(app.instance_path))
    app.register_blueprint(carrier_curso_bp)
