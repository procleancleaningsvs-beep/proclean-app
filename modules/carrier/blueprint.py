"""
Rutas Flask: Carrier > Cursos.

La integración con Altas reutiliza la vista `nuevo_formato` y la sesión
`carrier_curso_return_expediente_id` (ver `modules.carrier.integration`).
"""

from __future__ import annotations

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
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from modules.carrier.dates import now_iso, today_in_app_tz
from modules.carrier.db import (
    attach_alta_format_history,
    clear_alta_link,
    ensure_carrier_tables,
    get_carrier_curso_export_log,
    get_expediente,
    get_monthly_base,
    insert_carrier_curso_export_log,
    insert_expediente,
    list_carrier_curso_export_logs,
    list_expedientes,
    list_monthly_bases,
    parse_slots,
    dumps_slots,
    update_expediente_meta,
    update_expediente_slots_json,
    upsert_monthly_base,
)
from modules.carrier.export_naming import curso_export_pdf_display_name, first_worker_name_from_payload_json
from modules.carrier.inhabiles import load_inhabile_dates
from modules.carrier.integration import set_return_expediente_id
from modules.carrier.pdf_merge import write_merged_pdf
from modules.carrier.vigencia import (
    max_still_valid_payment_month,
    payment_month_still_valid_today,
    should_warn_stale_payment_month,
    ym_to_str,
)

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

FILE_SLOTS = ("curso_evidencia", "foto_persona", "ine_frente", "ine_reverso")
ALLOWED_EXTENSIONS = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".webp"})

SLOT_LABELS = {
    "curso_evidencia": "Evidencia del curso / Forms",
    "foto_persona": "Foto de la persona",
    "ine_frente": "INE frente",
    "ine_reverso": "INE reverso",
}

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


def _filter_bases_expediente(all_bases: list[Any], *, is_admin: bool, today_d: date, inhabiles: set[date]) -> list[Any]:
    out: list[Any] = []
    for b in all_bases:
        pym = _parse_year_month(b.year_month)
        if not pym:
            continue
        y, m = map(int, b.year_month.split("-", 1))
        if is_admin or payment_month_still_valid_today(y, m, today_d, inhabiles):
            out.append(b)
    return sorted(out, key=lambda x: x.year_month, reverse=True)


def _operational_package_ym(today_d: date, inhabiles: set[date]) -> str:
    op = max_still_valid_payment_month(today_d, inhabiles)
    if op:
        return ym_to_str(op[0], op[1])
    return f"{today_d.year:04d}-{today_d.month:02d}"


def _user_may_manage_mensual_ym(ym: str, *, is_admin: bool, today_d: date, inhabiles: set[date]) -> bool:
    if not _parse_year_month(ym):
        return False
    if is_admin:
        return True
    allowed = _operational_package_ym(today_d, inhabiles)
    return ym == allowed


def _user_may_access_mensual_download(
    ym: str, _kind: str, *, is_admin: bool, today_d: date, inhabiles: set[date]
) -> bool:
    """Admin: cualquier mes con paquete. Usuario: solo el paquete operativo vigente."""
    if not _parse_year_month(ym):
        return False
    if is_admin:
        return True
    return _user_may_manage_mensual_ym(ym, is_admin=False, today_d=today_d, inhabiles=inhabiles)


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


def _merge_sources_for_expediente(
    *,
    monthly: Any,
    slots: dict[str, Any],
    alta_path: Path | None,
    alta_pages: list[int] | None,
) -> list[tuple[str, Path, list[int] | None, tuple[float, float, float, float] | None]]:
    """Orden fijo del expediente."""
    sources: list[tuple[str, Path, list[int] | None, tuple[float, float, float, float] | None]] = []

    if monthly and monthly.sipare_relpath:
        p = _storage_root() / monthly.sipare_relpath
        sources.append(("pdf", p, None, None))
    if monthly and monthly.pago_imss_relpath:
        p = _storage_root() / monthly.pago_imss_relpath
        sources.append(("pdf", p, None, None))

    for slot in FILE_SLOTS:
        meta = _slot_meta(slots, slot)
        rel = meta.get("rel")
        if not rel:
            continue
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
        kind = "pdf" if ext == ".pdf" else "image"
        sources.append((kind, path, plist, cr if kind == "image" else None))

    if alta_path and alta_path.exists():
        sources.append(("pdf", alta_path, alta_pages, None))

    return sources


@carrier_curso_bp.app_context_processor
def _inject_forms_url():
    return {"carrier_forms_curso_url": FORMS_CURSO_URL}


@carrier_curso_bp.route("/")
def index():
    if (redir := _login_required()) is not None:
        return redir
    db_path = _db_path()
    all_bases = list_monthly_bases(db_path)
    expedientes = list_expedientes(db_path, user_id=int(g.user["id"]))
    inhabiles = load_inhabile_dates(_instance_dir())
    today_d = today_in_app_tz()
    is_admin = _is_admin_carrier()
    operational_ym = _operational_package_ym(today_d, inhabiles)
    operational_label = _label_paquete_mes(operational_ym)
    operational_base = get_monthly_base(db_path, operational_ym)

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
            y, m = map(int, active_ym.split("-", 1))
            stale_warning = should_warn_stale_payment_month(y, m, today_d, inhabiles)
    else:
        active_ym = operational_ym
        if _parse_year_month(active_ym):
            active_base = operational_base
            y, m = map(int, active_ym.split("-", 1))
            stale_warning = should_warn_stale_payment_month(y, m, today_d, inhabiles)

    y0 = today_d.year
    year_options = list(range(y0 - 2, y0 + 4))

    return render_template(
        "cursos_index.html",
        is_admin=is_admin,
        all_bases=all_bases if is_admin else None,
        expedientes=expedientes,
        active_ym=active_ym,
        active_base=active_base,
        operational_ym=operational_ym,
        operational_label=operational_label,
        operational_base=operational_base,
        active_label=_label_paquete_mes(active_ym) if active_ym else "",
        stale_warning=stale_warning,
        inhabiles_path=str(_instance_dir() / "carrier_inhabiles.json"),
        mes_labels=MES_LABEL_ES,
        year_options=year_options,
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
    if (redir := _login_required()) is not None:
        return redir
    db_path = _db_path()
    all_bases = list_monthly_bases(db_path)
    inhabiles = load_inhabile_dates(_instance_dir())
    today_d = today_in_app_tz()
    is_admin = _is_admin_carrier()
    bases = _filter_bases_expediente(all_bases, is_admin=is_admin, today_d=today_d, inhabiles=inhabiles)
    if request.method == "POST":
        nombre = (request.form.get("nombre_persona") or "").strip()
        ym = _parse_year_month(request.form.get("base_year_month") or "")
        if not nombre:
            flash("El nombre de la persona es obligatorio.", "error")
            return redirect(url_for("carrier_curso.expediente_nuevo"))
        if not ym:
            flash("Selecciona un mes base válido.", "error")
            return redirect(url_for("carrier_curso.expediente_nuevo"))
        allowed_yms = {b.year_month for b in bases}
        if ym not in allowed_yms:
            flash("Ese mes base no está disponible para tu usuario.", "error")
            return redirect(url_for("carrier_curso.expediente_nuevo"))
        if not get_monthly_base(db_path, ym):
            flash("Ese mes base aún no tiene documentos cargados.", "error")
            return redirect(url_for("carrier_curso.expediente_nuevo"))
        ts = now_iso()
        eid = insert_expediente(
            db_path,
            user_id=int(g.user["id"]),
            nombre_persona=nombre,
            base_year_month=ym,
            created_at=ts,
            updated_at=ts,
        )
        flash("Expediente creado. Sube los anexos y exporta el PDF cuando esté listo.", "success")
        return redirect(url_for("carrier_curso.expediente_edit", expediente_id=eid))

    default_ym = (request.args.get("mes") or "").strip() if is_admin else ""
    if not default_ym:
        op = _operational_package_ym(today_d, inhabiles)
        default_ym = op
    if is_admin and default_ym and default_ym not in {b.year_month for b in bases} and bases:
        default_ym = bases[0].year_month
    return render_template("cursos_expediente_nuevo.html", bases=bases, default_ym=default_ym)


@carrier_curso_bp.route("/expediente/<int:expediente_id>", methods=["GET", "POST"])
def expediente_edit(expediente_id: int):
    if (redir := _login_required()) is not None:
        return redir
    db_path = _db_path()
    row = get_expediente(db_path, expediente_id)
    if not row or int(row.user_id) != int(g.user["id"]):
        abort(404)

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "save_meta":
            nombre = (request.form.get("nombre_persona") or "").strip()
            is_ad = _is_admin_carrier()
            if not nombre:
                flash("El nombre es obligatorio.", "error")
                return redirect(url_for("carrier_curso.expediente_edit", expediente_id=expediente_id))
            if not is_ad:
                update_expediente_meta(
                    db_path,
                    expediente_id,
                    nombre_persona=nombre,
                    updated_at=now_iso(),
                )
                flash("Datos del expediente guardados.", "success")
                return redirect(url_for("carrier_curso.expediente_edit", expediente_id=expediente_id))
            ym = _parse_year_month(request.form.get("base_year_month") or "")
            if ym and get_monthly_base(db_path, ym):
                update_expediente_meta(
                    db_path,
                    expediente_id,
                    nombre_persona=nombre,
                    base_year_month=ym,
                    updated_at=now_iso(),
                )
                flash("Datos del expediente guardados.", "success")
            else:
                flash("No se pudo guardar: revisa mes base y que exista paquete para ese mes.", "error")
            return redirect(url_for("carrier_curso.expediente_edit", expediente_id=expediente_id))
        if action == "clear_alta":
            if clear_alta_link(db_path, expediente_id, int(g.user["id"]), now_iso()):
                flash("Se quitó el vínculo con la constancia IMSS.", "success")
            return redirect(url_for("carrier_curso.expediente_edit", expediente_id=expediente_id))

    slots = parse_slots(row.slots_json)
    monthly = get_monthly_base(db_path, row.base_year_month)
    inhabiles = load_inhabile_dates(_instance_dir())
    pm = _parse_ym_to_date(row.base_year_month)
    stale = False
    if pm:
        stale = should_warn_stale_payment_month(pm[0], pm[1], today_in_app_tz(), inhabiles)

    alta_info: dict[str, Any] | None = None
    alta_pdf_page_count = 0
    if row.alta_format_history_id:
        hr = _get_format_history_row(db_path, int(row.alta_format_history_id))
        if hr and int(hr["user_id"]) == int(g.user["id"]):
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

    pdf_page_counts: dict[str, int] = {}
    for slot in FILE_SLOTS:
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

    return render_template(
        "cursos_expediente.html",
        expediente=row,
        slots=slots,
        monthly=monthly,
        slot_labels=SLOT_LABELS,
        file_slots=FILE_SLOTS,
        stale_month_warning=stale,
        alta_info=alta_info,
        pdf_page_counts=pdf_page_counts,
        alta_pdf_page_count=alta_pdf_page_count,
        lock_mes_base=not _is_admin_carrier(),
    )


@carrier_curso_bp.route("/expediente/<int:expediente_id>/upload/<slot>", methods=["POST"])
def expediente_upload_slot(expediente_id: int, slot: str):
    if (redir := _login_required()) is not None:
        return redir
    if slot not in FILE_SLOTS:
        abort(404)
    db_path = _db_path()
    row = get_expediente(db_path, expediente_id)
    if not row or int(row.user_id) != int(g.user["id"]):
        abort(404)

    f = request.files.get("file")
    if not f or not f.filename:
        flash("Selecciona un archivo.", "error")
        return redirect(url_for("carrier_curso.expediente_edit", expediente_id=expediente_id))

    ext = _validate_upload_ext(f.filename)
    if not ext:
        flash("Tipo de archivo no permitido. Usa PDF, PNG, JPG, JPEG o WEBP.", "error")
        return redirect(url_for("carrier_curso.expediente_edit", expediente_id=expediente_id))

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
    slots[slot] = {
        "rel": rel,
        "orig": f.filename,
        "pdf_pages": None,
        "crop_norm": None,
    }
    update_expediente_slots_json(db_path, expediente_id, dumps_slots(slots), now_iso())
    flash(f"Archivo cargado: {SLOT_LABELS.get(slot, slot)}.", "success")
    return redirect(url_for("carrier_curso.expediente_edit", expediente_id=expediente_id))


@carrier_curso_bp.route("/expediente/<int:expediente_id>/slot-meta", methods=["POST"])
def expediente_slot_meta(expediente_id: int):
    if (redir := _login_required()) is not None:
        return redir
    db_path = _db_path()
    row = get_expediente(db_path, expediente_id)
    if not row or int(row.user_id) != int(g.user["id"]):
        abort(404)

    slot = (request.form.get("slot") or "").strip()
    if slot not in FILE_SLOTS:
        abort(400)

    slots = parse_slots(row.slots_json)
    meta = _slot_meta(slots, slot)
    if not meta.get("rel"):
        flash("Primero sube un archivo para este apartado.", "error")
        return redirect(url_for("carrier_curso.expediente_edit", expediente_id=expediente_id))

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

    slots[slot] = meta
    update_expediente_slots_json(db_path, expediente_id, dumps_slots(slots), now_iso())
    flash("Opciones de recorte / páginas guardadas (opcional).", "success")
    return redirect(url_for("carrier_curso.expediente_edit", expediente_id=expediente_id))


@carrier_curso_bp.route("/expediente/<int:expediente_id>/preview/<slot>")
def expediente_preview_slot(expediente_id: int, slot: str):
    if (redir := _login_required()) is not None:
        return redir
    if slot not in FILE_SLOTS:
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

    alta_path: Path | None = None
    alta_pages: list[int] | None = None
    nombre_desde_alta: str | None = None
    if row.alta_format_history_id:
        hr = _get_format_history_row(db_path, int(row.alta_format_history_id))
        if hr and int(hr["user_id"]) == int(g.user["id"]):
            nombre_desde_alta = first_worker_name_from_payload_json(hr["payload_json"])
            alta_path = Path(str(hr["pdf_path"]))
            if alta_path.suffix.lower() != ".pdf":
                flash(
                    "La constancia vinculada no es PDF (p. ej. PNG o ZIP). "
                    "Genera de nuevo el movimiento en formato PDF para incluirlo en el expediente.",
                    "error",
                )
                return redirect(url_for("carrier_curso.expediente_edit", expediente_id=expediente_id))
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
        alta_path=alta_path,
        alta_pages=alta_pages,
    )
    if not sources:
        flash("No hay contenido para exportar. Revisa documentos base y archivos del expediente.", "error")
        return redirect(url_for("carrier_curso.expediente_edit", expediente_id=expediente_id))

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
        return redirect(url_for("carrier_curso.expediente_edit", expediente_id=expediente_id))

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

    flash("PDF del expediente generado y guardado en el historial de Cursos.", "success")
    return send_file(out_path, as_attachment=True, download_name=pdf_display_name)


@carrier_curso_bp.route("/historial")
def historial_exportaciones():
    if (redir := _login_required()) is not None:
        return redir
    rows = list_carrier_curso_export_logs(_db_path(), user_id=int(g.user["id"]))
    return render_template("cursos_historial.html", rows=rows)


@carrier_curso_bp.route("/descargar/<int:log_id>")
def descargar_export_log(log_id: int):
    if (redir := _login_required()) is not None:
        return redir
    log = get_carrier_curso_export_log(_db_path(), log_id)
    if not log or int(log.user_id) != int(g.user["id"]):
        abort(404)
    path = _storage_root() / log.pdf_stored_relpath
    if not path.is_file():
        flash("El archivo ya no está disponible en el servidor.", "error")
        return redirect(url_for("carrier_curso.historial_exportaciones"))
    return send_file(path, as_attachment=True, download_name=log.pdf_display_name)


@carrier_curso_bp.route("/expediente/<int:expediente_id>/ir-alta")
def ir_al_generador_alta(expediente_id: int):
    """Acceso directo al formulario existente de movimientos IMSS con retorno al expediente."""
    if (redir := _login_required()) is not None:
        return redir
    row = get_expediente(_db_path(), expediente_id)
    if not row or int(row.user_id) != int(g.user["id"]):
        abort(404)
    set_return_expediente_id(session, expediente_id)
    return redirect(url_for("nuevo_formato", carrier_curso_expediente_id=expediente_id))


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
