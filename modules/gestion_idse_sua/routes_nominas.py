"""Rutas del espacio Nóminas y análisis."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from io import BytesIO

from flask import abort, flash, g, jsonify, redirect, render_template, request, send_file, url_for
from openpyxl import load_workbook

from modules.comparativo.headcount_service import obtener_activos
from modules.gestion_idse_sua.nominas import repository as repo
from modules.gestion_idse_sua.nominas.attendance_service import (
    correct_attendance_code,
    list_period_attendance,
    trajectory_for_periods,
)
from modules.gestion_idse_sua.nominas.client_inference_service import infer_period_clients, preview_sheet_clients
from modules.gestion_idse_sua.nominas.comparative_service import enrich_workers, run_comparative, summarize_results
from modules.gestion_idse_sua.nominas.excel_export import generate_comparative_excel
from modules.gestion_idse_sua.nominas.import_service import (
    confirm_classifications,
    confirm_period,
    extract_sheet_workers,
    register_import,
)
from modules.gestion_idse_sua.nominas.match_service import (
    _build_match,
    build_review_match,
    confirm_match,
    load_full_headcount,
    manual_search,
    match_worker,
)
from modules.gestion_idse_sua.nominas.movement_bridge import convert_results_to_movements
from modules.gestion_idse_sua.nominas.period_signals import collect_period_signals
from modules.gestion_idse_sua.nominas.planta_cliente_service import confirm_planta_cliente
from modules.gestion_idse_sua.nominas.schema import ensure_gis_nominas_tables
from modules.gestion_idse_sua.nominas.sheet_inspector import inspect_sheet
from modules.gestion_idse_sua.nominas.text_utils import normalize_upper
from modules.gestion_idse_sua.nominas.ui_helpers import (
    build_weekly_workspace_rows,
    format_period_hint,
    group_attendance_rows,
    parse_suggested_period,
    period_day_headers,
)
from modules.nomina.asistencia_palette import css_vars_for_json
from modules.exportacion_imss.exportacion_service import obtener_patrones
from modules.roles_access import can_access_comparativo, normalized_role


def _staging_path(import_id: int) -> Path:
    base = Path(tempfile.gettempdir()) / "gis_nomina_imports"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"import_{import_id}.xlsx"


def _load_import_bytes(conn: sqlite3.Connection, import_id: int) -> bytes:
    row = conn.execute(
        "SELECT file_content FROM gis_nomina_imports WHERE id = ?", (import_id,)
    ).fetchone()
    if row is not None and row["file_content"]:
        return bytes(row["file_content"])
    staging = _staging_path(import_id)
    return staging.read_bytes() if staging.is_file() else b""


def _db_from_app() -> sqlite3.Connection:
    from flask import current_app

    conn = sqlite3.connect(str(current_app.config["DATABASE"]))
    conn.row_factory = sqlite3.Row
    return conn


def _require_comparativo() -> None:
    role = normalized_role(g.user)
    if not can_access_comparativo(role):
        abort(403)


def _session_username() -> str:
    user = g.user
    if not user:
        return ""
    if isinstance(user, dict):
        return str(user.get("username") or "")
    return str(user["username"])


def _clientes_disponibles() -> list[str]:
    try:
        activos = obtener_activos()
        return sorted({str(a.get("cliente", "")).strip() for a in activos if a.get("cliente")})
    except Exception:
        return []


def register_nominas_routes(bp, *, login_required) -> None:
    def route(rule, **options):
        def decorator(f):
            wrapped = login_required(f)
            endpoint = options.pop("endpoint", f.__name__)
            bp.add_url_rule(rule, endpoint=endpoint, view_func=wrapped, **options)
            return wrapped

        return decorator

    @route("/nominas", methods=["GET"], endpoint="nominas_index")
    def nominas_index():
        _require_comparativo()
        conn = _db_from_app()
        try:
            ensure_gis_nominas_tables(conn)
            conn.commit()
            show_archived = request.args.get("archived") == "1"
            recent = conn.execute(
                """
                SELECT id, original_filename, uploaded_at, status, archived_at, archived_by, archive_reason
                FROM gis_nomina_imports
                WHERE (? = 1 AND archived_at IS NOT NULL) OR (? = 0 AND archived_at IS NULL)
                ORDER BY id DESC LIMIT 25
                """,
                (1 if show_archived else 0, 1 if show_archived else 0),
            ).fetchall()
            recent_rows = []
            for row in recent:
                item = dict(row)
                item["resume"] = repo.resolve_import_resume(conn, int(row["id"]))
                item["dependencies"] = repo.import_dependencies(conn, int(row["id"]))
                recent_rows.append(item)
            return render_template(
                "gestion_idse_sua/nominas/index.html",
                clientes=_clientes_disponibles(),
                recent=recent_rows,
                show_archived=show_archived,
                legacy_url=url_for("comparativo.index"),
            )
        finally:
            conn.close()

    @route("/nominas/import", methods=["POST"], endpoint="nominas_import")
    def nominas_import():
        _require_comparativo()
        file = request.files.get("file")
        if file is None or not file.filename:
            flash("Seleccione un archivo Excel.", "error")
            return redirect(url_for("gestion_idse_sua.nominas_index"))
        data = file.read()
        conn = _db_from_app()
        try:
            ensure_gis_nominas_tables(conn)
            result = register_import(
                conn,
                file_bytes=data,
                filename=file.filename,
                uploaded_by=_session_username(),
            )
            _staging_path(result["import_id"]).write_bytes(data)
            return redirect(url_for("gestion_idse_sua.nominas_import_review", import_id=result["import_id"]))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("gestion_idse_sua.nominas_index"))
        finally:
            conn.close()

    @route("/nominas/import/<int:import_id>", methods=["GET"], endpoint="nominas_import_review")
    def nominas_import_review(import_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            imp = repo.get_import(conn, import_id)
            if imp is None:
                abort(404)
            return render_template(
                "gestion_idse_sua/nominas/import_review.html",
                import_row=imp,
                sheets=[
                    {**dict(s), "period_hint": format_period_hint(parse_suggested_period(s.get("suggested_period_json")))}
                    for s in repo.list_sheets(conn, import_id)
                ],
            )
        finally:
            conn.close()

    @route("/nominas/import/<int:import_id>/open", methods=["GET"], endpoint="nominas_open_import")
    def nominas_open_import(import_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            state = repo.resolve_import_resume(conn, import_id)
            if state["state"] == "missing":
                abort(404)
            if state["state"] == "archived":
                flash("La importación está archivada. Restáurela para continuar.", "warning")
                return redirect(url_for("gestion_idse_sua.nominas_index", archived=1))
            if state["state"] in {"comparative_ready", "period_confirmed"}:
                return redirect(url_for("gestion_idse_sua.nominas_workspace", period_id=state["period_id"]))
            if state["state"] == "period_pending":
                return redirect(url_for("gestion_idse_sua.nominas_period_review", import_id=import_id))
            if state["state"] == "incomplete":
                flash("Importación incompleta: no hay hojas extraídas. Puede reimportar o archivar.", "warning")
                return redirect(url_for("gestion_idse_sua.nominas_index"))
            return redirect(url_for("gestion_idse_sua.nominas_import_review", import_id=import_id))
        finally:
            conn.close()

    @route("/nominas/import/<int:import_id>/archive", methods=["POST"], endpoint="nominas_archive_import")
    def nominas_archive_import(import_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            if repo.get_import(conn, import_id) is None:
                abort(404)
            dependencies = repo.import_dependencies(conn, import_id)
            repo.archive_import(
                conn,
                import_id,
                archived_by=_session_username(),
                reason=request.form.get("reason"),
            )
            conn.commit()
            linked = sum(dependencies.values())
            if linked:
                flash(f"Importación archivada sin borrar {linked} dependencias históricas.", "warning")
            else:
                flash("Importación archivada. Puede restaurarla cuando lo necesite.", "success")
            return redirect(url_for("gestion_idse_sua.nominas_index"))
        finally:
            conn.close()

    @route("/nominas/import/<int:import_id>/restore", methods=["POST"], endpoint="nominas_restore_import")
    def nominas_restore_import(import_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            if repo.get_import(conn, import_id) is None:
                abort(404)
            repo.restore_import(conn, import_id)
            conn.commit()
            flash("Importación restaurada.", "success")
            return redirect(url_for("gestion_idse_sua.nominas_index", archived=1))
        finally:
            conn.close()

    @route("/nominas/import/<int:import_id>/classify", methods=["POST"], endpoint="nominas_classify")
    def nominas_classify(import_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            mapping = {
                int(key.replace("sheet_", "")): value
                for key, value in request.form.items()
                if key.startswith("sheet_")
            }
            confirm_classifications(conn, import_id, mapping)
            return redirect(url_for("gestion_idse_sua.nominas_period_review", import_id=import_id))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("gestion_idse_sua.nominas_import_review", import_id=import_id))
        finally:
            conn.close()

    @route("/nominas/import/<int:import_id>/period", methods=["GET"], endpoint="nominas_period_review")
    def nominas_period_review(import_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            sheets = []
            imp = repo.get_import(conn, import_id)
            if imp is None:
                abort(404)
            continue_pending = (
                request.args.get("continue_pending") == "1"
                and repo.has_pending_payroll_sheets(conn, import_id)
            )
            if (
                continue_pending
                and repo.resolve_import_resume(conn, import_id)["state"] == "comparative_ready"
            ):
                period_id = int(
                    conn.execute(
                        """
                        SELECT p.id
                        FROM gis_nomina_comparatives c
                        JOIN gis_nomina_periods p ON p.id = c.period_id
                        JOIN gis_nomina_sheets s ON s.id = p.sheet_id
                        WHERE s.import_id = ?
                        ORDER BY c.id DESC
                        LIMIT 1
                        """,
                        (import_id,),
                    ).fetchone()["id"]
                )
                return redirect(url_for("gestion_idse_sua.nominas_workspace", period_id=period_id))
            file_bytes = _load_import_bytes(conn, import_id)
            headcount_rows = obtener_activos() if file_bytes else []
            for s in repo.list_sheets(conn, import_id):
                if s.get("confirmed_classification") != "nomina":
                    continue
                if continue_pending:
                    extracted = conn.execute(
                        """
                        SELECT 1
                        FROM gis_nomina_periods p
                        JOIN gis_nomina_workers w ON w.period_id = p.id
                        WHERE p.sheet_id = ?
                        LIMIT 1
                        """,
                        (s["id"],),
                    ).fetchone()
                    if extracted is not None:
                        continue
                period = parse_suggested_period(s.get("suggested_period_json"))
                preview = {"workers": [], "summary": {"counts": {}, "pending_count": 0}}
                if file_bytes:
                    preview = preview_sheet_clients(
                        conn,
                        file_bytes=file_bytes,
                        sheet=dict(s),
                        headcount_rows=headcount_rows,
                        filename=str(imp["original_filename"]),
                    )
                sheets.append(
                    {
                        **dict(s),
                        "period_hint": format_period_hint(period),
                        "period": period,
                        "client_preview": preview,
                    }
                )
            return render_template(
                "gestion_idse_sua/nominas/period_review.html",
                import_id=import_id,
                sheets=sheets,
                clientes=_clientes_disponibles(),
            )
        finally:
            conn.close()

    @route("/nominas/sheet/<int:sheet_id>/period", methods=["POST"], endpoint="nominas_confirm_period")
    def nominas_confirm_period(sheet_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            import_row = conn.execute(
                "SELECT s.import_id, s.sheet_index, s.sheet_name FROM gis_nomina_sheets s WHERE s.id = ?",
                (sheet_id,),
            ).fetchone()
            if import_row is None:
                abort(404)
            import_id = int(import_row["import_id"])
            staging = _staging_path(import_id)
            file_bytes = _load_import_bytes(conn, import_id)
            if not file_bytes:
                flash("Importación incompleta: falta el Excel extraído. Reimporte o archive el registro.", "error")
                return redirect(url_for("gestion_idse_sua.nominas_index"))
            wb = load_workbook(BytesIO(file_bytes), data_only=True)
            ws = wb.worksheets[int(import_row["sheet_index"])]
            inspection = inspect_sheet(
                ws,
                sheet_name=str(import_row["sheet_name"]),
                sheet_index=int(import_row["sheet_index"]),
                is_hidden=False,
            )
            signal_payload = collect_period_signals(
                ws,
                sheet_name=str(import_row["sheet_name"]),
                header_row=inspection.get("header_row"),
                nombre_col=(inspection.get("columns") or {}).get("nombre"),
            )
            wb.close()

            selected_clients = {
                normalize_upper(value) for value in request.form.getlist("clientes") if normalize_upper(value)
            }
            period = confirm_period(
                conn,
                sheet_id,
                fecha_inicio=request.form.get("fecha_inicio", ""),
                fecha_fin=request.form.get("fecha_fin", ""),
                cliente=next(iter(selected_clients)) if len(selected_clients) == 1 else None,
                confirmed=True,
                extra_warnings=signal_payload.get("warnings") or [],
            )
            if period.get("conflicts"):
                flash(
                    "Advertencia: ya existe otro periodo confirmado con las mismas fechas. "
                    "Puede continuar importando semanas históricas.",
                    "warning",
                )
            if signal_payload.get("warnings"):
                flash("Advertencia: se detectaron señales de periodo contradictorias.", "warning")
            hc = obtener_activos()
            extract_sheet_workers(conn, file_bytes=file_bytes, sheet_id=sheet_id, headcount_rows=hc)
            period = repo.get_period_for_sheet(conn, sheet_id)
            full_hc = load_full_headcount()
            enrich_workers(conn, int(period["id"]), full_hc or hc)
            workers = repo.list_workers(conn, int(period["id"]))
            matches = {int(w["id"]): repo.get_match(conn, int(w["id"])) for w in workers}
            assignments = infer_period_clients(
                conn,
                period_id=int(period["id"]),
                workers=workers,
                matches=matches,
                headcount_rows=hc,
                filename=str(conn.execute(
                    "SELECT original_filename FROM gis_nomina_imports WHERE id = ?", (import_id,)
                ).fetchone()["original_filename"]),
                sheet_name=str(import_row["sheet_name"]),
            )
            for assignment in assignments["workers"]:
                cliente = normalize_upper(assignment.get("cliente"))
                if cliente and cliente in selected_clients:
                    repo.update_worker_cliente(conn, int(assignment["worker_id"]), cliente)
            staging.unlink(missing_ok=True)
            repo.set_import_status(conn, import_id, "review")
            conn.commit()
            return redirect(url_for("gestion_idse_sua.nominas_workspace", period_id=period["id"]))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(request.referrer or url_for("gestion_idse_sua.nominas_index"))
        finally:
            conn.close()

    @route("/nominas/workspace/<int:period_id>", methods=["GET"], endpoint="nominas_workspace")
    def nominas_workspace(period_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            period = conn.execute(
                """
                SELECT p.*, s.sheet_name, s.import_id, i.original_filename
                FROM gis_nomina_periods p
                JOIN gis_nomina_sheets s ON s.id = p.sheet_id
                JOIN gis_nomina_imports i ON i.id = s.import_id
                WHERE p.id = ?
                """,
                (period_id,),
            ).fetchone()
            if period is None:
                abort(404)
            workers = repo.list_workers(conn, period_id)
            enriched_workers = []
            for worker in workers:
                row = dict(worker)
                row["match"] = repo.get_match(conn, int(worker["id"]))
                row["attendance"] = repo.list_attendance_for_worker(conn, int(worker["id"]))
                enriched_workers.append(row)
            attendance = list_period_attendance(conn, period_id)
            attendance_by_id = {int(a["id"]): a for a in attendance}
            trajectory = trajectory_for_periods(conn, [period_id])
            confirmed_clients = sorted(
                {
                    normalize_upper(worker.get("cliente_confirmado"))
                    for worker in workers
                    if normalize_upper(worker.get("cliente_confirmado"))
                }
            )
            comparatives = repo.list_latest_comparatives(
                conn, period_id, clientes=confirmed_clients
            )
            comparative = max(comparatives, key=lambda item: int(item["id"]), default=None)
            results = []
            for item in comparatives:
                item_client = normalize_upper(item.get("cliente"))
                for result in repo.list_results(conn, int(item["id"])):
                    if result.get("worker_id") and normalize_upper(
                        result.get("cliente_confirmado")
                    ) != item_client:
                        continue
                    results.append(result)
            comp_warnings = []
            for item in comparatives:
                if not item.get("warnings_json"):
                    continue
                import json

                try:
                    item_warnings = json.loads(item["warnings_json"])
                except json.JSONDecodeError:
                    item_warnings = []
                comp_warnings.extend(str(warning) for warning in item_warnings)
            hc_rows = obtener_activos()
            matches = {int(w["id"]): w.get("match") for w in enriched_workers}
            client_payload = infer_period_clients(
                conn,
                period_id=period_id,
                workers=[dict(w) for w in workers],
                matches=matches,
                headcount_rows=hc_rows,
                filename=str(period["original_filename"] or ""),
                sheet_name=str(period["sheet_name"] or ""),
            )
            client_by_worker = {item["worker_id"]: item for item in client_payload["workers"]}
            day_headers = period_day_headers(str(period["fecha_inicio"]))
            table_rows = build_weekly_workspace_rows(
                workers=enriched_workers,
                results=results,
                attendance_rows=attendance,
                client_inferences=client_by_worker,
                trajectory_payload=trajectory,
            )
            for table_row in table_rows:
                result_id = table_row.get("result_id")
                table_row["audit"] = repo.list_workspace_audit(
                    conn, scope="weekly", record_type="result", record_id=int(result_id)
                ) if result_id else []
            pending_import_url = None
            if repo.has_pending_payroll_sheets(conn, int(period["import_id"])):
                pending_import_url = url_for(
                    "gestion_idse_sua.nominas_period_review",
                    import_id=int(period["import_id"]),
                    continue_pending=1,
                )
            return render_template(
                "gestion_idse_sua/nominas/workspace.html",
                period=dict(period),
                workers=enriched_workers,
                comparative=dict(comparative) if comparative else None,
                results=results,
                totals=summarize_results(results) if results else {},
                clientes=_clientes_disponibles(),
                client_summary=client_payload["summary"],
                client_by_worker=client_by_worker,
                table_rows=table_rows,
                day_headers=day_headers,
                attendance_palette=css_vars_for_json(),
                patrones=obtener_patrones(),
                comp_warnings=comp_warnings,
                attendance=group_attendance_rows(attendance),
                attendance_by_id=attendance_by_id,
                trajectory=trajectory,
                legacy_url=url_for("comparativo.index"),
                movimientos_url=url_for("exportacion_imss.index"),
                pending_import_url=pending_import_url,
            )
        finally:
            conn.close()

    @route("/nominas/workspace/<int:period_id>/compare", methods=["POST"], endpoint="nominas_compare")
    def nominas_compare(period_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            period = conn.execute(
                """
                SELECT p.*, s.sheet_name, i.original_filename
                FROM gis_nomina_periods p
                JOIN gis_nomina_sheets s ON s.id = p.sheet_id
                JOIN gis_nomina_imports i ON i.id = s.import_id
                WHERE p.id = ?
                """,
                (period_id,),
            ).fetchone()
            if period is None:
                abort(404)
            requested_client = normalize_upper(request.form.get("cliente"))
            workers = repo.list_workers(conn, period_id)
            confirmed_clients = sorted(
                {
                    normalize_upper(worker.get("cliente_confirmado"))
                    for worker in workers
                    if normalize_upper(worker.get("cliente_confirmado"))
                }
            )
            target_clients = [requested_client] if requested_client else confirmed_clients
            if not target_clients:
                flash("No hay trabajadores con cliente confirmado para comparar.", "error")
                return redirect(url_for("gestion_idse_sua.nominas_workspace", period_id=period_id))
            for cliente in target_clients:
                run_comparative(
                    conn,
                    period_id=period_id,
                    cliente=cliente,
                    generated_by=_session_username(),
                )
            flash(
                "Comparativo generado."
                if len(target_clients) == 1
                else f"Comparativos generados: {len(target_clients)}.",
                "success",
            )
            return redirect(url_for("gestion_idse_sua.nominas_workspace", period_id=period_id))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("gestion_idse_sua.nominas_workspace", period_id=period_id))
        finally:
            conn.close()

    @route("/nominas/worker/<int:worker_id>/planta-cliente", methods=["POST"], endpoint="nominas_confirm_planta")
    def nominas_confirm_planta(worker_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            confirm_planta_cliente(
                conn,
                planta=request.form.get("planta", ""),
                cliente=request.form.get("cliente", ""),
                confirmed_by=_session_username(),
            )
            repo.update_worker_cliente(conn, worker_id, request.form.get("cliente", ""))
            conn.commit()
            period_id = conn.execute(
                "SELECT period_id FROM gis_nomina_workers WHERE id = ?",
                (worker_id,),
            ).fetchone()
            return redirect(url_for("gestion_idse_sua.nominas_workspace", period_id=int(period_id["period_id"])))
        finally:
            conn.close()

    @route("/nominas/worker/<int:worker_id>/confirm-match", methods=["POST"], endpoint="nominas_confirm_suggested_match")
    def nominas_confirm_suggested_match(worker_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            match = repo.get_match(conn, worker_id)
            if match and match.get("status") in {"suggested", "auto"}:
                from modules.gestion_idse_sua.nominas.match_service import confirm_match

                confirm_match(conn, worker_id, dict(match))
                conn.commit()
                flash("Match confirmado.", "success")
            period_id = conn.execute(
                "SELECT period_id FROM gis_nomina_workers WHERE id = ?",
                (worker_id,),
            ).fetchone()
            return redirect(url_for("gestion_idse_sua.nominas_workspace", period_id=int(period_id["period_id"])))
        finally:
            conn.close()

    @route("/nominas/result/<int:result_id>/decision", methods=["POST"], endpoint="nominas_update_decision")
    def nominas_update_decision(result_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            current = conn.execute(
                "SELECT resultado, decision_final FROM gis_nomina_results WHERE id = ?",
                (result_id,),
            ).fetchone()
            if current is None:
                abort(404)
            decision_final = (request.form.get("decision_final") or "").strip() or str(
                current["decision_final"] or current["resultado"]
            )
            if decision_final not in {
                "Coincidencia",
                "Revisión",
                "Posible alta",
                "Posible baja",
                "Reingreso",
            }:
                abort(400)
            movement_value = request.form.get("tipo_sugerido")
            if movement_value is not None:
                movement_value = normalize_upper(movement_value)
                if movement_value not in {"", "ALTA", "BAJA"}:
                    abort(400)
            repo.update_result_decision(
                conn,
                result_id,
                decision_final=decision_final,
                tipo_sugerido=movement_value,
                fecha_sugerida=(request.form.get("fecha_sugerida") or "").strip() or None,
                observaciones=(request.form.get("observaciones") or "").strip() or None,
                changed_by=_session_username(),
            )
            conn.commit()
            comp = conn.execute(
                "SELECT c.period_id FROM gis_nomina_results r JOIN gis_nomina_comparatives c ON c.id = r.comparative_id WHERE r.id = ?",
                (result_id,),
            ).fetchone()
            return redirect(url_for("gestion_idse_sua.nominas_workspace", period_id=int(comp["period_id"])))
        finally:
            conn.close()

    @route("/nominas/result/<int:result_id>/visibility", methods=["POST"], endpoint="nominas_result_visibility")
    def nominas_result_visibility(result_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            comp = conn.execute(
                """
                SELECT c.period_id
                FROM gis_nomina_results r
                JOIN gis_nomina_comparatives c ON c.id = r.comparative_id
                WHERE r.id = ?
                """,
                (result_id,),
            ).fetchone()
            if comp is None:
                abort(404)
            hidden = request.form.get("action") != "restore"
            repo.set_result_visibility(
                conn,
                result_id,
                hidden=hidden,
                changed_by=_session_username(),
                reason=request.form.get("reason"),
            )
            conn.commit()
            flash("Línea restaurada." if not hidden else "Línea retirada de la vista activa.", "success")
            return redirect(url_for("gestion_idse_sua.nominas_workspace", period_id=int(comp["period_id"])))
        finally:
            conn.close()

    @route("/nominas/comparative/<int:comparative_id>/visibility", methods=["POST"], endpoint="nominas_bulk_visibility")
    def nominas_bulk_visibility(comparative_id: int):
        _require_comparativo()
        result_ids = [int(value) for value in request.form.getlist("result_ids") if str(value).isdigit()]
        conn = _db_from_app()
        try:
            comparative = repo.get_comparative(conn, comparative_id)
            if comparative is None:
                abort(404)
            hidden = request.form.get("action") != "restore"
            valid = {
                int(row["id"]) for row in repo.list_results(conn, comparative_id)
            }
            for result_id in result_ids:
                if result_id in valid:
                    repo.set_result_visibility(
                        conn,
                        result_id,
                        hidden=hidden,
                        changed_by=_session_username(),
                        reason=request.form.get("reason"),
                    )
            conn.commit()
            return redirect(url_for("gestion_idse_sua.nominas_workspace", period_id=int(comparative["period_id"])))
        finally:
            conn.close()

    @route("/nominas/worker/<int:worker_id>/match", methods=["POST"], endpoint="nominas_confirm_match")
    def nominas_confirm_match(worker_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            worker = conn.execute(
                "SELECT period_id, cliente_confirmado FROM gis_nomina_workers WHERE id = ?",
                (worker_id,),
            ).fetchone()
            if worker is None:
                abort(404)
            if not normalize_upper(worker["cliente_confirmado"]):
                flash("Confirme el cliente antes de resolver la identidad.", "error")
                return redirect(
                    url_for("gestion_idse_sua.nominas_workspace", period_id=int(worker["period_id"]))
                )
            search = manual_search(request.form.get("query", ""), request.form.get("campo", "nombre_completo"))
            if not search.get("encontrado"):
                flash("No se encontró trabajador en Headcount.", "error")
            else:
                candidates = [search["datos"]] if search.get("datos") else list(search.get("opciones") or [])
                if len(candidates) == 1:
                    confirm_match(
                        conn,
                        worker_id,
                        _build_match(candidates[0], method="manual", confidence=1.0, status="manual"),
                    )
                    conn.commit()
                    flash("Match confirmado.", "success")
                elif candidates:
                    repo.upsert_match(
                        conn,
                        worker_id,
                        build_review_match(
                            candidates,
                            method="busqueda_manual",
                            reason="La búsqueda devolvió varias personas del mismo cliente",
                        ),
                    )
                    conn.commit()
                    flash("Se encontraron varias opciones; revise los candidatos antes de confirmar.", "warning")
                else:
                    flash("No se encontró trabajador en Headcount.", "error")
            return redirect(
                url_for("gestion_idse_sua.nominas_workspace", period_id=int(worker["period_id"]))
            )
        finally:
            conn.close()

    @route("/nominas/comparative/<int:comparative_id>/export", methods=["GET"], endpoint="nominas_export")
    def nominas_export(comparative_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            out_buf, filename = generate_comparative_excel(
                conn,
                comparative_id,
                username=_session_username(),
            )
            return send_file(
                out_buf,
                as_attachment=True,
                download_name=filename,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        finally:
            conn.close()

    @route("/nominas/comparative/<int:comparative_id>/convert", methods=["POST"], endpoint="nominas_convert")
    def nominas_convert(comparative_id: int):
        _require_comparativo()
        ids = [int(x) for x in request.form.getlist("result_ids") if str(x).isdigit()]
        overrides: dict[int, dict[str, str]] = {}
        for rid in ids:
            overrides[rid] = {
                "tipo_movimiento": request.form.get(f"tipo_{rid}", ""),
                "fecha_movimiento": request.form.get(f"fecha_{rid}", ""),
                "rp": request.form.get(f"rp_{rid}", ""),
                "rfc_patron": request.form.get(f"rfc_patron_{rid}", ""),
            }
        conn = _db_from_app()
        try:
            outcome = convert_results_to_movements(conn, result_ids=ids, overrides=overrides)
            flash(
                f"Movimientos creados: {len(outcome['converted_ids'])}; excluidos: {len(outcome['excluded'])}.",
                "success",
            )
            period = conn.execute(
                "SELECT period_id FROM gis_nomina_comparatives WHERE id = ?",
                (comparative_id,),
            ).fetchone()
            return redirect(url_for("gestion_idse_sua.nominas_workspace", period_id=int(period["period_id"])))
        finally:
            conn.close()

    @route("/nominas/attendance/<int:attendance_id>/correct", methods=["POST"], endpoint="nominas_correct_attendance")
    def nominas_correct_attendance(attendance_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            row = repo.get_attendance(conn, attendance_id)
            if row is None:
                abort(404)
            correct_attendance_code(
                conn,
                attendance_id=attendance_id,
                code_corrected=(request.form.get("code_corrected") or "").strip(),
                corrected_by=_session_username(),
                reason=(request.form.get("reason") or "").strip() or None,
            )
            flash("Corrección de asistencia registrada.", "success")
            period_id = int(row["period_id"])
            return redirect(url_for("gestion_idse_sua.nominas_workspace", period_id=period_id))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(request.referrer or url_for("gestion_idse_sua.nominas_index"))
        finally:
            conn.close()

    @route("/nominas/api/trajectory", methods=["GET"], endpoint="nominas_trajectory_api")
    def nominas_trajectory_api():
        _require_comparativo()
        raw = (request.args.get("period_ids") or "").strip()
        if not raw:
            return jsonify({"error": "period_ids requerido"}), 400
        period_ids = [int(x) for x in raw.split(",") if x.strip().isdigit()]
        if not period_ids:
            return jsonify({"error": "period_ids inválido"}), 400
        conn = _db_from_app()
        try:
            ensure_gis_nominas_tables(conn)
            payload = trajectory_for_periods(conn, period_ids)
            return jsonify(payload)
        finally:
            conn.close()

    @route("/nominas/api/search-headcount", methods=["GET"], endpoint="nominas_search_headcount")
    def nominas_search_headcount():
        _require_comparativo()
        try:
            return jsonify(
                manual_search(
                    (request.args.get("q") or "").strip(),
                    (request.args.get("campo") or "nombre_completo").strip(),
                )
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
