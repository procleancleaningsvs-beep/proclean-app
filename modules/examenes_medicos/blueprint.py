"""Rutas Flask: Vitroflex > Exámenes médicos (formulario maestro)."""

from __future__ import annotations

import json
import random
import uuid
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
from modules.examenes_medicos.paths import ORINA_DOCX, SANGRE_DOCX
from modules.examenes_medicos.validation import (
    classify_imc,
    edad_desde_fecha_nacimiento,
    parse_date_iso,
    validate_cliente_numero,
    validate_codigo_barra,
    validate_folio_orina,
    validate_folio_sangre,
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


def _errors_master(data: dict[str, Any], *, require_imc_body: bool) -> list[str]:
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

    for fn, key in (
        (validate_folio_orina, "folio_orina"),
        (validate_folio_sangre, "folio_sangre"),
        (validate_cliente_numero, "cliente_numero"),
        (validate_codigo_barra, "codigo_barra"),
    ):
        e = fn(data.get(key))
        if e:
            errs.append(e)

    if require_imc_body:
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


@examenes_medicos_bp.route("/api/master/export", methods=["POST"])
def api_master_export():
    err = _login_required_json()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    target = str(data.get("target") or "").strip().lower()
    if target not in ("orina", "sangre"):
        return jsonify({"ok": False, "error": "target debe ser orina o sangre."}), 400

    errs = _errors_master(data, require_imc_body=False)
    if errs:
        return jsonify({"ok": False, "errors": errs}), 400

    want = str(data.get("format") or "pdf").lower().strip()
    if want not in ("docx", "pdf"):
        return jsonify({"ok": False, "error": "format debe ser docx o pdf."}), 400

    master = _normalize_master(data)
    raw_seed = data.get("seed_clinico")
    try:
        clin_seed: int | None = int(raw_seed) if raw_seed is not None and str(raw_seed).strip() != "" else None
    except (TypeError, ValueError):
        clin_seed = None
    if clin_seed is None:
        clin_seed = random.randrange(0, 2**31)

    bundle = generate_clinical_bundle(sexo=str(master.get("sexo") or ""), seed=clin_seed)

    try:
        if target == "orina":
            odata = build_orina_data_for_mapping(master, bundle["orina"])
            mapping = build_orina_mapping(odata)
            docx_b = _orina_docx_bytes(mapping)
            stem = safe_file_stem("Examen de Orina", str(master.get("nombres")), str(master.get("apellidos")))
            exam_type = "orina"
        else:
            sdata = build_sangre_data_for_mapping(master, bundle["sangre"])
            mapping = build_sangre_mapping(sdata)
            docx_b = _sangre_docx_bytes(mapping)
            stem = safe_file_stem("Examen de Sangre", str(master.get("nombres")), str(master.get("apellidos")))
            exam_type = "sangre"
        pdf_b = docx_bytes_to_pdf_bytes(docx_b, pdf_stem=stem)
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503

    docx_fn = f"{stem}.docx"
    pdf_fn = f"{stem}.pdf"
    pac = mapping["{paciente_nombre_completo}"]
    snap_master = {k: data.get(k) for k in data if k not in ("format", "target")}
    payload = {
        "tipo": exam_type,
        "formulario_maestro": snap_master,
        "seed_clinico": clin_seed,
        "bundle_clinico": bundle,
        "placeholders": mapping,
    }
    rid = _persist_export(
        exam_type=exam_type,
        patient_display_name=pac,
        payload=payload,
        docx_bytes=docx_b,
        pdf_bytes=pdf_b,
        docx_download=docx_fn,
        pdf_download=pdf_fn,
    )

    if want == "docx":
        body, mime, fn = docx_b, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", docx_fn
    else:
        body, mime, fn = pdf_b, "application/pdf", pdf_fn
    return Response(
        body,
        mimetype=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{fn}"',
            "X-Examenes-Historial-Id": str(rid),
        },
    )


@examenes_medicos_bp.route("/api/imc/registro", methods=["POST"])
def api_imc_registro():
    err = _login_required_json()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    errs = _errors_master(data, require_imc_body=True)
    if errs:
        return jsonify({"ok": False, "errors": errs}), 400

    master = _normalize_master(data)
    peso, _ = validate_positive_float(data.get("peso_kg"), "Peso (kg)")
    est, _ = validate_positive_float(data.get("estatura_m"), "Estatura (m)")
    assert peso is not None and est is not None
    imc = peso / (est**2)
    clas = classify_imc(imc)
    pac = build_paciente_orina(str(master.get("nombres") or ""), str(master.get("apellidos") or ""))
    snap = {
        "tipo": "imc",
        "formulario_maestro": {k: data.get(k) for k in data},
        "valores": {"peso_kg": peso, "estatura_m": est, "imc": round(imc, 2), "clasificacion": clas},
    }
    rid = insert_examen_historial(
        str(current_app.config["DATABASE"]),
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
        str(current_app.config["DATABASE"]),
        user_id=int(g.user["id"]),
        module="examenes_medicos",
        action="imc",
        status="ok",
        ref=str(rid),
    )
    return jsonify({"ok": True, "id": rid, "imc": round(imc, 2), "clasificacion": clas})


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
