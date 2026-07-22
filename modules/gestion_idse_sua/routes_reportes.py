"""Rutas del espacio Reportes mensuales GIS."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from flask import abort, flash, g, jsonify, redirect, render_template, request, send_file, url_for

from modules.comparativo.headcount_service import obtener_activos
from modules.gestion_idse_sua.nominas.schema import ensure_gis_nominas_tables
from modules.gestion_idse_sua.reportes.consolidation_service import generate_monthly_report
from modules.gestion_idse_sua.reportes.excel_export import generate_monthly_excel
from modules.gestion_idse_sua.reportes.headcount_compare import compare_report_to_headcount
from modules.gestion_idse_sua.reportes.movement_bridge import convert_events_to_movements
from modules.gestion_idse_sua.reportes.period_selection import list_available_weeks
from modules.gestion_idse_sua.reportes.repository import (
    create_report,
    get_report,
    get_report_person,
    list_recent_reports,
    list_report_events,
    list_report_persons,
    list_report_weeks,
    update_event,
    update_report_status,
)
from modules.gestion_idse_sua.reportes.schema import ensure_gis_monthly_tables
from modules.roles_access import can_access_comparativo, normalized_role


def _db_from_app() -> sqlite3.Connection:
    from flask import current_app

    conn = sqlite3.connect(str(current_app.config["DATABASE"]))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    ensure_gis_nominas_tables(conn)
    ensure_gis_monthly_tables(conn)


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


def _parse_json_field(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def register_reportes_routes(bp, *, login_required) -> None:
    def route(rule, **options):
        def decorator(f):
            wrapped = login_required(f)
            endpoint = options.pop("endpoint", f.__name__)
            bp.add_url_rule(rule, endpoint=endpoint, view_func=wrapped, **options)
            return wrapped

        return decorator

    @route("/reportes", methods=["GET"], endpoint="reportes_index")
    def reportes_index():
        _require_comparativo()
        conn = _db_from_app()
        try:
            _ensure_tables(conn)
            conn.commit()
            recent = list_recent_reports(conn, limit=10)
            for row in recent:
                row["mes_label"] = f"{int(row['mes']):02d}/{int(row['anio'])}"
            return render_template(
                "gestion_idse_sua/reportes/index.html",
                recent=recent,
                clientes=_clientes_disponibles(),
                legacy_url=url_for("comparativo.reporte_mensual_index"),
                nominas_url=url_for("gestion_idse_sua.nominas_index"),
            )
        finally:
            conn.close()

    @route("/reportes/nuevo", methods=["GET", "POST"], endpoint="reportes_create")
    def reportes_create():
        _require_comparativo()
        clientes = _clientes_disponibles()
        if request.method == "GET":
            return render_template(
                "gestion_idse_sua/reportes/create.html",
                clientes=clientes,
                legacy_url=url_for("comparativo.reporte_mensual_index"),
                nominas_url=url_for("gestion_idse_sua.nominas_index"),
            )

        cliente = str(request.form.get("cliente") or "").strip()
        mes = int(request.form.get("mes") or 0)
        anio = int(request.form.get("anio") or 0)
        period_ids = [int(x) for x in request.form.getlist("period_ids") if str(x).isdigit()]
        if not cliente or mes < 1 or mes > 12 or anio < 2000:
            flash("Cliente, mes y año son obligatorios.", "error")
            return redirect(url_for("gestion_idse_sua.reportes_create"))

        conn = _db_from_app()
        try:
            _ensure_tables(conn)
            report_id = create_report(
                conn,
                cliente=cliente,
                mes=mes,
                anio=anio,
                created_by=getattr(g.user, "username", None),
            )
            generate_monthly_report(
                conn,
                report_id=report_id,
                period_ids=period_ids,
                cliente=cliente,
                mes=mes,
                anio=anio,
            )
            conn.commit()
            flash("Reporte mensual generado.", "success")
            return redirect(url_for("gestion_idse_sua.reportes_workspace", report_id=report_id))
        except ValueError as exc:
            conn.rollback()
            flash(str(exc), "error")
            return redirect(url_for("gestion_idse_sua.reportes_create"))
        finally:
            conn.close()

    @route("/reportes/api/semanas", methods=["GET"], endpoint="reportes_api_semanas")
    def reportes_api_semanas():
        _require_comparativo()
        cliente = str(request.args.get("cliente") or "").strip()
        mes = int(request.args.get("mes") or 0)
        anio = int(request.args.get("anio") or 0)
        if not cliente or not mes or not anio:
            return jsonify({"ok": False, "error": "Parámetros incompletos"}), 400
        conn = _db_from_app()
        try:
            _ensure_tables(conn)
            weeks = list_available_weeks(conn, cliente=cliente, mes=mes, anio=anio)
            return jsonify({"ok": True, "weeks": weeks})
        finally:
            conn.close()

    @route("/reportes/<int:report_id>", methods=["GET"], endpoint="reportes_workspace")
    def reportes_workspace(report_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            _ensure_tables(conn)
            report = get_report(conn, report_id)
            if report is None:
                abort(404)
            persons = list_report_persons(conn, report_id)
            events = list_report_events(conn, report_id)
            weeks = list_report_weeks(conn, report_id)
            snapshot = _parse_json_field(report.get("snapshot_json"), {})
            for person in persons:
                person["totals"] = _parse_json_field(person.get("totals_json"), {})
                person["daily"] = _parse_json_field(person.get("daily_json"), [])
                person["warnings"] = _parse_json_field(person.get("warnings_json"), [])
                person["plantas"] = _parse_json_field(person.get("plantas_json"), [])
                person["clientes"] = _parse_json_field(person.get("clientes_json"), [])
            return render_template(
                "gestion_idse_sua/reportes/workspace.html",
                report=report,
                persons=persons,
                events=events,
                weeks=weeks,
                pendientes=snapshot.get("pendientes") or [],
                warnings=_parse_json_field(report.get("warnings_json"), []),
                legacy_url=url_for("comparativo.reporte_mensual_index"),
            )
        finally:
            conn.close()

    @route("/reportes/<int:report_id>/persona/<int:person_id>", methods=["GET"], endpoint="reportes_person")
    def reportes_person(report_id: int, person_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            _ensure_tables(conn)
            report = get_report(conn, report_id)
            person = get_report_person(conn, person_id)
            if report is None or person is None or int(person["report_id"]) != report_id:
                abort(404)
            person["totals"] = _parse_json_field(person.get("totals_json"), {})
            person["daily"] = _parse_json_field(person.get("daily_json"), [])
            person["trajectory"] = _parse_json_field(person.get("trajectory_json"), {})
            person["warnings"] = _parse_json_field(person.get("warnings_json"), [])
            events = [e for e in list_report_events(conn, report_id) if int(e["person_id"]) == person_id]
            return render_template(
                "gestion_idse_sua/reportes/person.html",
                report=report,
                person=person,
                events=events,
            )
        finally:
            conn.close()

    @route("/reportes/<int:report_id>/evento/<int:event_id>", methods=["POST"], endpoint="reportes_event_action")
    def reportes_event_action(report_id: int, event_id: int):
        _require_comparativo()
        action = str(request.form.get("action") or "").strip()
        conn = _db_from_app()
        try:
            _ensure_tables(conn)
            if action == "confirm":
                update_event(
                    conn,
                    event_id,
                    event_type_confirmed=str(request.form.get("tipo") or request.form.get("event_type") or "").upper() or None,
                    fecha_confirmed=str(request.form.get("fecha") or "").strip() or None,
                    estado="confirmado",
                    observaciones=str(request.form.get("observaciones") or "").strip() or None,
                    decided_by=getattr(g.user, "username", None),
                )
            elif action == "discard":
                update_event(
                    conn,
                    event_id,
                    estado="descartado",
                    observaciones=str(request.form.get("observaciones") or "").strip() or None,
                    decided_by=getattr(g.user, "username", None),
                )
            elif action == "correct":
                update_event(
                    conn,
                    event_id,
                    event_type_confirmed=str(request.form.get("tipo") or "").upper() or None,
                    fecha_confirmed=str(request.form.get("fecha") or "").strip() or None,
                    estado="confirmado",
                    observaciones=str(request.form.get("observaciones") or "").strip() or None,
                    decided_by=getattr(g.user, "username", None),
                )
            else:
                flash("Acción no reconocida.", "error")
                return redirect(url_for("gestion_idse_sua.reportes_workspace", report_id=report_id))
            update_report_status(conn, report_id, estado="en_revision")
            conn.commit()
            flash("Evento actualizado.", "success")
        finally:
            conn.close()
        return redirect(url_for("gestion_idse_sua.reportes_workspace", report_id=report_id))

    @route("/reportes/<int:report_id>/exportar", methods=["GET"], endpoint="reportes_export")
    def reportes_export(report_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            _ensure_tables(conn)
            out_path, filename = generate_monthly_excel(
                conn,
                report_id,
                username=getattr(g.user, "username", None),
            )
            return send_file(
                out_path,
                as_attachment=True,
                download_name=filename,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("gestion_idse_sua.reportes_workspace", report_id=report_id))
        finally:
            conn.close()

    @route("/reportes/<int:report_id>/movimientos", methods=["POST"], endpoint="reportes_send_movements")
    def reportes_send_movements(report_id: int):
        _require_comparativo()
        event_ids = [int(x) for x in request.form.getlist("event_ids") if str(x).isdigit()]
        conn = _db_from_app()
        try:
            _ensure_tables(conn)
            result = convert_events_to_movements(conn, event_ids=event_ids)
            converted = len(result.get("converted_ids") or [])
            excluded = len(result.get("excluded") or [])
            flash(f"Movimientos enviados: {converted}. Excluidos: {excluded}.", "success")
        finally:
            conn.close()
        return redirect(url_for("gestion_idse_sua.reportes_workspace", report_id=report_id))

    @route("/reportes/<int:report_id>/comparar-headcount", methods=["POST"], endpoint="reportes_compare_headcount")
    def reportes_compare_headcount(report_id: int):
        _require_comparativo()
        conn = _db_from_app()
        try:
            _ensure_tables(conn)
            result = compare_report_to_headcount(conn, report_id)
            if result.get("historical_warning"):
                flash("Advertencia: Headcount es una fotografía actual, no histórica.", "warning")
            diff_count = len(result.get("differences") or [])
            flash(f"Comparación completada. Diferencias encontradas: {diff_count}.", "info")
        finally:
            conn.close()
        return redirect(url_for("gestion_idse_sua.reportes_workspace", report_id=report_id))
