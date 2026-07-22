from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from openpyxl.worksheet.worksheet import Worksheet

from modules.gestion_idse_sua.nominas import repository as repo
from modules.gestion_idse_sua.nominas.attendance_parser import extract_attendance_for_workers


def persist_attendance(
    conn: sqlite3.Connection,
    *,
    period_id: int,
    worker_ids: list[int],
    worker_row_numbers: list[int],
    attendance_payload: dict[str, Any],
) -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute("DELETE FROM gis_nomina_attendance WHERE period_id = ?", (period_id,))
    rows_written = 0
    block = attendance_payload.get("block")
    parsed_rows: dict[int, list[dict[str, Any]]] = attendance_payload.get("rows") or {}

    for worker_id, row_number in zip(worker_ids, worker_row_numbers, strict=False):
        days = parsed_rows.get(int(row_number)) or []
        for day in days:
            repo.insert_attendance(
                conn,
                {
                    "worker_id": worker_id,
                    "period_id": period_id,
                    "column_index": day["column_index"],
                    "column_number": day.get("column_number"),
                    "fecha_iso": day["fecha_iso"],
                    "header_original": day.get("header_original"),
                    "code_original": day.get("code_original"),
                    "code_normalized": day.get("code_normalized"),
                    "interpretation_status": day.get("interpretation_status") or "ok",
                    "warning": day.get("warning"),
                    "created_at": now,
                    "updated_at": now,
                },
            )
            rows_written += 1

    return {
        "rows_written": rows_written,
        "block_detected": block is not None,
        "block_confidence": (block or {}).get("confidence"),
        "warnings": attendance_payload.get("warnings") or [],
    }


def parse_and_persist_attendance(
    conn: sqlite3.Connection,
    ws: Worksheet,
    *,
    period_id: int,
    header_row: int,
    nombre_col: int,
    fecha_inicio: str,
    fecha_fin: str,
    worker_ids: list[int],
    worker_row_numbers: list[int],
) -> dict[str, Any]:
    payload = extract_attendance_for_workers(
        ws,
        header_row=header_row,
        nombre_col=nombre_col,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        worker_rows=worker_row_numbers,
    )
    return persist_attendance(
        conn,
        period_id=period_id,
        worker_ids=worker_ids,
        worker_row_numbers=worker_row_numbers,
        attendance_payload=payload,
    )


def list_period_attendance(conn: sqlite3.Connection, period_id: int) -> list[dict[str, Any]]:
    return repo.list_attendance_for_period(conn, period_id)


def trajectory_for_periods(
    conn: sqlite3.Connection,
    period_ids: list[int],
) -> dict[str, Any]:
    from modules.gestion_idse_sua.nominas.trajectory_service import build_trajectories_for_workers

    workers: list[dict[str, Any]] = []
    attendance_rows: list[dict[str, Any]] = []
    matches: dict[int, dict[str, Any] | None] = {}
    for period_id in period_ids:
        period_workers = repo.list_workers(conn, period_id)
        workers.extend(period_workers)
        attendance_rows.extend(repo.list_attendance_for_period(conn, period_id))
        for worker in period_workers:
            matches[int(worker["id"])] = repo.get_match(conn, int(worker["id"]))

    return build_trajectories_for_workers(workers, attendance_rows, matches)


def correct_attendance_code(
    conn: sqlite3.Connection,
    *,
    attendance_id: int,
    code_corrected: str,
    corrected_by: str | None,
    reason: str | None = None,
) -> dict[str, Any]:
    from modules.gestion_idse_sua.nominas.attendance_parser import normalize_attendance_code

    parsed = normalize_attendance_code(code_corrected)
    if parsed["status"] == "review" and not parsed["normalized"]:
        raise ValueError("Código de corrección no reconocido.")
    canonical = parsed["normalized"] or parsed["original"].upper()
    correction_id = repo.apply_attendance_correction(
        conn,
        attendance_id,
        code_corrected=canonical,
        corrected_by=corrected_by,
        reason=reason,
    )
    conn.commit()
    return {"correction_id": correction_id, "code_normalized": canonical}
