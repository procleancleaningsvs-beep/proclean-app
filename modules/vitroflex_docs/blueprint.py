"""Blueprint aislado: documentos Vitroflex (MEMO / CR mensual)."""

from __future__ import annotations

from functools import wraps
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    Response,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from modules.vitroflex_docs.build_document import build_cr_docx_bytes, build_memo_docx_bytes
from modules.vitroflex_docs.dates import default_fecha_linea, linea_fecha_documento
from modules.vitroflex_docs.excel_import import parse_excel_bytes
from modules.vitroflex_docs.libreoffice_pdf import docx_bytes_to_pdf_bytes, resolve_soffice_path
from modules.vitroflex_docs.template_paths import CR_DOCX, MEMO_DOCX

_BASE = Path(__file__).resolve().parent.parent.parent
_TEMPLATE_DIR = _BASE / "templates" / "vitroflex_docs"
_STATIC_DIR = _BASE / "static" / "vitroflex_docs"
_ASSETS_DIR = _STATIC_DIR / "assets"

vitroflex_bp = Blueprint(
    "vitroflex",
    __name__,
    url_prefix="/vitroflex",
    template_folder=str(_TEMPLATE_DIR),
    static_folder=str(_STATIC_DIR),
    static_url_path="assets",
)


def _vf_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


@vitroflex_bp.route("/memo", methods=["GET"])
@_vf_login_required
def memo_mensual():
    tz = ZoneInfo("America/Mexico_City")
    return render_template(
        "memo.html",
        doc_kind="memo",
        default_fecha=default_fecha_linea(),
        default_fecha_iso=datetime.now(tz).date().isoformat(),
    )


@vitroflex_bp.route("/cr", methods=["GET"])
@_vf_login_required
def cr_mensual():
    tz = ZoneInfo("America/Mexico_City")
    return render_template(
        "cr.html",
        doc_kind="cr",
        default_fecha=default_fecha_linea(),
        default_fecha_iso=datetime.now(tz).date().isoformat(),
    )


@vitroflex_bp.route("/api/import-excel", methods=["POST"])
@_vf_login_required
def api_import_excel():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No se recibió ningún archivo."}), 400
    data = f.read()
    if not data:
        return jsonify({"ok": False, "error": "Archivo vacío."}), 400
    rows, needs_defaults, err = parse_excel_bytes(data)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    return jsonify(
        {
            "ok": True,
            "rows": rows,
            "needs_default_fields": needs_defaults,
        }
    )


@vitroflex_bp.route("/plantilla/descargable", methods=["GET"])
@_vf_login_required
def descargar_plantilla():
    """Entrega 'descargable de ejemplo.xlsx' desde assets empaquetados."""
    path = _ASSETS_DIR / "descargable_de_ejemplo.xlsx"
    if not path.is_file():
        abort(404)
    return send_file(
        path,
        as_attachment=True,
        download_name="descargable de ejemplo.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _normalize_imss_digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))[:11]


def _safe_download_filename(
    name: str | None,
    *,
    default_stem: str,
    ext: str,
    max_stem: int = 160,
) -> str:
    ext_l = ext.lower()
    if not ext_l.startswith("."):
        ext_l = f".{ext_l}"
    raw = (name or "").strip() or default_stem
    stem = raw
    for e in (".pdf", ".docx"):
        if stem.lower().endswith(e):
            stem = stem[: -len(e)]
            break
    stem = re.sub(r'[<>:"/\\\\|?*\x00-\x1f]+', "_", stem).strip(" .")
    stem = stem.upper()
    if len(stem) > max_stem:
        stem = stem[:max_stem]
    if not stem:
        stem = "DOCUMENTO"
    return f"{stem}{ext_l}"


@vitroflex_bp.route("/api/status", methods=["GET"])
@_vf_login_required
def api_status():
    return jsonify(
        {
            "ok": True,
            "templates": {
                "memo_docx": MEMO_DOCX.is_file(),
                "cr_docx": CR_DOCX.is_file(),
                "memo_path": str(MEMO_DOCX),
                "cr_path": str(CR_DOCX),
            },
            "libreoffice": bool(resolve_soffice_path()),
        }
    )


@vitroflex_bp.route("/api/generate-pdf", methods=["POST"])
@_vf_login_required
def api_generate_pdf():
    """
    Genera PDF desde las plantillas DOCX oficiales (fuente de verdad).
    Cuerpo JSON: kind, fecha_texto, workers, permiso1/permiso2 (memo), planta (cr),
    filename, disposition (inline|attachment).
    """
    payload = request.get_json(silent=True) or {}
    kind = (payload.get("kind") or "").strip().lower()
    fecha_texto = linea_fecha_documento(
        fecha_iso=payload.get("fecha_iso"),
        municipio_modo=payload.get("municipio_modo"),
        municipio_otro=payload.get("municipio_otro"),
        fecha_texto_legacy=payload.get("fecha_texto"),
    )
    output = (payload.get("output_format") or payload.get("format") or "pdf").strip().lower()
    if output not in ("pdf", "docx"):
        output = "pdf"
    workers = payload.get("workers")
    if not isinstance(workers, list):
        workers = []
    workers_norm = []
    for w in workers:
        if not isinstance(w, dict):
            continue
        workers_norm.append(
            {
                "nombre": str(w.get("nombre") or ""),
                "imss": _normalize_imss_digits(w.get("imss")),
                "actividad": str(w.get("actividad") or ""),
                "tel": str(w.get("tel") or ""),
            }
        )
    filename = _safe_download_filename(
        payload.get("filename"),
        default_stem="VITROFLEX",
        ext=".pdf" if output == "pdf" else ".docx",
    )
    disp = (payload.get("disposition") or "inline").strip().lower()
    if disp not in {"inline", "attachment"}:
        disp = "inline"

    try:
        if kind == "memo":
            if not MEMO_DOCX.is_file():
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": f"No se encontró la plantilla MEMO en: {MEMO_DOCX}",
                        }
                    ),
                    400,
                )
            docx_bytes = build_memo_docx_bytes(
                fecha_texto=fecha_texto,
                permiso1_iso=(payload.get("permiso1") or None),
                permiso2_iso=(payload.get("permiso2") or None),
                workers=workers_norm,
                template_path=MEMO_DOCX,
            )
        elif kind == "cr":
            if not CR_DOCX.is_file():
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": f"No se encontró la plantilla CR en: {CR_DOCX}",
                        }
                    ),
                    400,
                )
            docx_bytes = build_cr_docx_bytes(
                fecha_texto=fecha_texto,
                planta=(payload.get("planta") or ""),
                workers=workers_norm,
                template_path=CR_DOCX,
            )
        else:
            return jsonify({"ok": False, "error": "kind debe ser memo o cr."}), 400

        if output == "pdf":
            out_bytes = docx_bytes_to_pdf_bytes(docx_bytes, suffix=kind)
            mime = "application/pdf"
        else:
            out_bytes = docx_bytes
            mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Error al generar documento: {exc}"}), 500

    cd = f'{disp}; filename="{filename}"'
    return Response(
        out_bytes,
        mimetype=mime,
        headers={"Content-Disposition": cd},
    )


def register_vitroflex(app):
    app.register_blueprint(vitroflex_bp)
