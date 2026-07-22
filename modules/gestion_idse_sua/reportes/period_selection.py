from __future__ import annotations

import sqlite3
from typing import Any

from modules.gestion_idse_sua.nominas import repository as nom_repo
from modules.gestion_idse_sua.nominas.text_utils import normalize_upper
from modules.gestion_idse_sua.nominas.trajectory_service import resolve_worker_identity
from modules.gestion_idse_sua.reportes.date_utils import period_intersects_month


MIN_WEEKS = 4
MAX_WEEKS = 6
ACTIVE_CODES = frozenset({"A", "I", "V"})


def _worker_cliente(worker: dict[str, Any]) -> str:
    return normalize_upper(worker.get("cliente_confirmado") or "")


def list_available_weeks(
    conn: sqlite3.Connection,
    *,
    cliente: str,
    mes: int,
    anio: int,
) -> list[dict[str, Any]]:
    cliente_norm = normalize_upper(cliente)
    rows = conn.execute(
        """
        SELECT p.id AS period_id, p.fecha_inicio, p.fecha_fin, p.semana_num, p.cut_warning,
               s.id AS sheet_id, s.sheet_name, i.id AS import_id, i.original_filename, i.file_hash
        FROM gis_nomina_periods p
        JOIN gis_nomina_sheets s ON s.id = p.sheet_id
        JOIN gis_nomina_imports i ON i.id = s.import_id
        WHERE p.user_confirmed = 1
          AND p.fecha_inicio IS NOT NULL
          AND p.fecha_fin IS NOT NULL
        ORDER BY p.fecha_inicio, p.id
        """
    ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        period = dict(row)
        if not period_intersects_month(period["fecha_inicio"], period["fecha_fin"], mes=mes, anio=anio):
            continue
        workers = nom_repo.list_workers(conn, int(period["period_id"]))
        client_workers = [w for w in workers if _worker_cliente(w) == cliente_norm]
        if not client_workers and workers:
            continue
        confirmed_ids = 0
        pending_ids = 0
        attendance_count = 0
        warnings: list[str] = []
        for worker in client_workers:
            match = nom_repo.get_match(conn, int(worker["id"]))
            _, resolved = resolve_worker_identity(worker, match)
            if resolved:
                confirmed_ids += 1
            else:
                pending_ids += 1
        attendance_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM gis_nomina_attendance a
            JOIN gis_nomina_workers w ON w.id = a.worker_id
            WHERE a.period_id = ?
              AND COALESCE(w.cliente_confirmado, '') = ?
            """,
            (period["period_id"], cliente_norm),
        ).fetchone()[0]
        if not attendance_count and client_workers:
            warnings.append("Sin asistencia interpretada para este cliente en la semana.")
        if period.get("cut_warning"):
            warnings.append(str(period["cut_warning"]))
        out.append(
            {
                "period_id": int(period["period_id"]),
                "fecha_inicio": period["fecha_inicio"],
                "fecha_fin": period["fecha_fin"],
                "semana_num": period.get("semana_num"),
                "sheet_name": period["sheet_name"],
                "original_filename": period["original_filename"],
                "file_hash": period["file_hash"],
                "import_id": int(period["import_id"]),
                "worker_count": len(client_workers),
                "confirmed_identities": confirmed_ids,
                "pending_identities": pending_ids,
                "attendance_rows": int(attendance_count),
                "attendance_status": "ok" if attendance_count else "missing",
                "warnings": warnings,
            }
        )
    return out


def validate_week_selection(
    conn: sqlite3.Connection,
    period_ids: list[int],
    *,
    cliente: str,
    mes: int,
    anio: int,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    unique_ids = list(dict.fromkeys(int(pid) for pid in period_ids))
    if len(unique_ids) < MIN_WEEKS:
        errors.append(f"Se requieren al menos {MIN_WEEKS} semanas confirmadas.")
    if len(unique_ids) > MAX_WEEKS:
        errors.append(f"Máximo {MAX_WEEKS} semanas por reporte.")
    if len(unique_ids) != len(period_ids):
        warnings.append("Se eliminaron periodos duplicados en la selección.")

    cliente_norm = normalize_upper(cliente)
    selected: list[dict[str, Any]] = []
    seen_ranges: dict[tuple[str, str], int] = {}
    for period_id in unique_ids:
        row = conn.execute(
            """
            SELECT p.*, s.sheet_name, i.original_filename, i.file_hash
            FROM gis_nomina_periods p
            JOIN gis_nomina_sheets s ON s.id = p.sheet_id
            JOIN gis_nomina_imports i ON i.id = s.import_id
            WHERE p.id = ?
            """,
            (period_id,),
        ).fetchone()
        if row is None:
            errors.append(f"Periodo {period_id} no encontrado.")
            continue
        period = dict(row)
        if not period.get("user_confirmed"):
            errors.append(f"Periodo {period_id} no está confirmado.")
            continue
        if not period_intersects_month(period["fecha_inicio"], period["fecha_fin"], mes=mes, anio=anio):
            errors.append(f"Periodo {period_id} no intersecta {mes:02d}/{anio}.")
            continue
        key = (str(period["fecha_inicio"]), str(period["fecha_fin"]))
        if key in seen_ranges:
            warnings.append(
                f"Mismo rango {key[0]}–{key[1]} en periodos {seen_ranges[key]} y {period_id}."
            )
        seen_ranges[key] = period_id
        workers = nom_repo.list_workers(conn, period_id)
        if not any(_worker_cliente(w) == cliente_norm for w in workers):
            warnings.append(f"Periodo {period_id} no tiene trabajadores confirmados para {cliente}.")
        selected.append(
            {
                "period_id": period_id,
                "fecha_inicio": period["fecha_inicio"],
                "fecha_fin": period["fecha_fin"],
                "origin_ref": f"{period['file_hash']}:{period['sheet_name']}",
            }
        )

    if len(selected) >= 2:
        for i, left in enumerate(selected):
            for right in selected[i + 1 :]:
                if _periods_overlap(left, right):
                    warnings.append(
                        f"Semanas superpuestas: {left['period_id']} y {right['period_id']}."
                    )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "weeks": selected,
    }


def _periods_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    from modules.gestion_idse_sua.reportes.date_utils import parse_period_date

    a0 = parse_period_date(left["fecha_inicio"])
    a1 = parse_period_date(left["fecha_fin"])
    b0 = parse_period_date(right["fecha_inicio"])
    b1 = parse_period_date(right["fecha_fin"])
    return a0 <= b1 and b0 <= a1
