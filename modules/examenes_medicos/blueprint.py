"""Rutas Flask: Vitroflex > Exámenes médicos (formulario maestro)."""

from __future__ import annotations

import sqlite3
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

from modules.examenes_medicos.db import ensure_examenes_medicos_tables_path
from modules.examenes_medicos.clinical_autogen import generate_clinical_bundle
from modules.examenes_medicos.expediente_db import (
    delete_examenes_expediente,
    ensure_examenes_expediente_table,
    get_examenes_expediente,
    insert_unified_expediente,
    list_examenes_expedientes,
    update_unified_expediente_export,
)
from modules.examenes_medicos.export_helpers import (
    app_mx_today,
    default_yesterday_iso_mx,
    resolve_generated_artifact,
)
from modules.examenes_medicos.identifiers import (
    build_unified_filename_base,
    generate_unique_folio_unificado,
    generate_unique_orden_unificada,
    get_or_create_paciente_id,
    normalize_nombre_key,
)
from modules.examenes_medicos.paths import UNIFICADO_DOCX
from modules.examenes_medicos.reference_ranges import (
    GENERATED_CLINICAL_PLACEHOLDER_NAMES,
    MANUAL_CLINICAL_PLACEHOLDER_NAMES,
    clinical_form_sections,
    validate_generated_clinical_results,
    validate_manual_clinical_results,
)
from modules.examenes_medicos.unified_document import (
    UnifiedTemplateError,
    build_unified_mapping,
    render_unified_docx_bytes,
)
from modules.examenes_medicos.validation import (
    edad_desde_fecha_nacimiento,
    parse_date_iso,
    validate_positive_float,
    validate_required_non_empty,
    validate_sexo,
)
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

    return errs


def _parse_expediente_id(value: Any) -> int | None:
    try:
        n = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _prepare_unified_ids(
    conn: sqlite3.Connection,
    *,
    master: dict[str, Any],
    expediente_id: int | None,
    user_id: int,
) -> tuple[dict[str, str], int | None]:
    if expediente_id is not None:
        row = conn.execute(
            """
            SELECT paciente_id, orden, folio, filename_base
            FROM examenes_medicos_expediente
            WHERE id = ? AND user_id = ? AND last_scope = 'unificado'
            """,
            (expediente_id, user_id),
        ).fetchone()
        if row is None:
            raise ValueError("Expediente unificado no encontrado.")
        paciente_id, orden, folio, filename_base = [str(v or "") for v in row]
        if not (paciente_id and orden and folio and filename_base):
            raise ValueError("El expediente no tiene identificadores unificados completos.")
        return {
            "paciente_id": paciente_id,
            "orden": orden,
            "folio": folio,
            "filename_base": filename_base,
        }, expediente_id

    paciente_id = get_or_create_paciente_id(
        conn,
        nombres=str(master.get("nombres") or ""),
        apellidos=str(master.get("apellidos") or ""),
        fecha_nacimiento=str(master.get("fecha_nacimiento") or ""),
    )
    orden = generate_unique_orden_unificada(conn)
    folio = generate_unique_folio_unificado(conn)
    filename_base = build_unified_filename_base(orden, folio, str(master.get("fecha_nacimiento") or ""))
    return {
        "paciente_id": paciente_id,
        "orden": orden,
        "folio": folio,
        "filename_base": filename_base,
    }, None


def _persist_unified_export(
    *,
    payload: dict[str, Any],
    docx_bytes: bytes,
    pdf_bytes: bytes | None,
    docx_download: str,
    pdf_download: str | None,
    expediente_id: int | None,
    when_iso: str | None = None,
) -> int:
    gen = Path(current_app.config["GENERATED_DIR"])
    gen.mkdir(parents=True, exist_ok=True)
    db_path = str(current_app.config["DATABASE"])
    user_id = int(g.user["id"])
    ident = payload.get("identificadores") if isinstance(payload.get("identificadores"), dict) else {}
    filename_base = str(ident.get("filename_base") or "").strip()
    if not filename_base:
        raise RuntimeError("No existe nombre base para el expediente unificado.")

    rel_dir = f"examenes_medicos/{uuid.uuid4().hex}"
    if expediente_id is not None:
        existing = get_examenes_expediente(db_path, expediente_id)
        if existing and int(existing.user_id) == user_id:
            existing_rel = existing.sangre_docx_relpath or existing.sangre_pdf_relpath
            if existing_rel and "/" in existing_rel:
                rel_dir = existing_rel.rsplit("/", 1)[0]

    folder = gen / rel_dir
    folder.mkdir(parents=True, exist_ok=True)
    docx_name = f"{filename_base}.docx"
    pdf_name = f"{filename_base}.pdf"
    docx_path = folder / docx_name
    pdf_path = folder / pdf_name
    written: list[Path] = []
    preexisting = {p for p in (docx_path, pdf_path) if p.exists()}
    when = when_iso or _now_iso()
    fm = payload.get("formulario_maestro") if isinstance(payload.get("formulario_maestro"), dict) else {}
    conn = sqlite3.connect(db_path)
    try:
        docx_path.write_bytes(docx_bytes)
        written.append(docx_path)
        pdf_relpath = None
        if pdf_bytes is not None:
            pdf_path.write_bytes(pdf_bytes)
            written.append(pdf_path)
            pdf_relpath = f"{rel_dir}/{pdf_name}"

        ensure_examenes_expediente_table(conn)
        if expediente_id is None:
            eid = insert_unified_expediente(
                conn,
                user_id=user_id,
                master=fm,
                ident=ident,
                last_format=str(payload.get("formato_descarga") or "pdf"),
                docx_relpath=f"{rel_dir}/{docx_name}",
                pdf_relpath=pdf_relpath,
                docx_download_name=docx_download,
                pdf_download_name=pdf_download,
                when_iso=when,
                template_name=UNIFICADO_DOCX.name,
            )
        else:
            eid = expediente_id
            update_unified_expediente_export(
                conn,
                expediente_id=eid,
                user_id=user_id,
                last_format=str(payload.get("formato_descarga") or "pdf"),
                docx_relpath=f"{rel_dir}/{docx_name}",
                pdf_relpath=pdf_relpath,
                docx_download_name=docx_download,
                pdf_download_name=pdf_download,
                when_iso=when,
                template_name=UNIFICADO_DOCX.name,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        for path in written:
            if path in preexisting:
                continue
            try:
                path.unlink()
            except OSError:
                pass
        raise
    finally:
        conn.close()
    log_app_activity(
        db_path,
        user_id=user_id,
        module="examenes_medicos",
        action="exportar",
        status="ok",
        ref=str(eid),
        detail="unificado",
    )
    return eid


def _unificado_docx_bytes(mapping: dict[str, str]) -> bytes:
    if not UNIFICADO_DOCX.is_file():
        raise FileNotFoundError(str(UNIFICADO_DOCX))
    raw = UNIFICADO_DOCX.read_bytes()
    return render_unified_docx_bytes(raw, mapping)


@examenes_medicos_bp.route("/", methods=["GET"])
@_login_required_page
def index():
    y = default_yesterday_iso_mx()
    return render_template(
        "examenes_medicos_index.html",
        default_fecha=y,
        clinical_sections=clinical_form_sections(),
    )


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
    return jsonify({"ok": False, "error": "La captura clínica ahora es manual y validada."}), 410


def _form_dict_clean(data: dict[str, Any]) -> dict[str, Any]:
    skip = frozenset({"target", "format", "scope", "confirmar_generacion", "expediente_id"})
    return {k: v for k, v in data.items() if k not in skip}


def _is_confirmed(value: Any) -> bool:
    if value is True:
        return True
    return str(value or "").strip().lower() in {"1", "true", "si", "sí", "yes"}


def _history_master_payload(raw: dict[str, Any], ident: dict[str, str]) -> dict[str, Any]:
    keys = (
        "nombres",
        "apellidos",
        "fecha_nacimiento",
        "edad",
        "sexo",
        "fecha_estudio",
        "fecha_toma",
        "hora_toma",
        "fecha_val",
        "hora_val",
    )
    return {**{k: raw.get(k) for k in keys if k in raw}, **ident}


def _generated_clinical_from_bundle(bundle: dict[str, Any]) -> dict[str, str]:
    raw = bundle.get("unificado") if isinstance(bundle, dict) else None
    if not isinstance(raw, dict):
        raise ValueError("El generador clinico no devolvio resultados unificados.")
    return {name: str(raw[name]).strip() if name in raw else "" for name in GENERATED_CLINICAL_PLACEHOLDER_NAMES}


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
    fnac = str(data.get("fecha_nacimiento") or "").strip()[:10]
    db_path = str(current_app.config["DATABASE"])
    conn = sqlite3.connect(db_path)
    try:
        row = None
        if fnac:
            from modules.examenes_medicos.identifiers import normalize_patient_identity_key

            key = normalize_patient_identity_key(n, a, fnac)
            row = conn.execute(
                "SELECT paciente_id FROM examenes_medicos_paciente_ids WHERE patient_identity_key = ?",
                (key,),
            ).fetchone()
    finally:
        conn.close()
    return jsonify(
        {
            "ok": True,
            "paciente_id_existente": str(row[0]) if row else None,
            "nota_identificadores": "Paciente ID se reutiliza por nombre y fecha de nacimiento; orden y folio se asignan al generar un expediente nuevo.",
        }
    )


@examenes_medicos_bp.route("/api/master/download", methods=["POST"])
def api_master_download():
    err = _login_required_json()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    if not _is_confirmed(data.get("confirmar_generacion")):
        return jsonify({"ok": False, "error": "Confirme la generacion del documento."}), 400

    raw = _form_dict_clean(data)
    errs = _errors_master_form(raw)
    errs.extend(validate_manual_clinical_results(raw))
    if errs:
        return jsonify({"ok": False, "errors": errs}), 400

    raw_scope = str(data.get("scope") or "unificado").strip().lower()
    if raw_scope == "both":
        scope = "unificado"
    else:
        scope = raw_scope
    if scope != "unificado":
        return jsonify({"ok": False, "error": "scope debe ser unificado."}), 400
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

    try:
        clinical_bundle = generate_clinical_bundle(sexo=str(master_base.get("sexo") or ""))
        generated_clinical = _generated_clinical_from_bundle(clinical_bundle)
    except Exception:
        current_app.logger.exception("examenes_medicos: generador clinico fallo")
        return jsonify({"ok": False, "error": "No se pudieron generar resultados clinicos validos."}), 500

    clinical_payload = {
        **generated_clinical,
        **{name: raw.get(name) for name in MANUAL_CLINICAL_PLACEHOLDER_NAMES},
    }
    generated_errors = validate_generated_clinical_results(clinical_payload)
    if generated_errors:
        return jsonify({"ok": False, "errors": generated_errors}), 500

    db_path = str(current_app.config["DATABASE"])
    expediente_id = _parse_expediente_id(data.get("expediente_id"))
    conn = sqlite3.connect(db_path)
    try:
        ensure_examenes_expediente_table(conn)
        ident, expediente_id = _prepare_unified_ids(
            conn,
            master=master_base,
            expediente_id=expediente_id,
            user_id=int(g.user["id"]),
        )
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    master = {**raw, **master_base, **clinical_payload, **ident}
    mapping = build_unified_mapping(master)

    def hist_payload() -> dict[str, Any]:
        return {
            "tipo": "unificado",
            "formulario_maestro": _history_master_payload(master_base, ident),
            "formato_descarga": want,
            "alcance": scope,
            "plantilla": UNIFICADO_DOCX.name,
            "placeholders_count": len(mapping),
            "identificadores": ident,
        }

    rid: int | None = None
    export_ts = _now_iso()
    try:
        docx_b = _unificado_docx_bytes(mapping)
        if not docx_b:
            return jsonify({"ok": False, "error": "El documento generado esta vacio."}), 500
        stem = ident["filename_base"]
        pdf_b: bytes | None = None
        if want == "pdf":
            pdf_b = docx_bytes_to_pdf_bytes(docx_b, pdf_stem=stem)
            if not pdf_b:
                return jsonify({"ok": False, "error": "El PDF generado esta vacio."}), 500
        docx_fn = f"{stem}.docx"
        pdf_fn = f"{stem}.pdf"
        rid = _persist_unified_export(
            payload=hist_payload(),
            docx_bytes=docx_b,
            pdf_bytes=pdf_b,
            docx_download=docx_fn,
            pdf_download=pdf_fn if pdf_b is not None else None,
            expediente_id=expediente_id,
            when_iso=export_ts,
        )
        artifact = docx_b if want == "docx" else pdf_b
        out_name = docx_fn if want == "docx" else pdf_fn
        mime = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if want == "docx"
            else "application/pdf"
        )
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    except UnifiedTemplateError as exc:
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
    exp_id = rid
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
    if k == "unificado":
        k = "sangre"
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
