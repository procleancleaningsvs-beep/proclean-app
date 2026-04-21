"""Rutas Flask: Vitroflex > Exámenes médicos (formulario maestro)."""

from __future__ import annotations

import io
import json
import random
import sqlite3
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from functools import wraps

from modules.examenes_medicos.clinical_autogen import generate_clinical_bundle
from modules.examenes_medicos.db import (
    delete_examen_historial,
    ensure_examenes_medicos_tables_path,
    get_examen_historial,
    insert_examen_historial,
    list_examen_historial,
)
from modules.examenes_medicos.export_helpers import (
    app_mx_today,
    build_orina_data_for_mapping,
    build_orina_mapping,
    build_paciente_orina,
    build_sangre_data_for_mapping,
    build_sangre_mapping,
    default_hora_val_sugerida,
    default_yesterday_iso_mx,
    resolve_generated_artifact,
    safe_file_stem,
)
from modules.examenes_medicos.identifiers import (
    generate_unique_folio_orina,
    generate_unique_folio_sangre,
    get_or_create_cliente_numero,
    normalize_nombre_key,
    stable_codigo_barra,
)
from modules.examenes_medicos.paths import ORINA_DOCX, SANGRE_DOCX
from modules.examenes_medicos.validation import (
    classify_imc,
    edad_desde_fecha_nacimiento,
    parse_date_iso,
    validate_positive_float,
    validate_required_non_empty,
    validate_sexo,
)
from modules.finiquitos.docx_placeholders import replace_placeholders_in_docx_bytes
from modules.vitroflex_docs.libreoffice_pdf import docx_bytes_to_pdf_bytes
from services.app_activity import log_app_activity

_BASE = Path(__file__).resolve().parent.parent.parent
_TEMPLATE_DIR = _BASE / "templates" / "examenes_medicos"
_STATIC_DIR = _BASE / "static" / "examenes_medicos"

examenes_medicos_bp = Blueprint(
    "examenes_medicos",
    __name__,
    url_prefix="/vitroflex/examenes-medicos",
    template_folder=str(_TEMPLATE_DIR),
    static_folder=str(_STATIC_DIR),
    static_url_path="em-assets",
)


def _login_required_page(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def _login_required_json():
    if g.user is None:
        return jsonify({"ok": False, "error": "No autenticado."}), 401
    return None


def _now_iso() -> str:
    return datetime.now(ZoneInfo("America/Mexico_City")).strftime("%Y-%m-%d %H:%M:%S")


def _is_admin() -> bool:
    try:
        return g.user is not None and g.user["role"] == "admin"
    except (TypeError, KeyError):
        return False


def _normalize_master(data: dict[str, Any]) -> dict[str, Any]:
    """Añade `edad` coherente con fecha de nacimiento."""
    m = {k: data.get(k) for k in data}
    fnac, _ = parse_date_iso(m.get("fecha_nacimiento"))
    if fnac is not None:
        m["edad"] = str(edad_desde_fecha_nacimiento(fnac, app_mx_today()))
    return m


def _errors_master_form(data: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    for label, key in (
        ("Nombres", "nombres"),
        ("Apellidos", "apellidos"),
    ):
        e = validate_required_non_empty(data.get(key), label)
        if e:
            errs.append(e)
    fnac_raw = data.get("fecha_nacimiento")
    _fnac, ferr = parse_date_iso(fnac_raw)
    if ferr:
        errs.append("Fecha de nacimiento: " + (ferr or "inválida."))

    e = validate_sexo(data.get("sexo"))
    if e:
        errs.append(e)

    med = str(data.get("medico") or "").strip()
    if len(med) > 200:
        errs.append("Médico: texto demasiado largo (máx. 200 caracteres).")

    for label, key in (
        ("Fecha de estudio", "fecha_estudio"),
        ("Fecha de toma", "fecha_toma"),
        ("Fecha de validación", "fecha_val"),
    ):
        e = validate_required_non_empty(data.get(key), label)
        if e:
            errs.append(e)
    for key in ("fecha_estudio", "fecha_toma", "fecha_val"):
        if parse_date_iso(data.get(key))[1]:
            errs.append(f"{key}: fecha inválida.")

    for label, key in (
        ("Hora de toma", "hora_toma"),
        ("Hora de validación", "hora_val"),
    ):
        e = validate_required_non_empty(data.get(key), label)
        if e:
            errs.append(e)

    p1 = validate_positive_float(data.get("peso_kg"), "Peso (kg)")
    p2 = validate_positive_float(data.get("estatura_m"), "Estatura (m)")
    if p1[1]:
        errs.append(p1[1])
    if p2[1]:
        errs.append(p2[1])
    est = p2[0]
    if est is not None and est > 2.6:
        errs.append("Estatura fuera de rango razonable (metros).")

    return errs


def _assign_admin_ids(conn: sqlite3.Connection, master: dict[str, Any], rng: random.Random) -> dict[str, str]:
    """Añade folio_orina, folio_sangre, cliente_numero, codigo_barra únicos/reutilizados."""
    key = normalize_nombre_key(
        str(master.get("nombres") or ""),
        str(master.get("apellidos") or ""),
    )
    cliente = get_or_create_cliente_numero(conn, key, rng)
    codigo = stable_codigo_barra(key)
    fo = generate_unique_folio_orina(conn, rng)
    fs = generate_unique_folio_sangre(conn, rng)
    return {
        "folio_orina": fo,
        "folio_sangre": fs,
        "cliente_numero": cliente,
        "codigo_barra": codigo,
    }


def _maybe_insert_imc_historial(db_path: str, master: dict[str, Any], ident: dict[str, str]) -> int | None:
    p1 = validate_positive_float(master.get("peso_kg"), "Peso (kg)")
    p2 = validate_positive_float(master.get("estatura_m"), "Estatura (m)")
    peso, est = p1[0], p2[0]
    errs = [x for x in (p1[1], p2[1]) if x]
    if errs or peso is None or est is None or est > 2.6:
        return None
    imc = peso / (est**2)
    clas = classify_imc(imc)
    pac = build_paciente_orina(str(master.get("nombres") or ""), str(master.get("apellidos") or ""))
    snap = {
        "tipo": "imc",
        "formulario_maestro": {**master, **ident},
        "valores": {"peso_kg": peso, "estatura_m": est, "imc": round(imc, 2), "clasificacion": clas},
    }
    rid = insert_examen_historial(
        db_path,
        user_id=int(g.user["id"]),
        created_at=_now_iso(),
        exam_type="imc",
        patient_display_name=pac or "IMC",
        payload=snap,
        docx_relpath=None,
        pdf_relpath=None,
        docx_download_name=None,
        pdf_download_name=None,
    )
    log_app_activity(
        db_path,
        user_id=int(g.user["id"]),
        module="examenes_medicos",
        action="imc_auto",
        status="ok",
        ref=str(rid),
        detail="post_descarga",
    )
    return rid


def _persist_export(
    *,
    exam_type: str,
    patient_display_name: str,
    payload: dict[str, Any],
    docx_bytes: bytes,
    pdf_bytes: bytes,
    docx_download: str,
    pdf_download: str,
) -> int:
    gen = Path(current_app.config["GENERATED_DIR"])
    gen.mkdir(parents=True, exist_ok=True)
    rel_dir = f"examenes_medicos/{uuid.uuid4().hex}"
    folder = gen / rel_dir
    folder.mkdir(parents=True, exist_ok=True)
    docx_name = "examen.docx"
    pdf_name = "examen.pdf"
    (folder / docx_name).write_bytes(docx_bytes)
    (folder / pdf_name).write_bytes(pdf_bytes)
    rid = insert_examen_historial(
        str(current_app.config["DATABASE"]),
        user_id=int(g.user["id"]),
        created_at=_now_iso(),
        exam_type=exam_type,
        patient_display_name=patient_display_name,
        payload=payload,
        docx_relpath=f"{rel_dir}/{docx_name}",
        pdf_relpath=f"{rel_dir}/{pdf_name}",
        docx_download_name=docx_download,
        pdf_download_name=pdf_download,
    )
    log_app_activity(
        str(current_app.config["DATABASE"]),
        user_id=int(g.user["id"]),
        module="examenes_medicos",
        action="exportar",
        status="ok",
        ref=str(rid),
        detail=exam_type,
    )
    return rid


def _orina_docx_bytes(mapping: dict[str, str]) -> bytes:
    if not ORINA_DOCX.is_file():
        raise FileNotFoundError(str(ORINA_DOCX))
    raw = ORINA_DOCX.read_bytes()
    return replace_placeholders_in_docx_bytes(raw, mapping)


def _sangre_docx_bytes(mapping: dict[str, str]) -> bytes:
    if not SANGRE_DOCX.is_file():
        raise FileNotFoundError(str(SANGRE_DOCX))
    raw = SANGRE_DOCX.read_bytes()
    return replace_placeholders_in_docx_bytes(raw, mapping)


@examenes_medicos_bp.route("/", methods=["GET"])
@_login_required_page
def index():
    y = default_yesterday_iso_mx()
    return render_template(
        "examenes_medicos_index.html",
        default_fecha=y,
        default_hora_toma="08:30:00",
        default_hora_val=default_hora_val_sugerida("08:30:00"),
    )


@examenes_medicos_bp.route("/historial", methods=["GET"])
@_login_required_page
def historial_page():
    rows = list_examen_historial(
        str(current_app.config["DATABASE"]),
        user_id=None if _is_admin() else int(g.user["id"]),
    )
    return render_template("examenes_medicos_historial.html", rows=rows, is_admin=_is_admin())


@examenes_medicos_bp.route("/historial/<int:rid>", methods=["GET"])
@_login_required_page
def historial_detalle(rid: int):
    row = get_examen_historial(str(current_app.config["DATABASE"]), rid)
    if not row:
        abort(404)
    if not _is_admin() and int(row.user_id) != int(g.user["id"]):
        abort(404)
    try:
        payload = json.loads(row.payload_json)
    except (json.JSONDecodeError, TypeError):
        payload = {}
    gen = Path(current_app.config["GENERATED_DIR"]).resolve()
    docx_p = resolve_generated_artifact(gen, row.docx_relpath)
    pdf_p = resolve_generated_artifact(gen, row.pdf_relpath)
    return render_template(
        "examenes_medicos_detalle.html",
        row=row,
        payload=payload,
        docx_ok=docx_p is not None,
        pdf_ok=pdf_p is not None,
        is_admin=_is_admin(),
    )


@examenes_medicos_bp.route("/api/clinical-preview", methods=["GET"])
def api_clinical_preview():
    err = _login_required_json()
    if err:
        return err
    sexo = str(request.args.get("sexo") or "Mujer").strip()
    raw = request.args.get("seed")
    seed: int | None
    try:
        seed = int(raw) if raw is not None and str(raw).strip() != "" else None
    except ValueError:
        seed = None
    if seed is None:
        seed = random.randrange(0, 2**31)
    bundle = generate_clinical_bundle(sexo=sexo, seed=seed)
    return jsonify({"ok": True, "seed": seed, "bundle": bundle})


def _form_dict_clean(data: dict[str, Any]) -> dict[str, Any]:
    skip = frozenset({"target", "format", "scope", "seed_clinico"})
    return {k: v for k, v in data.items() if k not in skip}


@examenes_medicos_bp.route("/api/master/preview-identificadores", methods=["POST"])
def api_preview_identificadores():
    err = _login_required_json()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    n = str(data.get("nombres") or "").strip()
    a = str(data.get("apellidos") or "").strip()
    if not n or not a:
        return jsonify({"ok": False, "error": "Indique nombres y apellidos."}), 400
    key = normalize_nombre_key(n, a)
    db_path = str(current_app.config["DATABASE"])
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT cliente_numero FROM examenes_medicos_cliente_cache WHERE nombre_key = ?",
            (key,),
        ).fetchone()
    finally:
        conn.close()
    return jsonify(
        {
            "ok": True,
            "codigo_barra": stable_codigo_barra(key),
            "cliente_numero_existente": str(row[0]) if row else None,
            "nota_folios": "Los folios de orina y sangre se asignan al confirmar la descarga.",
        }
    )


@examenes_medicos_bp.route("/api/master/download", methods=["POST"])
def api_master_download():
    err = _login_required_json()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    raw = _form_dict_clean(data)
    errs = _errors_master_form(raw)
    if errs:
        return jsonify({"ok": False, "errors": errs}), 400

    scope = str(data.get("scope") or "both").strip().lower()
    if scope not in ("both", "orina", "sangre"):
        return jsonify({"ok": False, "error": "scope debe ser both, orina o sangre."}), 400
    want = str(data.get("format") or "pdf").lower().strip()
    if want not in ("docx", "pdf"):
        return jsonify({"ok": False, "error": "format debe ser docx o pdf."}), 400

    master_base = _normalize_master(raw)
    raw_seed = data.get("seed_clinico")
    try:
        clin_seed: int | None = int(raw_seed) if raw_seed is not None and str(raw_seed).strip() != "" else None
    except (TypeError, ValueError):
        clin_seed = None
    if clin_seed is None:
        clin_seed = random.randrange(0, 2**31)

    db_path = str(current_app.config["DATABASE"])
    rng = random.Random()
    conn = sqlite3.connect(db_path)
    try:
        ident = _assign_admin_ids(conn, master_base, rng)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    master = {**master_base, **ident}
    bundle = generate_clinical_bundle(sexo=str(master.get("sexo") or ""), seed=clin_seed)
    pac = build_paciente_orina(str(master.get("nombres") or ""), str(master.get("apellidos") or ""))

    def build_one_orina() -> tuple[bytes, bytes, str, dict[str, str], dict[str, Any]]:
        odata = build_orina_data_for_mapping(master, bundle["orina"])
        mapping = build_orina_mapping(odata)
        dx = _orina_docx_bytes(mapping)
        stem_o = safe_file_stem("Examen de Orina", str(master.get("nombres")), str(master.get("apellidos")))
        pdf_b = docx_bytes_to_pdf_bytes(dx, pdf_stem=stem_o)
        return dx, pdf_b, stem_o, mapping, odata

    def build_one_sangre() -> tuple[bytes, bytes, str, dict[str, str], dict[str, Any]]:
        sdata = build_sangre_data_for_mapping(master, bundle["sangre"])
        mapping = build_sangre_mapping(sdata)
        dx = _sangre_docx_bytes(mapping)
        stem_s = safe_file_stem("Examen de Sangre", str(master.get("nombres")), str(master.get("apellidos")))
        pdf_b = docx_bytes_to_pdf_bytes(dx, pdf_stem=stem_s)
        return dx, pdf_b, stem_s, mapping, sdata

    def hist_payload(exam_k: str, mapping: dict[str, str]) -> dict[str, Any]:
        return {
            "tipo": exam_k,
            "formulario_maestro": {**raw, **ident},
            "formato_descarga": want,
            "alcance": scope,
            "seed_clinico": clin_seed,
            "bundle_clinico": bundle,
            "placeholders": mapping,
            "identificadores": ident,
        }

    try:
        if scope == "orina":
            docx_b, pdf_b, stem, mapping, _extra = build_one_orina()
            docx_fn = f"{stem}.docx"
            pdf_fn = f"{stem}.pdf"
            rid = _persist_export(
                exam_type="orina",
                patient_display_name=pac,
                payload=hist_payload("orina", mapping),
                docx_bytes=docx_b,
                pdf_bytes=pdf_b,
                docx_download=docx_fn,
                pdf_download=pdf_fn,
            )
            artifact = docx_b if want == "docx" else pdf_b
            out_name = docx_fn if want == "docx" else pdf_fn
            mime = (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                if want == "docx"
                else "application/pdf"
            )
        elif scope == "sangre":
            docx_b, pdf_b, stem, mapping, _extra = build_one_sangre()
            docx_fn = f"{stem}.docx"
            pdf_fn = f"{stem}.pdf"
            rid = _persist_export(
                exam_type="sangre",
                patient_display_name=mapping["{paciente_nombre_completo}"],
                payload=hist_payload("sangre", mapping),
                docx_bytes=docx_b,
                pdf_bytes=pdf_b,
                docx_download=docx_fn,
                pdf_download=pdf_fn,
            )
            artifact = docx_b if want == "docx" else pdf_b
            out_name = docx_fn if want == "docx" else pdf_fn
            mime = (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                if want == "docx"
                else "application/pdf"
            )
        else:
            dx_o, pdf_o, stem_o, map_o, _ = build_one_orina()
            dx_s, pdf_s, stem_s, map_s, _ = build_one_sangre()
            docx_fn_o = f"{stem_o}.docx"
            pdf_fn_o = f"{stem_o}.pdf"
            docx_fn_s = f"{stem_s}.docx"
            pdf_fn_s = f"{stem_s}.pdf"
            rid_o = _persist_export(
                exam_type="orina",
                patient_display_name=pac,
                payload=hist_payload("orina", map_o),
                docx_bytes=dx_o,
                pdf_bytes=pdf_o,
                docx_download=docx_fn_o,
                pdf_download=pdf_fn_o,
            )
            rid_s = _persist_export(
                exam_type="sangre",
                patient_display_name=map_s["{paciente_nombre_completo}"],
                payload=hist_payload("sangre", map_s),
                docx_bytes=dx_s,
                pdf_bytes=pdf_s,
                docx_download=docx_fn_s,
                pdf_download=pdf_fn_s,
            )
            zstem = safe_file_stem("Examenes medicos", str(master.get("nombres")), str(master.get("apellidos")))
            zbuf = io.BytesIO()
            with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
                if want == "docx":
                    zf.writestr(docx_fn_o, dx_o)
                    zf.writestr(docx_fn_s, dx_s)
                else:
                    zf.writestr(pdf_fn_o, pdf_o)
                    zf.writestr(pdf_fn_s, pdf_s)
            artifact = zbuf.getvalue()
            out_name = f"{zstem}.zip"
            mime = "application/zip"
            rid = rid_o
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503

    imc_rid = _maybe_insert_imc_historial(db_path, master, ident)

    headers: dict[str, str] = {"Content-Disposition": f'attachment; filename="{out_name}"'}
    if scope == "both":
        headers["X-Examenes-Historial-Ids"] = f"{rid_o},{rid_s}"
    elif rid:
        headers["X-Examenes-Historial-Id"] = str(rid)
    if imc_rid:
        headers["X-Examenes-Imc-Historial-Id"] = str(imc_rid)
    return Response(artifact, mimetype=mime, headers=headers)


@examenes_medicos_bp.route("/api/historial/<int:rid>", methods=["GET", "DELETE"])
def api_historial_item(rid: int):
    err = _login_required_json()
    if err:
        return err
    row = get_examen_historial(str(current_app.config["DATABASE"]), rid)
    if not row:
        return jsonify({"ok": False, "error": "No encontrado."}), 404
    if not _is_admin() and int(row.user_id) != int(g.user["id"]):
        return jsonify({"ok": False, "error": "No encontrado."}), 404

    if request.method == "DELETE":
        if not _is_admin():
            return jsonify({"ok": False, "error": "Solo administradores pueden eliminar registros."}), 403
        gen = Path(current_app.config["GENERATED_DIR"]).resolve()
        for rel in (row.docx_relpath, row.pdf_relpath):
            p = resolve_generated_artifact(gen, rel)
            if p and p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass
        if not delete_examen_historial(str(current_app.config["DATABASE"]), rid):
            return jsonify({"ok": False, "error": "No se pudo eliminar."}), 500
        log_app_activity(
            str(current_app.config["DATABASE"]),
            user_id=int(g.user["id"]),
            module="examenes_medicos",
            action="eliminar_historial",
            status="ok",
            ref=str(rid),
        )
        return jsonify({"ok": True})

    try:
        payload = json.loads(row.payload_json)
    except (json.JSONDecodeError, TypeError):
        payload = {}
    gen = Path(current_app.config["GENERATED_DIR"]).resolve()
    return jsonify(
        {
            "ok": True,
            "id": row.id,
            "created_at": row.created_at,
            "exam_type": row.exam_type,
            "patient_display_name": row.patient_display_name,
            "username": row.username,
            "docx_disponible": resolve_generated_artifact(gen, row.docx_relpath) is not None,
            "pdf_disponible": resolve_generated_artifact(gen, row.pdf_relpath) is not None,
            "payload": payload,
        }
    )


@examenes_medicos_bp.route("/api/historial/<int:rid>/docx", methods=["GET"])
@_login_required_page
def download_historial_docx(rid: int):
    row = get_examen_historial(str(current_app.config["DATABASE"]), rid)
    if not row:
        abort(404)
    if not _is_admin() and int(row.user_id) != int(g.user["id"]):
        abort(404)
    gen = Path(current_app.config["GENERATED_DIR"]).resolve()
    p = resolve_generated_artifact(gen, row.docx_relpath)
    if not p:
        abort(404)
    name = row.docx_download_name or p.name
    return send_file(p, as_attachment=True, download_name=name)


@examenes_medicos_bp.route("/api/historial/<int:rid>/pdf", methods=["GET"])
@_login_required_page
def download_historial_pdf(rid: int):
    row = get_examen_historial(str(current_app.config["DATABASE"]), rid)
    if not row:
        abort(404)
    if not _is_admin() and int(row.user_id) != int(g.user["id"]):
        abort(404)
    gen = Path(current_app.config["GENERATED_DIR"]).resolve()
    p = resolve_generated_artifact(gen, row.pdf_relpath)
    if not p:
        abort(404)
    name = row.pdf_download_name or p.name
    return send_file(p, as_attachment=True, download_name=name)


def register_examenes_medicos(app):
    ensure_examenes_medicos_tables_path(str(app.config["DATABASE"]))
    app.register_blueprint(examenes_medicos_bp)
