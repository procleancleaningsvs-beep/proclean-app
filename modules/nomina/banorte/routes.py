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

from modules.nomina.banorte.beneficiary_service import (
    BeneficiaryError,
    create_manual_beneficiary,
    list_beneficiaries,
    replace_beneficiary,
    replacement_history,
    search_by_account,
    search_by_name,
)
from modules.nomina.banorte.calculo_adapter import build_draft_rows_from_calculo, origin_hash_for_manual_capture
from modules.nomina.banorte.calculo_queries import get_calculo_run_readonly, list_exportable_calculo_runs
from modules.nomina.banorte.csrf import issue_csrf_token, require_csrf
from modules.nomina.banorte.draft_repository import (
    DraftConflictError,
    DraftStaleError,
    abandon_draft,
    apply_draft_row,
    create_draft_from_adapter,
    create_manual_draft_shell,
    exclude_draft_row,
    find_open_manual_draft,
    get_draft,
    reorder_draft_rows,
    restore_last_excluded,
    save_draft_rows,
)
from modules.nomina.banorte.export_service import (
    DraftPaymentRow,
    ExportBlockedError,
    generate_export,
    generate_from_persistent_draft,
    get_export_blob,
    normalize_consecutive,
    resolve_layout_date_monterrey,
)
from modules.nomina.banorte.excel_nomina_service import (
    ExcelNominaError,
    inspect_excel,
    prepare_excel_draft,
    preview_excel,
)
from modules.nomina.banorte.import_service import (
    import_nomina_banorte_xlsx,
    import_reporte_detallado_xlsx,
)
from modules.nomina.banorte.matching_service import match_name, save_alias
from modules.nomina.banorte.paste_service import parse_paste_lists
from modules.nomina.banorte.prepare_service import prepare_draft_rows
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


def _json_no_store(payload: dict[str, Any], status: int = 200) -> Response:
    resp = jsonify(payload)
    resp.status_code = status
    resp.headers.update(_NO_STORE)
    return resp


def _stale_response(exc: DraftStaleError) -> Response:
    return _json_no_store(
        {
            "ok": False,
            "code": "draft_stale",
            "draft_id": exc.draft_id,
            "current_revision": exc.current_revision,
        },
        409,
    )


def register_banorte_routes(bp) -> None:
    @_banorte_access_required
    def banorte_index():
        runs = list_exportable_calculo_runs(_db_path(), limit=20, offset=0)
        conn = connect(_db_path())
        try:
            ensure_banorte_tables(conn)
            exports = conn.execute(
                """
                SELECT e.id, e.filename, e.layout_date, e.consecutive, e.payment_count, e.total_cents,
                       e.created_at, e.created_by, e.capture_origin, e.calculo_id, e.draft_id,
                       r.fecha_inicio, r.fecha_fin, r.cliente AS calculo_cliente
                FROM nomina_banorte_exports e
                LEFT JOIN nomina_calculo_runs r ON r.id = e.calculo_id
                ORDER BY e.id DESC LIMIT 100
                """
            ).fetchall()
            historial = [dict(e) for e in exports]
            benef_listing = list_beneficiaries(_db_path(), page=1, page_size=50)
        finally:
            conn.close()
        _, application_date_display = resolve_layout_date_monterrey()
        resp = Response(
            render_template(
                "nomina/exportaciones_banorte.html",
                runs=runs,
                historial=historial,
                benef_listing=benef_listing,
                application_date_display=application_date_display,
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
                f"Importación ALTAS OK. EXITOSO={result.count_exitosos} manuales={result.count_manuales}.",
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
        return _json_no_store(
            {
                "ok": True,
                "length_mismatch": parsed.length_mismatch,
                "warning": parsed.warning,
                "rows": [
                    {
                        "position": r.position,
                        "raw_name": r.raw_name,
                        "raw_amount": r.raw_amount,
                        "incomplete": r.incomplete,
                        "amount_ok": bool(r.amount_result and r.amount_result.ok),
                        "amount": str(r.amount_result.amount) if r.amount_result and r.amount_result.ok else None,
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
        return _json_no_store({"ok": True, "alias_id": alias_id, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_export_generate():
        """Legacy paste-path generate (compat). Prefer draft generate endpoint."""
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
                capture_origin="PASTE_LISTS",
            )
        except ExportBlockedError as exc:
            return _json_no_store({"ok": False, "code": exc.code, "rows": exc.rows}, 400)
        return _json_no_store(
            {
                "ok": True,
                "export_id": result.export_id,
                "filename": result.filename,
                "sha256": result.file_sha256,
                "csrf_token": issue_csrf_token(),
            }
        )

    @_banorte_access_required
    def banorte_draft_from_calculo(calculo_id: int):
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        try:
            adapted = build_draft_rows_from_calculo(_db_path(), int(calculo_id))
        except KeyError:
            return _json_no_store({"ok": False, "code": "calculo_not_found"}, 404)
        except ValueError as exc:
            return _json_no_store({"ok": False, "code": str(exc)}, 400)
        draft = create_draft_from_adapter(_db_path(), _username(), adapted)
        prepared = prepare_draft_rows(_db_path(), draft["rows"], origin_kind="CALCULO_RUN")
        draft = save_draft_rows(_db_path(), int(draft["id"]), _username(), int(draft["revision"]), prepared)
        return _json_no_store(
            {
                "ok": True,
                "draft": draft,
                "omitted": list(getattr(adapted, "omitted", []) or []),
                "amount_errors": list(getattr(adapted, "amount_errors", []) or []),
                "csrf_token": issue_csrf_token(),
            }
        )

    @_banorte_access_required
    def banorte_draft_get(draft_id: int):
        draft = get_draft(_db_path(), int(draft_id))
        if draft is None:
            return _json_no_store({"ok": False, "code": "draft_not_found"}, 404)
        if draft["created_by"] != _username() and _current_role() != "admin":
            # admin may view; nomina only own — keep simple: same roles share module
            pass
        return _json_no_store({"ok": True, "draft": draft, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_draft_save(draft_id: int):
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        try:
            draft = save_draft_rows(
                _db_path(),
                int(draft_id),
                _username(),
                int(data.get("expected_revision")),
                list(data.get("rows") or []),
                consecutive_pref=data.get("consecutive_pref"),
            )
        except DraftStaleError as exc:
            return _stale_response(exc)
        except ValueError as exc:
            return _json_no_store({"ok": False, "code": str(exc)}, 400)
        return _json_no_store({"ok": True, "draft": draft, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_draft_reorder(draft_id: int):
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        try:
            draft = reorder_draft_rows(
                _db_path(),
                int(draft_id),
                _username(),
                int(data.get("expected_revision")),
                [int(x) for x in (data.get("ordered_row_ids") or [])],
            )
        except DraftStaleError as exc:
            return _stale_response(exc)
        except ValueError as exc:
            return _json_no_store({"ok": False, "code": str(exc)}, 400)
        return _json_no_store({"ok": True, "draft": draft, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_draft_abandon(draft_id: int):
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        if not data.get("confirm"):
            return _json_no_store({"ok": False, "code": "confirm_required"}, 400)
        try:
            draft = abandon_draft(
                _db_path(),
                int(draft_id),
                _username(),
                int(data.get("expected_revision")),
            )
        except DraftStaleError as exc:
            return _stale_response(exc)
        return _json_no_store({"ok": True, "draft": draft, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_draft_generate(draft_id: int):
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        try:
            result = generate_from_persistent_draft(
                _db_path(),
                _username(),
                int(draft_id),
                expected_revision=int(data.get("expected_revision")),
                consecutive=str(data.get("consecutive") or ""),
                layout_date=data.get("layout_date"),
                confirm_duplicate_consecutive=bool(data.get("confirm_duplicate_consecutive")),
                confirm_manuals=bool(data.get("confirm_manuals")),
                confirm_date_override=bool(data.get("confirm_date_override")),
            )
        except DraftStaleError as exc:
            return _stale_response(exc)
        except ExportBlockedError as exc:
            payload: dict[str, Any] = {"ok": False, "code": exc.code, "rows": exc.rows}
            if exc.prior_export_id is not None:
                payload["prior_export_id"] = exc.prior_export_id
            return _json_no_store(payload, 400)
        except KeyError:
            return _json_no_store({"ok": False, "code": "draft_not_found"}, 404)
        return _json_no_store(
            {
                "ok": True,
                "export_id": result.export_id,
                "filename": result.filename,
                "sha256": result.file_sha256,
                "layout_date": result.layout_date,
                "layout_date_display": result.layout_date_display,
                "csrf_token": issue_csrf_token(),
            }
        )

    @_banorte_access_required
    def banorte_draft_row_apply(draft_id: int, row_id: int):
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        try:
            bid = data.get("beneficiary_id")
            draft = apply_draft_row(
                _db_path(),
                int(draft_id),
                int(row_id),
                _username(),
                int(data.get("expected_revision")),
                beneficiary_id=int(bid) if bid is not None and bid != "" else None,
                nombre_recibido=data.get("nombre_recibido"),
                amount_final=data.get("amount_final"),
            )
        except DraftStaleError as exc:
            return _stale_response(exc)
        except ValueError as exc:
            return _json_no_store({"ok": False, "code": str(exc)}, 400)
        return _json_no_store({"ok": True, "draft": draft, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_draft_exclude_row(draft_id: int):
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        if not data.get("confirm"):
            return _json_no_store({"ok": False, "code": "confirm_required"}, 400)
        try:
            draft = exclude_draft_row(
                _db_path(),
                int(draft_id),
                int(data.get("row_id")),
                _username(),
                int(data.get("expected_revision")),
            )
        except DraftStaleError as exc:
            return _stale_response(exc)
        except ValueError as exc:
            return _json_no_store({"ok": False, "code": str(exc)}, 400)
        return _json_no_store({"ok": True, "draft": draft, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_draft_restore_last(draft_id: int):
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        try:
            draft = restore_last_excluded(
                _db_path(),
                int(draft_id),
                _username(),
                int(data.get("expected_revision")),
            )
        except DraftStaleError as exc:
            return _stale_response(exc)
        except ValueError as exc:
            return _json_no_store({"ok": False, "code": str(exc)}, 400)
        return _json_no_store({"ok": True, "draft": draft, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_draft_manual():
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        force_new = bool(data.get("force_new"))
        if force_new:
            # require prior abandon — do not auto-abandon
            existing = find_open_manual_draft(_db_path(), _username())
            if existing is not None:
                return _json_no_store(
                    {
                        "ok": False,
                        "code": "manual_open_exists",
                        "existing_draft_id": existing["id"],
                        "existing_revision": existing["revision"],
                    },
                    409,
                )
        result = create_manual_draft_shell(
            _db_path(),
            _username(),
            names_text=str(data.get("names") or ""),
            amounts_text=str(data.get("amounts") or ""),
            force_new=False,
        )
        if result.get("needs_choice"):
            return _json_no_store(
                {
                    "ok": False,
                    "code": "manual_open_exists",
                    "existing_draft_id": result["existing_draft_id"],
                    "existing_revision": result["existing_revision"],
                    "csrf_token": issue_csrf_token(),
                },
                409,
            )
        # populate rows from paste
        parsed = parse_paste_lists(data.get("names") or "", data.get("amounts") or "")
        draft = result["draft"]
        rows = []
        for i, r in enumerate(parsed.rows, start=1):
            cents = 0
            if r.amount_result and r.amount_result.ok and r.amount_result.amount is not None:
                from modules.nomina.banorte.money import to_cents

                cents = to_cents(r.amount_result.amount)
            rows.append(
                {
                    "position": i,
                    "nombre_recibido": r.raw_name or "",
                    "amount_original_cents": max(0, cents),
                    "amount_final_cents": cents if cents > 0 else 0,
                    "included": 1 if cents > 0 else 0,
                    "match_kind": "NONE",
                    "row_state": "NEEDS_REVIEW" if cents > 0 else "EXCLUDED",
                    "warnings": [],
                    "user_decision": {},
                }
            )
        prepared = prepare_draft_rows(_db_path(), rows, origin_kind="MANUAL_CAPTURE")
        draft = save_draft_rows(_db_path(), int(draft["id"]), _username(), int(draft["revision"]), prepared)
        return _json_no_store({"ok": True, "draft": draft, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_excel_inspect():
        require_csrf()
        f = request.files.get("file")
        if f is None:
            return _json_no_store({"ok": False, "code": "file_required"}, 400)
        raw = f.read()
        try:
            out = inspect_excel(
                raw,
                f.filename or "upload.xlsx",
                secret_key=str(current_app.config["SECRET_KEY"]),
                user=_username(),
            )
        except ExcelNominaError as exc:
            return _json_no_store({"ok": False, "code": exc.code}, 400)
        return _json_no_store({"ok": True, **out, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_excel_preview():
        require_csrf()
        f = request.files.get("file")
        sheet = str(request.form.get("sheet") or "")
        token = str(request.form.get("token") or "")
        if f is None or not sheet or not token:
            return _json_no_store({"ok": False, "code": "missing_fields"}, 400)
        raw = f.read()
        try:
            prev = preview_excel(
                raw,
                filename=f.filename or "upload.xlsx",
                sheet=sheet,
                token=token,
                secret_key=str(current_app.config["SECRET_KEY"]),
                user=_username(),
            )
        except ExcelNominaError as exc:
            return _json_no_store({"ok": False, "code": exc.code}, 400)
        except ValueError as exc:
            return _json_no_store({"ok": False, "code": str(exc)}, 400)
        return _json_no_store({"ok": True, "preview": prev.__dict__, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_excel_prepare():
        require_csrf()
        f = request.files.get("file")
        sheet = str(request.form.get("sheet") or "")
        token = str(request.form.get("token") or "")
        if f is None or not sheet or not token:
            return _json_no_store({"ok": False, "code": "missing_fields"}, 400)
        raw = f.read()
        try:
            draft = prepare_excel_draft(
                _db_path(),
                _username(),
                raw,
                filename=f.filename or "upload.xlsx",
                sheet=sheet,
                token=token,
                secret_key=str(current_app.config["SECRET_KEY"]),
            )
        except ExcelNominaError as exc:
            return _json_no_store({"ok": False, "code": exc.code}, 400)
        except ValueError as exc:
            return _json_no_store({"ok": False, "code": str(exc)}, 400)
        return _json_no_store({"ok": True, "draft": draft, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_beneficiarios_search_name():
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        try:
            rows = search_by_name(_db_path(), str(data.get("q") or ""), limit=int(data.get("limit") or 20))
        except BeneficiaryError as exc:
            return _json_no_store({"ok": False, "code": exc.code}, 400)
        return _json_no_store({"ok": True, "rows": rows, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_beneficiarios_list_json():
        page = int(request.args.get("page") or 1)
        data = list_beneficiaries(
            _db_path(),
            page=page,
            q_name="",
            q_emp="",
            validation_status=str(request.args.get("validation_status") or ""),
            record_status=str(request.args.get("record_status") or ""),
        )
        return _json_no_store({"ok": True, "listing": data, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_beneficiarios_page():
        page = int(request.args.get("page") or 1)
        data = list_beneficiaries(
            _db_path(),
            page=page,
            q_name=str(request.args.get("q_name") or ""),
            q_emp=str(request.args.get("q_emp") or ""),
            validation_status=str(request.args.get("validation_status") or ""),
            record_status=str(request.args.get("record_status") or ""),
        )
        resp = Response(
            render_template(
                "nomina/exportaciones_banorte_beneficiarios.html",
                listing=data,
                csrf_token=issue_csrf_token(),
                q_name=request.args.get("q_name") or "",
                q_emp=request.args.get("q_emp") or "",
                validation_status=request.args.get("validation_status") or "",
                record_status=request.args.get("record_status") or "",
            )
        )
        resp.headers.update(_NO_STORE)
        return resp

    @_banorte_access_required
    def banorte_beneficiarios_search_account():
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        try:
            rows = search_by_account(_db_path(), str(data.get("account") or ""))
        except BeneficiaryError as exc:
            return _json_no_store({"ok": False, "code": exc.code}, 400)
        return _json_no_store({"ok": True, "rows": rows, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_beneficiarios_create():
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        try:
            created = create_manual_beneficiary(
                _db_path(),
                _username(),
                nombre=str(data.get("nombre") or ""),
                account=str(data.get("account") or ""),
                confirm_effective_from_account=bool(data.get("confirm_effective_from_account")),
            )
        except BeneficiaryError as exc:
            return _json_no_store({"ok": False, "code": exc.code}, 400)
        return _json_no_store({"ok": True, "beneficiary": created, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_beneficiarios_replace(beneficiary_id: int):
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        try:
            out = replace_beneficiary(
                _db_path(),
                _username(),
                int(beneficiary_id),
                nombre=data.get("nombre"),
                account=data.get("account"),
                employee_number_effective=data.get("employee_number_effective"),
                reason=str(data.get("reason") or ""),
            )
        except BeneficiaryError as exc:
            return _json_no_store({"ok": False, "code": exc.code}, 400)
        return _json_no_store({"ok": True, "beneficiary": out, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_beneficiarios_history(beneficiary_id: int):
        chain = replacement_history(_db_path(), int(beneficiary_id))
        return _json_no_store({"ok": True, "chain": chain, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_historial():
        conn = connect(_db_path())
        try:
            ensure_banorte_tables(conn)
            exports = conn.execute(
                """
                SELECT e.id, e.filename, e.layout_date, e.consecutive, e.payment_count, e.total_cents,
                       e.created_at, e.created_by, e.file_sha256, e.capture_origin, e.calculo_id, e.draft_id,
                       r.fecha_inicio, r.fecha_fin, r.cliente AS calculo_cliente
                FROM nomina_banorte_exports e
                LEFT JOIN nomina_calculo_runs r ON r.id = e.calculo_id
                ORDER BY e.id DESC LIMIT 100
                """
            ).fetchall()
            # prior counts per calculo
            enriched = []
            for e in exports:
                d = dict(e)
                if d.get("calculo_id"):
                    n = conn.execute(
                        "SELECT COUNT(*) AS c FROM nomina_banorte_exports WHERE calculo_id=?",
                        (int(d["calculo_id"]),),
                    ).fetchone()
                    d["same_calculo_export_count"] = int(n["c"])
                else:
                    d["same_calculo_export_count"] = 0
                enriched.append(d)
        finally:
            conn.close()
        resp = Response(
            render_template(
                "nomina/exportaciones_banorte_historial.html",
                exports=enriched,
                csrf_token=issue_csrf_token(),
            )
        )
        resp.headers.update(_NO_STORE)
        return resp

    @_banorte_access_required
    def banorte_download(export_id: int):
        filename, blob, _digest = get_export_blob(_db_path(), export_id)
        resp = send_file(
            BytesIO(blob),
            as_attachment=True,
            download_name=filename,
            mimetype="application/octet-stream",
        )
        resp.headers.update(_NO_STORE)
        return resp

    bp.add_url_rule("/exportaciones/banorte", endpoint="banorte_index", view_func=banorte_index, methods=["GET"])
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
    bp.add_url_rule("/exportaciones/banorte/paste", endpoint="banorte_paste", view_func=banorte_paste, methods=["POST"])
    bp.add_url_rule(
        "/exportaciones/banorte/aliases", endpoint="banorte_alias", view_func=banorte_alias, methods=["POST"]
    )
    bp.add_url_rule(
        "/exportaciones/banorte/export/generate",
        endpoint="banorte_export_generate",
        view_func=banorte_export_generate,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/drafts/from-calculo/<int:calculo_id>",
        endpoint="banorte_draft_from_calculo",
        view_func=banorte_draft_from_calculo,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/drafts/<int:draft_id>",
        endpoint="banorte_draft_get",
        view_func=banorte_draft_get,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/drafts/<int:draft_id>/save",
        endpoint="banorte_draft_save",
        view_func=banorte_draft_save,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/drafts/<int:draft_id>/reorder",
        endpoint="banorte_draft_reorder",
        view_func=banorte_draft_reorder,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/drafts/<int:draft_id>/abandon",
        endpoint="banorte_draft_abandon",
        view_func=banorte_draft_abandon,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/drafts/<int:draft_id>/generate",
        endpoint="banorte_draft_generate",
        view_func=banorte_draft_generate,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/drafts/<int:draft_id>/rows/<int:row_id>/apply",
        endpoint="banorte_draft_row_apply",
        view_func=banorte_draft_row_apply,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/drafts/<int:draft_id>/exclude-row",
        endpoint="banorte_draft_exclude_row",
        view_func=banorte_draft_exclude_row,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/drafts/<int:draft_id>/restore-last",
        endpoint="banorte_draft_restore_last",
        view_func=banorte_draft_restore_last,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/drafts/manual",
        endpoint="banorte_draft_manual",
        view_func=banorte_draft_manual,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/excel/inspect",
        endpoint="banorte_excel_inspect",
        view_func=banorte_excel_inspect,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/excel/preview",
        endpoint="banorte_excel_preview",
        view_func=banorte_excel_preview,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/excel/prepare",
        endpoint="banorte_excel_prepare",
        view_func=banorte_excel_prepare,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/beneficiarios/list",
        endpoint="banorte_beneficiarios_list_json",
        view_func=banorte_beneficiarios_list_json,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/beneficiarios/search-name",
        endpoint="banorte_beneficiarios_search_name",
        view_func=banorte_beneficiarios_search_name,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/beneficiarios",
        endpoint="banorte_beneficiarios_page",
        view_func=banorte_beneficiarios_page,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/beneficiarios/search-account",
        endpoint="banorte_beneficiarios_search_account",
        view_func=banorte_beneficiarios_search_account,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/beneficiarios/create",
        endpoint="banorte_beneficiarios_create",
        view_func=banorte_beneficiarios_create,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/beneficiarios/<int:beneficiary_id>/replace",
        endpoint="banorte_beneficiarios_replace",
        view_func=banorte_beneficiarios_replace,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/beneficiarios/<int:beneficiary_id>/history",
        endpoint="banorte_beneficiarios_history",
        view_func=banorte_beneficiarios_history,
        methods=["GET"],
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
