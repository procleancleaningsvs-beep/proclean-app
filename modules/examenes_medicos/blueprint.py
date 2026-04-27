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
from pypdf import PdfReader, PdfWriter

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
from modules.examenes_medicos.db import ensure_examenes_medicos_tables_path
from modules.examenes_medicos.expediente_db import (
    delete_examenes_expediente,
    ensure_examenes_expediente_table,
    get_examenes_expediente,
    list_examenes_expedientes,
    upsert_examenes_expediente_merge,
)
from modules.examenes_medicos.export_helpers import (
    app_mx_today,
    build_orina_data_for_mapping,
    build_orina_mapping,
    build_sangre_data_for_mapping,
    build_sangre_mapping,
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


def _persist_export(
    *,
    exam_type: str,
    payload: dict[str, Any],
    docx_bytes: bytes,
    pdf_bytes: bytes,
    docx_download: str,
    pdf_download: str,
    when_iso: str | None = None,
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
    db_path = str(current_app.config["DATABASE"])
    when = when_iso or _now_iso()
    fm = payload.get("formulario_maestro") if isinstance(payload.get("formulario_maestro"), dict) else {}
    ident = payload.get("identificadores") if isinstance(payload.get("identificadores"), dict) else {}
    conn = sqlite3.connect(db_path)
    try:
        ensure_examenes_expediente_table(conn)
        eid = upsert_examenes_expediente_merge(
            conn,
            user_id=int(g.user["id"]),
            master=fm,
            ident=ident,
            exam_type=exam_type,
            last_scope=str(payload.get("alcance") or "orina"),
            last_format=str(payload.get("formato_descarga") or "pdf"),
            docx_relpath=f"{rel_dir}/{docx_name}",
            pdf_relpath=f"{rel_dir}/{pdf_name}",
            docx_download_name=docx_download,
            pdf_download_name=pdf_download,
            when_iso=when,
        )
        conn.commit()
    finally:
        conn.close()
    log_app_activity(
        db_path,
        user_id=int(g.user["id"]),
        module="examenes_medicos",
        action="exportar",
        status="ok",
        ref=str(eid),
        detail=exam_type,
    )
    return eid


def _persist_export_safe(**kwargs) -> int | None:
    """Intenta guardar historial sin bloquear la descarga si falla."""
    try:
        return _persist_export(**kwargs)
    except Exception:
        current_app.logger.exception(
            "examenes_medicos: fallo guardando historial exam_type=%s",
            kwargs.get("exam_type"),
        )
        return None


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


def _sangre_pdf_fix_pagination(pdf_bytes: bytes) -> bytes:
    """
    Corrige caso observado de página intermedia casi vacía en salida de sangre.
    Si detecta patrón de 4 páginas con la 2 sin bloque analítico, descarta esa página.
    """
    try:
        rd = PdfReader(io.BytesIO(pdf_bytes))
    except Exception:
        return pdf_bytes
    if len(rd.pages) != 4:
        return pdf_bytes
    p2 = (rd.pages[1].extract_text() or "").lower()
    has_content = any(
        k in p2
        for k in (
            "neutrófilos",
            "neutrofilos",
            "monocitos",
            "química clínica",
            "quimica clinica",
            "glucosa",
            "urea",
        )
    )
    looks_orphan = ("acreditaci" in p2 and "marcados con el signo" in p2) or ("resultados" in p2 and not has_content)
    if has_content or not looks_orphan:
        return pdf_bytes

    wr = PdfWriter()
    wr.add_page(rd.pages[0])
    wr.add_page(rd.pages[2])
    wr.add_page(rd.pages[3])
    out = io.BytesIO()
    wr.write(out)
    current_app.logger.info("examenes_medicos.sangre_pdf_fix: 4->3 páginas (removida página 2 huérfana)")
    return out.getvalue()


@examenes_medicos_bp.route("/", methods=["GET"])
@_login_required_page
def index():
    y = default_yesterday_iso_mx()
    return render_template("examenes_medicos_index.html", default_fecha=y)


@examenes_medicos_bp.route("/historial", methods=["GET"])
@_login_required_page
def historial_page():
    rows = list_examenes_expedientes(
        str(current_app.config["DATABASE"]),
        user_id=None if _is_admin() else int(g.user["id"]),
    )
    return render_template("examenes_medicos_historial.html", rows=rows, is_admin=_is_admin())


@examenes_medicos_bp.route("/historial/<int:rid>", methods=["GET"])
@_login_required_page
def historial_detalle(rid: int):
    row = get_examenes_expediente(str(current_app.config["DATABASE"]), rid)
    if not row:
        abort(404)
    if not _is_admin() and int(row.user_id) != int(g.user["id"]):
        abort(404)
    gen = Path(current_app.config["GENERATED_DIR"]).resolve()
    pdf_orina = resolve_generated_artifact(gen, row.orina_pdf_relpath)
    pdf_sangre = resolve_generated_artifact(gen, row.sangre_pdf_relpath)
    return render_template(
        "examenes_medicos_detalle.html",
        row=row,
        pdf_orina_ok=pdf_orina is not None,
        pdf_sangre_ok=pdf_sangre is not None,
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
    try:
        uid_log = g.user["id"] if g.user is not None else None
    except Exception:
        uid_log = None
    current_app.logger.info(
        "examenes_medicos.download start user_id=%s scope=%s format=%s",
        uid_log,
        scope,
        want,
    )
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
        qr_code = str(sdata.get("codigo_barra") or mapping.get("{codigo_barra}") or "").strip().upper()
        stem_s = safe_file_stem(qr_code or "SANGRE")
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

    rid_o: int | None = None
    rid_s: int | None = None
    rid: int | None = None
    export_ts = _now_iso()
    try:
        if scope == "orina":
            docx_b, pdf_b, stem, mapping, _extra = build_one_orina()
            docx_fn = f"{stem}.docx"
            pdf_fn = f"{stem}.pdf"
            rid = _persist_export_safe(
                exam_type="orina",
                payload=hist_payload("orina", mapping),
                docx_bytes=docx_b,
                pdf_bytes=pdf_b,
                docx_download=docx_fn,
                pdf_download=pdf_fn,
                when_iso=export_ts,
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
            rid = _persist_export_safe(
                exam_type="sangre",
                payload=hist_payload("sangre", mapping),
                docx_bytes=docx_b,
                pdf_bytes=pdf_b,
                docx_download=docx_fn,
                pdf_download=pdf_fn,
                when_iso=export_ts,
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
            rid_o = _persist_export_safe(
                exam_type="orina",
                payload=hist_payload("orina", map_o),
                docx_bytes=dx_o,
                pdf_bytes=pdf_o,
                docx_download=docx_fn_o,
                pdf_download=pdf_fn_o,
                when_iso=export_ts,
            )
            rid_s = _persist_export_safe(
                exam_type="sangre",
                payload=hist_payload("sangre", map_s),
                docx_bytes=dx_s,
                pdf_bytes=pdf_s,
                docx_download=docx_fn_s,
                pdf_download=pdf_fn_s,
                when_iso=export_ts,
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
            rid = rid_s or rid_o
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    except Exception:
        current_app.logger.exception(
            "examenes_medicos.download fallo inesperado scope=%s format=%s payload_keys=%s",
            scope,
            want,
            sorted(list(raw.keys())),
        )
        return jsonify({"ok": False, "error": "Error interno al generar la descarga."}), 500

    headers: dict[str, str] = {"Content-Disposition": f'attachment; filename="{out_name}"'}
    exp_id = rid_s or rid_o or rid
    if exp_id:
        headers["X-Examenes-Expediente-Id"] = str(exp_id)
        headers["X-Examenes-Historial-Id"] = str(exp_id)
    return Response(artifact, mimetype=mime, headers=headers)


@examenes_medicos_bp.route("/api/historial/<int:rid>", methods=["GET", "DELETE"])
def api_historial_item(rid: int):
    err = _login_required_json()
    if err:
        return err
    db_path = str(current_app.config["DATABASE"])
    row = get_examenes_expediente(db_path, rid)
    if not row:
        return jsonify({"ok": False, "error": "No encontrado."}), 404
    if not _is_admin() and int(row.user_id) != int(g.user["id"]):
        return jsonify({"ok": False, "error": "No encontrado."}), 404

    if request.method == "DELETE":
        if not _is_admin():
            return jsonify({"ok": False, "error": "Solo administradores pueden eliminar registros."}), 403
        gen = Path(current_app.config["GENERATED_DIR"]).resolve()
        for rel in (
            row.orina_pdf_relpath,
            row.orina_docx_relpath,
            row.sangre_pdf_relpath,
            row.sangre_docx_relpath,
        ):
            if not rel:
                continue
            p = resolve_generated_artifact(gen, rel)
            if p and p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass
        conn = sqlite3.connect(db_path)
        try:
            ensure_examenes_expediente_table(conn)
            ok = delete_examenes_expediente(conn, rid)
            conn.commit()
        finally:
            conn.close()
        if not ok:
            return jsonify({"ok": False, "error": "No se pudo eliminar."}), 500
        log_app_activity(
            db_path,
            user_id=int(g.user["id"]),
            module="examenes_medicos",
            action="eliminar_expediente",
            status="ok",
            ref=str(rid),
        )
        return jsonify({"ok": True})

    gen = Path(current_app.config["GENERATED_DIR"]).resolve()
    return jsonify(
        {
            "ok": True,
            "id": row.id,
            "last_export_at": row.last_export_at,
            "patient_display_name": row.patient_display_name,
            "cliente_numero": row.cliente_numero,
            "imc_label": row.imc_label,
            "username": row.username,
            "pdf_orina_disponible": resolve_generated_artifact(gen, row.orina_pdf_relpath) is not None,
            "pdf_sangre_disponible": resolve_generated_artifact(gen, row.sangre_pdf_relpath) is not None,
            "last_scope": row.last_scope,
            "last_format": row.last_format,
        }
    )


@examenes_medicos_bp.route("/api/historial/<int:rid>/pdf/<kind>", methods=["GET"])
@_login_required_page
def download_expediente_pdf(rid: int, kind: str):
    k = str(kind or "").lower().strip()
    if k not in ("orina", "sangre"):
        abort(404)
    row = get_examenes_expediente(str(current_app.config["DATABASE"]), rid)
    if not row:
        abort(404)
    if not _is_admin() and int(row.user_id) != int(g.user["id"]):
        abort(404)
    gen = Path(current_app.config["GENERATED_DIR"]).resolve()
    if k == "orina":
        rel, name = row.orina_pdf_relpath, row.orina_pdf_download_name
    else:
        rel, name = row.sangre_pdf_relpath, row.sangre_pdf_download_name
    p = resolve_generated_artifact(gen, rel)
    if not p:
        abort(404)
    dl = name or p.name
    return send_file(p, as_attachment=True, download_name=dl)


def register_examenes_medicos(app):
    ensure_examenes_medicos_tables_path(str(app.config["DATABASE"]))
    app.register_blueprint(examenes_medicos_bp)
