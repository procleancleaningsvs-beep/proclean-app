from __future__ import annotations

import json
from functools import wraps
from io import BytesIO
from typing import Any, Callable

from flask import (
    Response,
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

from modules.nomina.banorte.csrf import issue_csrf_token, require_csrf
from modules.nomina.banorte.export_service import (
    DraftPaymentRow,
    ExportBlockedError,
    generate_export,
    get_export_blob,
)
from modules.nomina.banorte.import_service import (
    import_nomina_banorte_xlsx,
    import_reporte_detallado_xlsx,
)
from modules.nomina.banorte.matching_service import match_name, save_alias
from modules.nomina.banorte.paste_service import parse_paste_lists
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.roles_access import NOMINA_DASHBOARD_ROLES

_NO_STORE = {"Cache-Control": "private, no-store"}


def _current_role() -> str:
    user = g.get("user")
    if user is None:
        return ""
    if isinstance(user, dict):
        return str(user.get("role") or "")
    try:
        return str(user["role"])
    except Exception:
        return str(getattr(user, "role", "") or "")


def _banorte_access_required(view: Callable):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        if _current_role() not in NOMINA_DASHBOARD_ROLES:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _db_path() -> str:
    return str(current_app.config["DATABASE"])


def _username() -> str:
    user = g.get("user")
    if isinstance(user, dict):
        return str(user.get("username") or user.get("id") or "user")
    try:
        return str(user["username"])
    except Exception:
        return "user"


def register_banorte_routes(bp) -> None:
    @_banorte_access_required
    def banorte_index():
        conn = connect(_db_path())
        try:
            ensure_banorte_tables(conn)
            conn.commit()
            beneficiaries = conn.execute(
                """
                SELECT id, nombre_original, employee_number_effective, account_number,
                       validation_status, record_status, curp
                FROM nomina_banorte_beneficiaries
                ORDER BY id DESC LIMIT 200
                """
            ).fetchall()
            exports = conn.execute(
                """
                SELECT id, filename, layout_date, consecutive, payment_count, total_cents,
                       created_at, created_by, file_sha256
                FROM nomina_banorte_exports
                ORDER BY id DESC LIMIT 50
                """
            ).fetchall()
        finally:
            conn.close()
        resp = Response(
            render_template(
                "nomina/exportaciones_banorte.html",
                beneficiaries=beneficiaries,
                exports=exports,
                csrf_token=issue_csrf_token(),
            )
        )
        resp.headers.update(_NO_STORE)
        return resp

    @_banorte_access_required
    def banorte_import_altas():
        require_csrf()
        f = request.files.get("file")
        if f is None:
            flash("Archivo requerido.", "error")
            return redirect(url_for("nomina.banorte_index"))
        confirm = (request.form.get("reimport_confirmed") or "") == "1"
        result = import_nomina_banorte_xlsx(
            _db_path(),
            f.read(),
            f.filename or "upload.xlsx",
            _username(),
            reimport_confirmed=confirm,
        )
        if not result.mutated:
            flash("Mismo archivo (SHA) ya importado. Confirme reimportación.", "warning")
        else:
            flash(
                f"Importación ALTAS OK. EXITOSO={result.count_exitosos} manuales={result.count_manuales} "
                f"FALLIDOS hoja={result.count_excluidos_hoja_fallidos_total} "
                f"({result.count_fallidos_estatus}+{result.count_fallidos_hoja_sin_estatus}).",
                "success",
            )
        return redirect(url_for("nomina.banorte_index"))

    @_banorte_access_required
    def banorte_import_reporte():
        require_csrf()
        f = request.files.get("file")
        if f is None:
            flash("Archivo requerido.", "error")
            return redirect(url_for("nomina.banorte_index"))
        confirm = (request.form.get("reimport_confirmed") or "") == "1"
        result = import_reporte_detallado_xlsx(
            _db_path(),
            f.read(),
            f.filename or "reporte.xlsx",
            _username(),
            reimport_confirmed=confirm,
        )
        if not result.mutated:
            flash("Mismo reporte (SHA) ya importado. Confirme reimportación.", "warning")
        else:
            flash(f"Reporte importado. EXITOSO={result.count_exitosos}.", "success")
        return redirect(url_for("nomina.banorte_index"))

    @_banorte_access_required
    def banorte_paste():
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        parsed = parse_paste_lists(data.get("names") or "", data.get("amounts") or "")
        matches = []
        for row in parsed.rows:
            if row.raw_name and str(row.raw_name).strip():
                m = match_name(_db_path(), str(row.raw_name))
                matches.append(
                    {
                        "position": row.position,
                        "kind": m.kind,
                        "auto_selected": m.auto_selected,
                        "selected_id": m.selected_id,
                        "message": m.message,
                        "candidates": [c.__dict__ for c in m.candidates],
                    }
                )
            else:
                matches.append({"position": row.position, "kind": "NONE", "auto_selected": False})
        return jsonify(
            {
                "ok": True,
                "length_mismatch": parsed.length_mismatch,
                "warning": parsed.warning,
                "name_headers": parsed.name_headers_detected,
                "amount_headers": parsed.amount_headers_detected,
                "rows": [
                    {
                        "position": r.position,
                        "raw_name": r.raw_name,
                        "raw_amount": r.raw_amount,
                        "incomplete": r.incomplete,
                        "amount_ok": bool(r.amount_result and r.amount_result.ok),
                        "amount": str(r.amount_result.amount) if r.amount_result and r.amount_result.ok else None,
                        "rounded": bool(r.amount_result.rounded) if r.amount_result else False,
                    }
                    for r in parsed.rows
                ],
                "matches": matches,
                "csrf_token": issue_csrf_token(),
            }
        )

    @_banorte_access_required
    def banorte_alias():
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        alias_id = save_alias(
            _db_path(),
            str(data.get("alias") or ""),
            int(data["beneficiary_id"]),
            _username(),
        )
        return jsonify({"ok": True, "alias_id": alias_id, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_export_generate():
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        drafts = []
        for row in data.get("rows") or []:
            drafts.append(
                DraftPaymentRow(
                    position=int(row["position"]),
                    nombre_recibido=str(row.get("nombre_recibido") or ""),
                    beneficiary_id=int(row["beneficiary_id"]),
                    amount_raw=str(row.get("amount_raw") or ""),
                    match_kind=str(row.get("match_kind") or "MANUAL_SELECT"),
                    alias_id=int(row["alias_id"]) if row.get("alias_id") else None,
                    client_employee_number=row.get("client_employee_number"),
                    client_account_number=row.get("client_account_number"),
                    warnings=row.get("warnings") or [],
                    user_decision=row.get("user_decision") or {},
                )
            )
        try:
            result = generate_export(
                _db_path(),
                _username(),
                drafts,
                consecutive=str(data.get("consecutive") or ""),
                layout_date=data.get("layout_date"),
                confirm_duplicate_consecutive=bool(data.get("confirm_duplicate_consecutive")),
                confirm_manuals=bool(data.get("confirm_manuals")),
                confirm_date_override=bool(data.get("confirm_date_override")),
            )
        except ExportBlockedError as exc:
            return jsonify({"ok": False, "code": exc.code, "rows": exc.rows}), 400
        return jsonify(
            {
                "ok": True,
                "export_id": result.export_id,
                "filename": result.filename,
                "sha256": result.file_sha256,
                "csrf_token": issue_csrf_token(),
            }
        )

    @_banorte_access_required
    def banorte_historial():
        conn = connect(_db_path())
        try:
            exports = conn.execute(
                """
                SELECT id, filename, layout_date, consecutive, payment_count, total_cents,
                       created_at, created_by, file_sha256
                FROM nomina_banorte_exports ORDER BY id DESC LIMIT 100
                """
            ).fetchall()
        finally:
            conn.close()
        resp = Response(
            render_template(
                "nomina/exportaciones_banorte_historial.html",
                exports=exports,
                csrf_token=issue_csrf_token(),
            )
        )
        resp.headers.update(_NO_STORE)
        return resp

    @_banorte_access_required
    def banorte_download(export_id: int):
        # Re-check role already done by decorator.
        filename, blob, _digest = get_export_blob(_db_path(), export_id)
        resp = send_file(
            BytesIO(blob),
            as_attachment=True,
            download_name=filename,
            mimetype="application/octet-stream",
        )
        resp.headers.update(_NO_STORE)
        return resp

    bp.add_url_rule(
        "/exportaciones/banorte",
        endpoint="banorte_index",
        view_func=banorte_index,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/import/altas",
        endpoint="banorte_import_altas",
        view_func=banorte_import_altas,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/import/reporte",
        endpoint="banorte_import_reporte",
        view_func=banorte_import_reporte,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/paste",
        endpoint="banorte_paste",
        view_func=banorte_paste,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/aliases",
        endpoint="banorte_alias",
        view_func=banorte_alias,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/export/generate",
        endpoint="banorte_export_generate",
        view_func=banorte_export_generate,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/historial",
        endpoint="banorte_historial",
        view_func=banorte_historial,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/historial/<int:export_id>/download",
        endpoint="banorte_download",
        view_func=banorte_download,
        methods=["GET"],
    )
