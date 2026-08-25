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

from modules.nomina.banorte.batch_service import (
    BatchStaleError,
    abandon_batch,
    add_batch_row,
    confirm_batch,
    create_batch,
    delete_batch_row,
    get_batch,
    prepare_reporte_batch,
)
from modules.nomina.banorte.beneficiary_service import (
    BeneficiaryError,
    apply_beneficiary_action,
    beneficiary_action_message,
    beneficiary_management_detail,
    create_manual_beneficiary,
    list_beneficiaries,
    replace_beneficiary,
    search_by_account,
    search_by_name,
)
from modules.nomina.banorte.employee_number_service import list_available_employee_numbers
from modules.nomina.banorte.calculo_adapter import build_draft_rows_from_calculo, origin_hash_for_manual_capture
from modules.nomina.banorte.calculo_queries import get_calculo_run_readonly, list_exportable_calculo_runs
from modules.nomina.banorte.csrf import issue_csrf_token, require_csrf
from modules.nomina.banorte.draft_repository import (
    DraftConflictError,
    DraftStaleError,
    abandon_draft,
    add_draft_payment,
    apply_draft_row,
    create_draft_from_adapter,
    create_manual_draft_shell,
    exclude_draft_row,
    find_open_manual_draft,
    get_draft,
    reorder_draft_rows,
    save_draft_rows,
    undo_last_draft_mutation,
)
from modules.nomina.banorte.download_service import (
    ExportDownloadError,
    load_historical_pag,
)
from modules.nomina.banorte.catalog_activation import (
    CatalogActivationError,
    activate_catalog_version,
    catalog_activation_check,
    rollback_catalog_activation,
)
from modules.nomina.banorte.catalog_lifecycle import legacy_authority_allowed
from modules.nomina.banorte.catalog_search_cursor import CatalogSearchCursorError
from modules.nomina.banorte.catalog_search_service import search_catalog_sidebar
from modules.nomina.banorte.catalog_parser import CatalogParseError
from modules.nomina.banorte.catalog_reconciliation import (
    CatalogReconciliationError,
    manual_reconcile_catalog_person,
    pre_reconcile_catalog_version,
)
from modules.nomina.banorte.catalog_service import (
    CatalogVersionError,
    analyze_catalog_version,
    catalog_version_diff,
    get_catalog_version,
    list_catalog_versions,
    mark_catalog_ready_for_review,
    stage_catalog_version,
)
from modules.nomina.banorte.history_service import (
    HistoricalExportNotFound,
    build_historical_export_excel,
    load_historical_export_movements,
)
from modules.nomina.banorte.export_service import (
    DraftPaymentRow,
    ExportBlockedError,
    generate_export,
    generate_from_persistent_draft,
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
from modules.nomina.banorte.catalog_row_adapter import prepare_capture_rows
from modules.nomina.banorte.payment_authority import enforce_prepared_rows_catalog_authority
from modules.nomina.banorte.rows_capture import parse_capture_input
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


def _banorte_operator_required(view: Callable):
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


def _legacy_authority_guard() -> Response | None:
    conn = connect(_db_path())
    try:
        if not legacy_authority_allowed(conn):
            return _json_no_store({"ok": False, "code": "CATALOG_ACTIVE_REQUIRED"}, 403)
    finally:
        conn.close()
    return None


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


def _excel_human_message(code: str | None) -> str:
    mapping = {
        None: "Operación completada.",
        "file_required": "Seleccione un archivo Excel (.xlsx o .xlsm).",
        "missing_fields": "Faltan la hoja o el token de validación. Vuelva a inspeccionar el archivo.",
        "file_too_large": "El archivo supera el tamaño máximo permitido.",
        "unsupported_extension": "Solo se admiten archivos .xlsx o .xlsm.",
        "header_not_found": "No se encontró el encabezado esperado (Nombre, Banco, Neto a pagar).",
        "excel_token_expired": "La validación del archivo expiró. Inspeccione de nuevo el Excel.",
        "excel_token_sha_mismatch": "El archivo cambió después de inspeccionarlo. Vuelva a inspeccionar.",
        "excel_token_invalid": "No se pudo validar el archivo. Inspeccione de nuevo.",
    }
    return mapping.get(code or "", f"No se pudo completar la operación ({code}).")


def _excel_envelope(
    *,
    success: bool,
    data: dict[str, Any] | None = None,
    error_code: str | None = None,
    message: str | None = None,
    status: int = 200,
) -> Response:
    payload: dict[str, Any] = {
        "success": success,
        "ok": success,
        "data": data or {},
        "error_code": error_code,
        "message": message or _excel_human_message(error_code if not success else None),
        "csrf_token": issue_csrf_token(),
    }
    if data:
        # Legacy top-level keys for older clients
        for key, value in data.items():
            if key not in payload:
                payload[key] = value
    if error_code:
        payload["code"] = error_code
    return _json_no_store(payload, status)


def _stale_response(exc: DraftStaleError) -> Response:
    return _json_no_store(
        {
            "ok": False,
            "code": "draft_stale",
            "draft_id": exc.draft_id,
            "current_revision": exc.current_revision,
            "message": "El borrador cambió en otra operación. Se actualizó con la versión más reciente.",
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
            benef_listing = list_beneficiaries(
                _db_path(), scope="current", page=1, page_size=15
            )
            historical_benef_listing = list_beneficiaries(
                _db_path(), scope="historical", page=1, page_size=15
            )
        finally:
            conn.close()
        _, application_date_display = resolve_layout_date_monterrey()
        resp = Response(
            render_template(
                "nomina/exportaciones_banorte.html",
                runs=runs,
                historial=historial,
                benef_listing=benef_listing,
                historical_benef_listing=historical_benef_listing,
                application_date_display=application_date_display,
                csrf_token=issue_csrf_token(),
            )
        )
        resp.headers.update(_NO_STORE)
        return resp

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_import_altas():
        require_csrf()
        wants_json = (
            request.accept_mimetypes.best == "application/json"
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or (request.headers.get("Accept") or "").find("application/json") >= 0
        )
        f = request.files.get("file")
        if f is None:
            if wants_json:
                return _json_no_store(
                    {"ok": False, "code": "file_required", "message": "Seleccione un archivo."},
                    400,
                )
            flash("Archivo requerido.", "error")
            return redirect(url_for("nomina.banorte_index"))
        confirm = (request.form.get("confirm_reimport") or request.form.get("reimport_confirmed") or "") in {
            "1",
            "true",
            "True",
        }
        result = import_nomina_banorte_xlsx(
            _db_path(),
            f.read(),
            f.filename or "upload.xlsx",
            _username(),
            reimport_confirmed=confirm,
        )
        if not result.mutated:
            msg = (
                "Este archivo de base ya fue procesado anteriormente. "
                "¿Deseas importarlo de nuevo?"
            )
            if wants_json:
                return _json_no_store(
                    {
                        "ok": False,
                        "code": "duplicate_file_confirmation_required",
                        "message": msg,
                        "csrf_token": issue_csrf_token(),
                    },
                    409,
                )
            flash(msg, "warning")
            return redirect(url_for("nomina.banorte_index"))
        ok_msg = (
            f"Importación ALTAS OK. EXITOSO={result.count_exitosos} "
            f"manuales={result.count_manuales}."
        )
        if wants_json:
            return _json_no_store(
                {
                    "ok": True,
                    "message": ok_msg,
                    "count_exitosos": result.count_exitosos,
                    "count_manuales": result.count_manuales,
                    "csrf_token": issue_csrf_token(),
                }
            )
        flash(ok_msg, "success")
        return redirect(url_for("nomina.banorte_index"))

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_import_reporte():
        """Legacy form path — prefer JSON prepare-batch + confirm staging."""
        require_csrf()
        f = request.files.get("file")
        if f is None:
            flash("Archivo requerido.", "error")
            return redirect(url_for("nomina.banorte_index"))
        confirm = (request.form.get("reimport_confirmed") or "") == "1"
        out = prepare_reporte_batch(
            _db_path(),
            _username(),
            f.read(),
            f.filename or "reporte.xlsx",
            confirm_reimport=confirm,
        )
        if not out.get("ok"):
            flash(out.get("message") or "Confirme reimportación del archivo.", "warning")
        else:
            flash(
                f"Lote de reporte preparado ({len(out.get('batch', {}).get('rows') or [])} filas). Confirme en Agregar beneficiarios.",
                "success",
            )
        return redirect(url_for("nomina.banorte_index"))

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_reporte_prepare_batch():
        require_csrf()
        f = request.files.get("file")
        if f is None:
            return _json_no_store(
                {"ok": False, "code": "file_required", "message": "Seleccione un archivo."},
                400,
            )
        confirm = (request.form.get("confirm_reimport") or "") in {"1", "true", "True"}
        try:
            out = prepare_reporte_batch(
                _db_path(),
                _username(),
                f.read(),
                f.filename or "reporte.xlsx",
                confirm_reimport=confirm,
            )
        except ValueError as exc:
            return _json_no_store({"ok": False, "code": str(exc), "message": "No se pudo leer el reporte."}, 400)
        if not out.get("ok"):
            return _json_no_store({**out, "csrf_token": issue_csrf_token()}, 409)
        return _json_no_store({**out, "csrf_token": issue_csrf_token()})

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
    @_banorte_operator_required
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
        blocked = _legacy_authority_guard()
        if blocked is not None:
            return blocked
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
        prepared = enforce_prepared_rows_catalog_authority(_db_path(), draft, prepared)
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
    def banorte_draft_add_payment(draft_id: int):
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        if not data.get("catalog_person_id"):
            blocked = _legacy_authority_guard()
            if blocked is not None:
                return blocked
        try:
            draft = add_draft_payment(
                _db_path(),
                int(draft_id),
                _username(),
                int(data.get("expected_revision")),
                beneficiary_id=int(data.get("beneficiary_id")),
                amount_final=str(data.get("amount_final") or data.get("amount") or ""),
                request_nonce=str(data.get("request_nonce") or "") or None,
                confirm_duplicate_beneficiary=bool(data.get("confirm_duplicate_beneficiary")),
                catalog_person_id=int(data["catalog_person_id"])
                if data.get("catalog_person_id") not in (None, "")
                else None,
            )
        except DraftStaleError as exc:
            return _stale_response(exc)
        except (TypeError, ValueError) as exc:
            code = str(exc)
            messages = {
                "amount_must_be_positive": "El monto debe ser mayor a cero.",
                "amount_invalid": "El monto no es válido.",
                "beneficiary_not_found": "Seleccione un beneficiario Banorte válido.",
                "beneficiary_not_active": "El beneficiario no está activo.",
                "beneficiary_not_usable": "El beneficiario no está listo para pago.",
                "catalog_authority_required": "Se requiere selección desde catálogo oficial.",
                "draft_not_open": "El borrador no está abierto.",
                "duplicate_beneficiary_payment_confirmation_required": (
                    "Este beneficiario ya tiene un pago en el borrador. "
                    "¿Deseas agregar otro pago para la misma persona?"
                ),
            }
            status = 409 if code == "duplicate_beneficiary_payment_confirmation_required" else 400
            return _json_no_store(
                {
                    "ok": False,
                    "code": code,
                    "error_code": code,
                    "message": messages.get(code, "No se pudo agregar el pago."),
                },
                status,
            )
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
        """Legacy alias — prefer /undo for broad persistent undo."""
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        try:
            draft = undo_last_draft_mutation(
                _db_path(),
                int(draft_id),
                _username(),
                int(data.get("expected_revision")),
            )
        except DraftStaleError as exc:
            return _stale_response(exc)
        except ValueError as exc:
            return _json_no_store({"ok": False, "code": str(exc), "message": "No hay cambios para deshacer."}, 400)
        return _json_no_store({"ok": True, "draft": draft, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_draft_undo(draft_id: int):
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        try:
            draft = undo_last_draft_mutation(
                _db_path(),
                int(draft_id),
                _username(),
                int(data.get("expected_revision")),
            )
        except DraftStaleError as exc:
            return _stale_response(exc)
        except ValueError as exc:
            return _json_no_store(
                {"ok": False, "code": str(exc), "message": "No hay cambios para deshacer."},
                400,
            )
        undone = (draft or {}).get("last_undone_action")
        message = "Pago agregado deshecho" if undone == "ADD_ROW" else None
        return _json_no_store(
            {
                "ok": True,
                "draft": draft,
                "undone_action": undone,
                "message": message,
                "csrf_token": issue_csrf_token(),
            }
        )

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
        # populate rows from grid/paste adapter
        capture_rows = parse_capture_input(
            names_text=str(data.get("names") or ""),
            amounts_text=str(data.get("amounts") or ""),
            rows_payload=data.get("rows") if isinstance(data.get("rows"), list) else None,
            tsv_text=str(data.get("tsv") or ""),
        )
        draft = result["draft"]
        prepared = prepare_capture_rows(
            _db_path(),
            capture_rows,
            origin_kind="MANUAL_CAPTURE",
        )
        draft = save_draft_rows(_db_path(), int(draft["id"]), _username(), int(draft["revision"]), prepared)
        return _json_no_store({"ok": True, "draft": draft, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_excel_inspect():
        require_csrf()
        f = request.files.get("file")
        if f is None:
            return _excel_envelope(success=False, error_code="file_required", status=400)
        raw = f.read()
        try:
            out = inspect_excel(
                raw,
                f.filename or "upload.xlsx",
                secret_key=str(current_app.config["SECRET_KEY"]),
                user=_username(),
            )
        except ExcelNominaError as exc:
            return _excel_envelope(success=False, error_code=exc.code, status=400)
        return _excel_envelope(
            success=True,
            data=out,
            message="Archivo inspeccionado. Seleccione la hoja a importar.",
        )

    @_banorte_access_required
    def banorte_excel_preview():
        require_csrf()
        f = request.files.get("file")
        sheet = str(request.form.get("sheet") or "")
        token = str(request.form.get("token") or "")
        if f is None or not sheet or not token:
            return _excel_envelope(success=False, error_code="missing_fields", status=400)
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
            return _excel_envelope(success=False, error_code=exc.code, status=400)
        except ValueError as exc:
            return _excel_envelope(success=False, error_code=str(exc), status=400)
        return _excel_envelope(
            success=True,
            data={"preview": prev.__dict__},
            message=(
                f"Vista previa: {prev.banorte_count} pagos Banorte, "
                f"total ${prev.total_banorte_cents / 100:.2f}."
            ),
        )

    @_banorte_access_required
    def banorte_excel_prepare():
        require_csrf()
        f = request.files.get("file")
        sheet = str(request.form.get("sheet") or "")
        token = str(request.form.get("token") or "")
        if f is None or not sheet or not token:
            return _excel_envelope(success=False, error_code="missing_fields", status=400)
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
            return _excel_envelope(success=False, error_code=exc.code, status=400)
        except ValueError as exc:
            return _excel_envelope(success=False, error_code=str(exc), status=400)
        amount_errors = draft.pop("amount_errors", [])
        omitted = draft.pop("omitted", [])
        preview = draft.pop("preview", None)
        return _excel_envelope(
            success=True,
            data={
                "draft": draft,
                "amount_errors": amount_errors,
                "omitted": omitted,
                "preview": preview,
            },
            message="Borrador preparado. Revise el editor de pagos.",
        )
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
        scope = str(request.args.get("scope") or "current")
        if scope not in {"current", "historical"}:
            return _json_no_store(
                {"ok": False, "code": "invalid_scope", "message": "Vista no válida."},
                400,
            )
        data = list_beneficiaries(
            _db_path(),
            scope=scope,
            page=page,
            page_size=15,
            q_name="",
            q_emp="",
            validation_status=str(request.args.get("validation_status") or ""),
            record_status=str(request.args.get("record_status") or ""),
        )
        return _json_no_store({"ok": True, "listing": data, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_beneficiarios_search():
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        try:
            listing = list_beneficiaries(
                _db_path(),
                scope=str(data.get("scope") or "current"),
                page=int(data.get("page") or 1),
                page_size=15,
                q_name=str(data.get("q_name") or ""),
                q_emp=str(data.get("q_emp") or ""),
                validation_status=str(data.get("validation_status") or ""),
                record_status=str(data.get("record_status") or ""),
                sort=str(data.get("sort") or "id_desc"),
            )
        except ValueError as exc:
            message = "Vista no válida." if str(exc) == "invalid_scope" else "Orden no válido."
            return _json_no_store(
                {"ok": False, "code": str(exc), "message": message}, 400
            )
        return _json_no_store({"ok": True, "listing": listing, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_beneficiarios_available_numbers():
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        out = list_available_employee_numbers(
            _db_path(),
            limit=int(data.get("limit") or 20),
            after=data.get("after"),
        )
        return _json_no_store({"ok": True, **out, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_beneficiarios_actions(beneficiary_id: int):
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        try:
            out = apply_beneficiary_action(
                _db_path(),
                _username(),
                int(beneficiary_id),
                action=str(data.get("action") or ""),
                reason=str(data.get("reason") or ""),
                nombre=data.get("nombre"),
                account=data.get("account"),
                employee_number_effective=data.get("employee_number_effective"),
                winner_id=int(data["winner_id"]) if data.get("winner_id") is not None else None,
                loser_mode=data.get("loser_mode"),
            )
        except BeneficiaryError as exc:
            status = (
                409
                if exc.code == "beneficiary_action_disallowed_for_provenance"
                else 400
            )
            return _json_no_store(
                {
                    "ok": False,
                    "code": exc.code,
                    "error_code": exc.code,
                    "reason_code": getattr(exc, "reason_code", None),
                    "message": getattr(exc, "message", None) or beneficiary_action_message(exc.code),
                },
                status,
            )
        return _json_no_store(
            {
                "ok": True,
                "beneficiary": out,
                "message": out.get("message") or beneficiary_action_message(str(data.get("action") or "")),
                "csrf_token": issue_csrf_token(),
            }
        )

    @_banorte_access_required
    def banorte_beneficiarios_page():
        page = int(request.args.get("page") or 1)
        scope = str(request.args.get("scope") or "current")
        if scope not in {"current", "historical"}:
            abort(400)
        data = list_beneficiaries(
            _db_path(),
            scope=scope,
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
                scope=scope,
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
    @_banorte_operator_required
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
                employee_number=data.get("employee_number"),
                confirm_effective_from_account=bool(data.get("confirm_effective_from_account")),
            )
        except BeneficiaryError as exc:
            return _json_no_store({"ok": False, "code": exc.code}, 400)
        return _json_no_store({"ok": True, "beneficiary": created, "csrf_token": issue_csrf_token()})

    def _batch_stale(exc: BatchStaleError) -> Response:
        return _json_no_store(
            {
                "ok": False,
                "code": "batch_stale",
                "batch_id": exc.batch_id,
                "current_revision": exc.current_revision,
                "message": "El lote cambió. Se recargará la versión más reciente.",
            },
            409,
        )

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_batch_get_or_create():
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        origin = str(data.get("origin_kind") or "MANUAL")
        batch = create_batch(_db_path(), _username(), origin_kind=origin)
        return _json_no_store({"ok": True, "batch": batch, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_batch_get(batch_id: int):
        batch = get_batch(_db_path(), int(batch_id))
        if batch is None:
            return _json_no_store({"ok": False, "code": "batch_not_found"}, 404)
        return _json_no_store({"ok": True, "batch": batch, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_batch_add_row(batch_id: int):
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        try:
            batch = add_batch_row(
                _db_path(),
                int(batch_id),
                _username(),
                int(data.get("expected_revision")),
                nombre=str(data.get("nombre") or ""),
                cuenta=str(data.get("cuenta") or data.get("account") or ""),
                employee_number=data.get("employee_number"),
                use_account_as_employee_number=bool(data.get("use_account_as_employee_number")),
                comment=data.get("comment"),
            )
        except BatchStaleError as exc:
            return _batch_stale(exc)
        except ValueError as exc:
            return _json_no_store({"ok": False, "code": str(exc)}, 400)
        return _json_no_store({"ok": True, "batch": batch, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_batch_delete_row(batch_id: int, row_id: int):
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        try:
            batch = delete_batch_row(
                _db_path(),
                int(batch_id),
                int(row_id),
                _username(),
                int(data.get("expected_revision")),
            )
        except BatchStaleError as exc:
            return _batch_stale(exc)
        except ValueError as exc:
            return _json_no_store({"ok": False, "code": str(exc)}, 400)
        return _json_no_store({"ok": True, "batch": batch, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_batch_confirm(batch_id: int):
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        try:
            batch = confirm_batch(
                _db_path(), int(batch_id), _username(), int(data.get("expected_revision"))
            )
        except BatchStaleError as exc:
            return _batch_stale(exc)
        except ValueError as exc:
            return _json_no_store({"ok": False, "code": str(exc), "message": "Revise las filas del lote."}, 400)
        return _json_no_store({"ok": True, "batch": batch, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_batch_abandon(batch_id: int):
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        if not data.get("confirm"):
            return _json_no_store({"ok": False, "code": "confirm_required"}, 400)
        try:
            batch = abandon_batch(
                _db_path(), int(batch_id), _username(), int(data.get("expected_revision"))
            )
        except BatchStaleError as exc:
            return _batch_stale(exc)
        return _json_no_store({"ok": True, "batch": batch, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    @_banorte_operator_required
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
            status = (
                409
                if exc.code == "beneficiary_action_disallowed_for_provenance"
                else 400
            )
            return _json_no_store(
                {
                    "ok": False,
                    "code": exc.code,
                    "error_code": exc.code,
                    "reason_code": getattr(exc, "reason_code", None),
                    "message": getattr(exc, "message", None)
                    or beneficiary_action_message(exc.code),
                },
                status,
            )
        return _json_no_store({"ok": True, "beneficiary": out, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    def banorte_beneficiarios_history(beneficiary_id: int):
        try:
            detail = beneficiary_management_detail(_db_path(), int(beneficiary_id))
        except BeneficiaryError as exc:
            return _json_no_store(
                {"ok": False, "code": exc.code, "message": exc.message}, 404
            )
        return _json_no_store(
            {"ok": True, **detail, "csrf_token": issue_csrf_token()}
        )

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
        try:
            exported = load_historical_pag(_db_path(), export_id)
        except ExportDownloadError as exc:
            status = 404 if exc.code == "export_not_found" else 409
            return _json_no_store({"ok": False, "code": exc.code}, status)
        resp = send_file(
            BytesIO(exported.blob),
            as_attachment=True,
            download_name=exported.filename,
            mimetype="application/octet-stream",
        )
        resp.headers.update(_NO_STORE)
        return resp

    @_banorte_access_required
    def banorte_download_metadata(export_id: int):
        try:
            exported = load_historical_pag(_db_path(), export_id)
        except ExportDownloadError as exc:
            status = 404 if exc.code == "export_not_found" else 409
            return _json_no_store({"ok": False, "code": exc.code}, status)
        return _json_no_store(
            {
                "ok": True,
                "export_id": exported.export_id,
                "filename": exported.filename,
                "size_bytes": exported.size_bytes,
                "sha256": exported.sha256,
                "raw_url": url_for("nomina.banorte_download", export_id=exported.export_id),
            }
        )

    @_banorte_access_required
    def banorte_export_movements(export_id: int):
        try:
            historical = load_historical_export_movements(_db_path(), export_id)
        except HistoricalExportNotFound:
            return _json_no_store({"ok": False, "code": "export_not_found"}, 404)
        return _json_no_store({"ok": True, **historical})

    @_banorte_access_required
    def banorte_export_movements_excel(export_id: int):
        try:
            payload = build_historical_export_excel(_db_path(), export_id)
        except HistoricalExportNotFound:
            return _json_no_store({"ok": False, "code": "export_not_found"}, 404)
        resp = Response(
            payload["data"],
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resp.headers["Content-Disposition"] = (
            f'attachment; filename="{payload["filename"]}"'
        )
        resp.headers["Cache-Control"] = "private, no-store"
        return resp

    def _catalog_redirect(version_id: int | None = None):
        if version_id is None:
            return redirect(url_for("nomina.banorte_catalog_index"))
        return redirect(url_for("nomina.banorte_catalog_index", version_id=int(version_id)))

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_catalog_index():
        versions = list_catalog_versions(_db_path())
        selected = None
        selected_diff = None
        selected_check = None
        raw_version_id = request.args.get("version_id")
        if raw_version_id:
            try:
                version_id = int(raw_version_id)
                selected = get_catalog_version(_db_path(), version_id)
                if selected["status"] != "STAGED":
                    selected_diff = catalog_version_diff(_db_path(), version_id)
                    selected_check = catalog_activation_check(_db_path(), version_id)
            except (ValueError, CatalogVersionError):
                abort(404)
        resp = Response(
            render_template(
                "nomina/exportaciones_banorte_catalogo.html",
                versions=versions,
                selected=selected,
                selected_diff=selected_diff,
                selected_check=selected_check,
                csrf_token=issue_csrf_token(),
            )
        )
        resp.headers.update(_NO_STORE)
        return resp

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_catalog_upload():
        require_csrf()
        file = request.files.get("file")
        if file is None:
            flash("Seleccione un Empleados.txt.", "error")
            return _catalog_redirect()
        try:
            version = stage_catalog_version(
                _db_path(),
                raw=file.read(),
                filename=file.filename or "Empleados.txt",
                actor=_username(),
            )
        except (CatalogParseError, CatalogVersionError) as exc:
            flash(f"No se pudo preparar el catálogo ({exc}).", "error")
            return _catalog_redirect()
        flash(f"Versión #{version['id']} preparada como STAGED.", "success")
        return _catalog_redirect(int(version["id"]))

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_catalog_detail(version_id: int):
        try:
            detail = get_catalog_version(_db_path(), int(version_id))
        except CatalogVersionError:
            return _json_no_store({"ok": False, "code": "version_not_found"}, 404)
        return _json_no_store({"ok": True, "version": detail})

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_catalog_analyze(version_id: int):
        require_csrf()
        try:
            analyze_catalog_version(_db_path(), int(version_id), actor=_username())
        except CatalogVersionError as exc:
            flash(f"No se pudo analizar ({exc.code}).", "error")
            return _catalog_redirect(int(version_id))
        flash("Proyección analizada sin activar catálogo.", "success")
        return _catalog_redirect(int(version_id))

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_catalog_diff(version_id: int):
        try:
            diff = catalog_version_diff(_db_path(), int(version_id))
        except CatalogVersionError:
            return _json_no_store({"ok": False, "code": "version_not_found"}, 404)
        return _json_no_store({"ok": True, "diff": diff})

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_catalog_pre_reconcile(version_id: int):
        require_csrf()
        try:
            summary = pre_reconcile_catalog_version(
                _db_path(), int(version_id), actor=_username()
            )
        except CatalogReconciliationError as exc:
            flash(f"No se pudo pre-reconciliar ({exc.code}).", "error")
            return _catalog_redirect(int(version_id))
        auto = int(summary["by_status"].get("AUTO_MATCHED", 0))
        flash(
            f"Pre-reconciliación completada: {auto} automáticas, {summary['total'] - auto} administrativas.",
            "success",
        )
        return _catalog_redirect(int(version_id))

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_catalog_ready(version_id: int):
        require_csrf()
        try:
            mark_catalog_ready_for_review(_db_path(), int(version_id), actor=_username())
        except CatalogVersionError as exc:
            flash(f"No se pudo marcar para revisión ({exc.code}).", "error")
            return _catalog_redirect(int(version_id))
        flash("Versión lista para revisión. No fue activada.", "success")
        return _catalog_redirect(int(version_id))

    @_banorte_access_required
    def banorte_catalog_sidebar_search():
        require_csrf()
        data = request.get_json(silent=True) or {}
        require_csrf(data)
        try:
            result = search_catalog_sidebar(
                _db_path(),
                secret_key=str(current_app.config["SECRET_KEY"]),
                q=str(data.get("q") or ""),
                sort=str(data.get("sort") or "employee_asc"),
                cursor=str(data.get("cursor") or "") or None,
                limit=int(data.get("limit") or 25),
                role=_current_role(),
            )
        except CatalogSearchCursorError:
            return _json_no_store({"ok": False, "code": "cursor_invalid"}, 400)
        except ValueError:
            return _json_no_store({"ok": False, "code": "invalid_request"}, 400)
        return _json_no_store({"ok": True, **result, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_catalog_activate(version_id: int):
        require_csrf()
        try:
            result = activate_catalog_version(_db_path(), int(version_id), actor=_username())
        except CatalogActivationError as exc:
            return _json_no_store({"ok": False, "code": exc.code}, 400)
        flash("Catálogo activado.", "success")
        return _json_no_store({"ok": True, **result, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_catalog_rollback(version_id: int):
        require_csrf()
        try:
            result = rollback_catalog_activation(_db_path(), int(version_id), actor=_username())
        except CatalogActivationError as exc:
            return _json_no_store({"ok": False, "code": exc.code}, 400)
        flash("Activación revertida.", "success")
        return _json_no_store({"ok": True, **result, "csrf_token": issue_csrf_token()})

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_catalog_activation_check(version_id: int):
        try:
            check = catalog_activation_check(_db_path(), int(version_id))
        except ValueError:
            return _json_no_store({"ok": False, "code": "version_not_found"}, 404)
        return _json_no_store({"ok": True, **check})

    @_banorte_access_required
    @_banorte_operator_required
    def banorte_catalog_manual_reconcile():
        require_csrf()
        try:
            person_id = int(request.form.get("person_id") or 0)
            beneficiary_id = int(request.form.get("beneficiary_id") or 0)
            result = manual_reconcile_catalog_person(
                _db_path(),
                int(person_id),
                beneficiary_id,
                actor=_username(),
                reason=str(request.form.get("reason") or ""),
            )
        except (ValueError, CatalogReconciliationError) as exc:
            code = getattr(exc, "code", "beneficiary_invalid")
            flash(f"No se pudo reconciliar manualmente ({code}).", "error")
            return _catalog_redirect()
        flash("Reconciliación manual registrada con historia append-only.", "success")
        return _catalog_redirect(int(result["version_id"]))

    bp.add_url_rule("/exportaciones/banorte", endpoint="banorte_index", view_func=banorte_index, methods=["GET"])
    bp.add_url_rule(
        "/exportaciones/banorte/catalogo",
        endpoint="banorte_catalog_index",
        view_func=banorte_catalog_index,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/catalogo/versions",
        endpoint="banorte_catalog_upload",
        view_func=banorte_catalog_upload,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/catalogo/versions/<int:version_id>",
        endpoint="banorte_catalog_detail",
        view_func=banorte_catalog_detail,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/catalogo/versions/<int:version_id>/analyze",
        endpoint="banorte_catalog_analyze",
        view_func=banorte_catalog_analyze,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/catalogo/versions/<int:version_id>/diff",
        endpoint="banorte_catalog_diff",
        view_func=banorte_catalog_diff,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/catalogo/versions/<int:version_id>/pre-reconcile",
        endpoint="banorte_catalog_pre_reconcile",
        view_func=banorte_catalog_pre_reconcile,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/catalogo/versions/<int:version_id>/ready",
        endpoint="banorte_catalog_ready",
        view_func=banorte_catalog_ready,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/catalogo/sidebar/search",
        endpoint="banorte_catalog_sidebar_search",
        view_func=banorte_catalog_sidebar_search,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/catalogo/versions/<int:version_id>/activate",
        endpoint="banorte_catalog_activate",
        view_func=banorte_catalog_activate,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/catalogo/versions/<int:version_id>/rollback",
        endpoint="banorte_catalog_rollback",
        view_func=banorte_catalog_rollback,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/catalogo/versions/<int:version_id>/activation-check",
        endpoint="banorte_catalog_activation_check",
        view_func=banorte_catalog_activation_check,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/catalogo/reconciliations/manual",
        endpoint="banorte_catalog_manual_reconcile",
        view_func=banorte_catalog_manual_reconcile,
        methods=["POST"],
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
        "/exportaciones/banorte/import/reporte/prepare-batch",
        endpoint="banorte_reporte_prepare_batch",
        view_func=banorte_reporte_prepare_batch,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/beneficiarios/batches",
        endpoint="banorte_batch_get_or_create",
        view_func=banorte_batch_get_or_create,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/beneficiarios/batches/<int:batch_id>",
        endpoint="banorte_batch_get",
        view_func=banorte_batch_get,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/beneficiarios/batches/<int:batch_id>/rows",
        endpoint="banorte_batch_add_row",
        view_func=banorte_batch_add_row,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/beneficiarios/batches/<int:batch_id>/rows/<int:row_id>/delete",
        endpoint="banorte_batch_delete_row",
        view_func=banorte_batch_delete_row,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/beneficiarios/batches/<int:batch_id>/confirm",
        endpoint="banorte_batch_confirm",
        view_func=banorte_batch_confirm,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/beneficiarios/batches/<int:batch_id>/abandon",
        endpoint="banorte_batch_abandon",
        view_func=banorte_batch_abandon,
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
        "/exportaciones/banorte/drafts/<int:draft_id>/add-payment",
        endpoint="banorte_draft_add_payment",
        view_func=banorte_draft_add_payment,
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
        "/exportaciones/banorte/drafts/<int:draft_id>/undo",
        endpoint="banorte_draft_undo",
        view_func=banorte_draft_undo,
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
        "/exportaciones/banorte/beneficiarios/search",
        endpoint="banorte_beneficiarios_search",
        view_func=banorte_beneficiarios_search,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/beneficiarios/available-employee-numbers",
        endpoint="banorte_beneficiarios_available_numbers",
        view_func=banorte_beneficiarios_available_numbers,
        methods=["POST"],
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
        "/exportaciones/banorte/beneficiarios/<int:beneficiary_id>/actions",
        endpoint="banorte_beneficiarios_actions",
        view_func=banorte_beneficiarios_actions,
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
    bp.add_url_rule(
        "/exportaciones/banorte/historial/<int:export_id>/metadata",
        endpoint="banorte_download_metadata",
        view_func=banorte_download_metadata,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/historial/<int:export_id>/movimientos",
        endpoint="banorte_export_movements",
        view_func=banorte_export_movements,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/exportaciones/banorte/historial/<int:export_id>/movimientos.xlsx",
        endpoint="banorte_export_movements_excel",
        view_func=banorte_export_movements_excel,
        methods=["GET"],
    )
