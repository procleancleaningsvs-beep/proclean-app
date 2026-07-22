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
from modules.gestion_idse_sua.nominas.comparative_service import enrich_workers, run_comparative, summarize_results
from modules.gestion_idse_sua.nominas.excel_export import generate_comparative_excel
from modules.gestion_idse_sua.nominas.import_service import (
    confirm_classifications,
    confirm_period,
    extract_sheet_workers,
    register_import,
)
from modules.gestion_idse_sua.nominas.match_service import confirm_match, manual_search, match_worker
from modules.gestion_idse_sua.nominas.movement_bridge import convert_results_to_movements
from modules.gestion_idse_sua.nominas.period_signals import collect_period_signals
from modules.gestion_idse_sua.nominas.planta_cliente_service import confirm_planta_cliente
from modules.gestion_idse_sua.nominas.schema import ensure_gis_nominas_tables
from modules.gestion_idse_sua.nominas.sheet_inspector import inspect_sheet
from modules.gestion_idse_sua.nominas.ui_helpers import format_period_hint, group_attendance_rows, parse_suggested_period
from modules.exportacion_imss.exportacion_service import obtener_patrones
from modules.roles_access import can_access_comparativo, normalized_role


def _staging_path(import_id: int) -> Path:
    base = Path(tempfile.gettempdir()) / "gis_nomina_imports"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"import_{import_id}.xlsx"


def _db_from_app() -> sqlite3.Connection:
    from flask import current_app

    conn = sqlite3.connect(str(current_app.config["DATABASE"]))
    conn.row_factory = sqlite3.Row
    return conn


def _require_comparativo() -> None:
    role = normalized_role(g.user)
    if not can_access_comparativo(role):
        abort(403)


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
            recent = conn.execute(
                "SELECT id, original_filename, uploaded_at, status FROM gis_nomina_imports ORDER BY id DESC LIMIT 10"
            ).fetchall()
            return render_template(
                "gestion_idse_sua/nominas/index.html",
                clientes=_clientes_disponibles(),
                recent=[dict(r) for r in recent],
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
                uploaded_by=str(g.user.get("username") if g.user else ""),
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
            for s in repo.list_sheets(conn, import_id):
                if s.get("confirmed_classification") != "nomina":
                    continue
                period = parse_suggested_period(s.get("suggested_period_json"))
                sheets.append({**dict(s), "period_hint": format_period_hint(period), "period": period})
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
            if not staging.is_file():
                flash("Vuelva a cargar el archivo Excel para continuar.", "error")
                return redirect(url_for("gestion_idse_sua.nominas_index"))

            file_bytes = staging.read_bytes()
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

            period = confirm_period(
                conn,
                sheet_id,
                fecha_inicio=request.form.get("fecha_inicio", ""),
                fecha_fin=request.form.get("fecha_fin", ""),
                cliente=(request.form.get("cliente") or "").strip() or None,
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
            enrich_workers(conn, int(period["id"]), hc)
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
            comparative = conn.execute(
                "SELECT * FROM gis_nomina_comparatives WHERE period_id = ? ORDER BY id DESC LIMIT 1",
                (period_id,),
            ).fetchone()
            results = repo.list_results(conn, int(comparative["id"])) if comparative else []
            comp_warnings = []
            if comparative and comparative["warnings_json"]:
                import json

                try:
                    comp_warnings = json.loads(comparative["warnings_json"])
                except json.JSONDecodeError:
                    comp_warnings = []
            return render_template(
                "gestion_idse_sua/nominas/workspace.html",
                period=dict(period),
                workers=enriched_workers,
                comparative=dict(comparative) if comparative else None,
                results=results,
                totals=summarize_results(results) if results else {},
                clientes=_clientes_disponibles(),
                patrones=obtener_patrones(),
                comp_warnings=comp_warnings,
                attendance=group_attendance_rows(attendance),
                attendance_by_id=attendance_by_id,
                trajectory=trajectory,
                legacy_url=url_for("comparativo.index"),
                movimientos_url=url_for("exportacion_imss.index"),
            )
        finally:
            conn.close()

    @route("/nominas/workspace/<int:period_id>/compare", methods=["POST"], endpoint="nominas_compare")
    def nominas_compare(period_id: int):
        _require_comparativo()
        cliente = (request.form.get("cliente") or "").strip()
        if not cliente:
            flash("Seleccione cliente antes de comparar.", "error")
            return redirect(url_for("gestion_idse_sua.nominas_workspace", period_id=period_id))
        conn = _db_from_app()
        try:
            run_comparative(
                conn,
                period_id=period_id,
                cliente=cliente,
                generated_by=str(g.user.get("username") if g.user else ""),
            )
            flash("Comparativo generado.", "success")
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
                confirmed_by=str(g.user.get("username") if g.user else ""),
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
            repo.update_result_decision(
                conn,
                result_id,
                decision_final=(request.form.get("decision_final") or "").strip(),
                tipo_sugerido=(request.form.get("tipo_sugerido") or "").strip() or None,
                fecha_sugerida=(request.form.get("fecha_sugerida") or "").strip() or None,
            )
            conn.commit()
            comp = conn.execute(
                "SELECT c.period_id FROM gis_nomina_results r JOIN gis_nomina_comparatives c ON c.id = r.comparative_id WHERE r.id = ?",
                (result_id,),
            ).fetchone()
            return redirect(url_for("gestion_idse_sua.nominas_workspace", period_id=int(comp["period_id"])))
        finally:
            conn.close()

    @route("/nominas/worker/<int:worker_id>/match", methods=["POST"], endpoint="nominas_confirm_match")
    def nominas_confirm_match(worker_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            search = manual_search(request.form.get("query", ""), request.form.get("campo", "nombre_completo"))
            if not search.get("encontrado"):
                flash("No se encontró trabajador en Headcount.", "error")
            else:
                datos = search.get("datos") or (search.get("opciones") or [None])[0]
                if datos:
                    from modules.gestion_idse_sua.nominas.match_service import _build_match

                    confirm_match(conn, worker_id, _build_match(datos, method="manual", confidence=1.0, status="manual"))
                    conn.commit()
                    flash("Match confirmado.", "success")
            period_id = conn.execute(
                "SELECT period_id FROM gis_nomina_workers WHERE id = ?",
                (worker_id,),
            ).fetchone()
            return redirect(url_for("gestion_idse_sua.nominas_workspace", period_id=int(period_id["period_id"])))
        finally:
            conn.close()

    @route("/nominas/comparative/<int:comparative_id>/export", methods=["GET"], endpoint="nominas_export")
    def nominas_export(comparative_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            out_path, filename = generate_comparative_excel(
                conn,
                comparative_id,
                username=str(g.user.get("username") if g.user else ""),
            )
            return send_file(
                out_path,
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
                corrected_by=str(g.user.get("username") if g.user else ""),
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
