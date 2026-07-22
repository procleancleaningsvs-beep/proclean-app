from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from modules.gestion_idse_sua.nominas.text_utils import json_dumps


def create_report(
    conn: sqlite3.Connection,
    *,
    cliente: str,
    mes: int,
    anio: int,
    created_by: str | None = None,
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """
        INSERT INTO gis_monthly_reports
            (cliente, mes, anio, estado, created_by, created_at, updated_at, warnings_json, version)
        VALUES (?, ?, ?, 'borrador', ?, ?, ?, '[]', '1.0')
        """,
        (cliente, mes, anio, created_by, now, now),
    )
    return int(cur.lastrowid)


def get_report(conn: sqlite3.Connection, report_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM gis_monthly_reports WHERE id = ?", (report_id,)).fetchone()
    return dict(row) if row else None


def list_recent_reports(conn: sqlite3.Connection, *, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.*,
               (SELECT COUNT(*) FROM gis_monthly_report_weeks w WHERE w.report_id = r.id) AS week_count,
               (SELECT COUNT(*) FROM gis_monthly_report_persons p WHERE p.report_id = r.id) AS person_count,
               (
                   SELECT COUNT(*)
                   FROM gis_monthly_report_persons p
                   WHERE p.report_id = r.id
                     AND p.match_status IN ('unmatched', 'review', 'suggested')
               ) AS pending_count
        FROM gis_monthly_reports r
        ORDER BY r.updated_at DESC, r.id DESC
        LIMIT ?
        """,
        (max(0, int(limit)),),
    ).fetchall()
    return [dict(r) for r in rows]


def update_report_status(
    conn: sqlite3.Connection,
    report_id: int,
    *,
    estado: str,
    warnings: list[str] | None = None,
    snapshot: dict[str, Any] | None = None,
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    fields = ["estado = ?", "updated_at = ?"]
    params: list[Any] = [estado, now]
    if warnings is not None:
        fields.append("warnings_json = ?")
        params.append(json_dumps(warnings))
    if snapshot is not None:
        fields.append("snapshot_json = ?")
        params.append(json_dumps(snapshot))
    params.append(report_id)
    conn.execute(
        f"UPDATE gis_monthly_reports SET {', '.join(fields)} WHERE id = ?",
        params,
    )


def replace_report_weeks(
    conn: sqlite3.Connection,
    report_id: int,
    weeks: list[dict[str, Any]],
) -> None:
    conn.execute("DELETE FROM gis_monthly_report_weeks WHERE report_id = ?", (report_id,))
    now = datetime.now().isoformat(timespec="seconds")
    for idx, week in enumerate(weeks):
        conn.execute(
            """
            INSERT INTO gis_monthly_report_weeks
                (report_id, period_id, sort_order, included_at, origin_ref)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                report_id,
                int(week["period_id"]),
                idx + 1,
                now,
                week.get("origin_ref"),
            ),
        )


def list_report_weeks(conn: sqlite3.Connection, report_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT w.*, p.fecha_inicio, p.fecha_fin, p.semana_num, s.sheet_name, i.original_filename, i.file_hash
        FROM gis_monthly_report_weeks w
        JOIN gis_nomina_periods p ON p.id = w.period_id
        JOIN gis_nomina_sheets s ON s.id = p.sheet_id
        JOIN gis_nomina_imports i ON i.id = s.import_id
        WHERE w.report_id = ?
        ORDER BY w.sort_order
        """,
        (report_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def replace_report_persons(
    conn: sqlite3.Connection,
    report_id: int,
    persons: list[dict[str, Any]],
) -> dict[str, int]:
    conn.execute("DELETE FROM gis_monthly_report_events WHERE report_id = ?", (report_id,))
    conn.execute("DELETE FROM gis_monthly_report_persons WHERE report_id = ?", (report_id,))
    id_map: dict[str, int] = {}
    for person in persons:
        cur = conn.execute(
            """
            INSERT INTO gis_monthly_report_persons
                (report_id, identity_key, worker_ids_json, num_empleado, nombre_nomina, nombre_hc,
                 match_method, match_status, nss, rfc, curp, sbc, clientes_json, plantas_json,
                 semanas_json, estado_mensual, totals_json, primera_a, ultima_a, afiliatorios_json,
                 warnings_json, daily_json, trajectory_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                person["identity_key"],
                json_dumps(person.get("worker_ids") or []),
                person.get("num_empleado"),
                person.get("nombre_nomina"),
                person.get("nombre_hc"),
                person.get("match_method"),
                person.get("match_status"),
                person.get("nss"),
                person.get("rfc"),
                person.get("curp"),
                person.get("sbc"),
                json_dumps(person.get("clientes") or []),
                json_dumps(person.get("plantas") or []),
                json_dumps(person.get("semanas") or []),
                person.get("estado_mensual"),
                json_dumps(person.get("totals") or {}),
                person.get("primera_a"),
                person.get("ultima_a"),
                json_dumps(person.get("afiliatorios") or {}),
                json_dumps(person.get("warnings") or []),
                json_dumps(person.get("daily") or []),
                json_dumps(person.get("trajectory") or {}),
            ),
        )
        id_map[str(person["identity_key"])] = int(cur.lastrowid)
    return id_map


def insert_report_events(
    conn: sqlite3.Connection,
    report_id: int,
    events: list[dict[str, Any]],
    *,
    person_id_map: dict[str, int],
) -> None:
    for event in events:
        identity_key = str(event.get("identity_key") or "")
        person_id = person_id_map.get(identity_key)
        if not person_id:
            continue
        conn.execute(
            """
            INSERT INTO gis_monthly_report_events
                (report_id, person_id, period_id, event_type_suggested, event_type_confirmed,
                 fecha_suggested, fecha_confirmed, estado, motivo, observaciones, segment_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                person_id,
                event.get("period_id"),
                event.get("event_type_suggested"),
                event.get("event_type_confirmed"),
                event.get("fecha_suggested"),
                event.get("fecha_confirmed"),
                event.get("estado") or "propuesto",
                event.get("motivo"),
                event.get("observaciones"),
                json_dumps(event.get("segment") or {}),
            ),
        )


def list_report_persons(conn: sqlite3.Connection, report_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM gis_monthly_report_persons WHERE report_id = ? ORDER BY nombre_nomina, id",
        (report_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_report_person(conn: sqlite3.Connection, person_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM gis_monthly_report_persons WHERE id = ?", (person_id,)).fetchone()
    return dict(row) if row else None


def list_report_events(conn: sqlite3.Connection, report_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT e.*, p.identity_key, p.nombre_nomina, p.num_empleado, p.nss, p.rfc, p.curp, p.sbc,
               p.nombre_hc, p.plantas_json, p.clientes_json
        FROM gis_monthly_report_events e
        JOIN gis_monthly_report_persons p ON p.id = e.person_id
        WHERE e.report_id = ?
        ORDER BY e.id
        """,
        (report_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_event(
    conn: sqlite3.Connection,
    event_id: int,
    *,
    event_type_confirmed: str | None = None,
    fecha_confirmed: str | None = None,
    estado: str | None = None,
    observaciones: str | None = None,
    decided_by: str | None = None,
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    row = conn.execute("SELECT * FROM gis_monthly_report_events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        raise ValueError("Evento no encontrado.")
    conn.execute(
        """
        UPDATE gis_monthly_report_events
        SET event_type_confirmed = COALESCE(?, event_type_confirmed),
            fecha_confirmed = COALESCE(?, fecha_confirmed),
            estado = COALESCE(?, estado),
            observaciones = COALESCE(?, observaciones),
            decided_by = COALESCE(?, decided_by),
            decided_at = ?
        WHERE id = ?
        """,
        (
            event_type_confirmed,
            fecha_confirmed,
            estado,
            observaciones,
            decided_by,
            now,
            event_id,
        ),
    )


def mark_event_conversion(
    conn: sqlite3.Connection,
    event_id: int,
    *,
    status: str,
    movimiento_id: str | None = None,
    motivo: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE gis_monthly_report_events
        SET estado = ?, movimiento_id = ?, motivo = COALESCE(?, motivo)
        WHERE id = ?
        """,
        (status, movimiento_id, motivo, event_id),
    )
