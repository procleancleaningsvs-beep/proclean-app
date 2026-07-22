from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from modules.gestion_idse_sua.nominas.text_utils import json_dumps


def create_import(
    conn: sqlite3.Connection,
    *,
    original_filename: str,
    file_hash: str,
    uploaded_by: str | None,
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """
        INSERT INTO gis_nomina_imports
            (original_filename, file_hash, uploaded_by, uploaded_at, status)
        VALUES (?, ?, ?, ?, 'uploaded')
        """,
        (original_filename, file_hash, uploaded_by, now),
    )
    return int(cur.lastrowid)


def add_sheet(conn: sqlite3.Connection, import_id: int, sheet: dict[str, Any]) -> int:
    period_json = json_dumps(sheet.get("suggested_period")) if sheet.get("suggested_period") else None
    cur = conn.execute(
        """
        INSERT INTO gis_nomina_sheets
            (import_id, sheet_index, sheet_name, is_hidden, suggested_classification,
             estimated_rows, suggested_period_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            import_id,
            sheet["sheet_index"],
            sheet["sheet_name"],
            1 if sheet.get("is_hidden") else 0,
            sheet.get("suggested_classification"),
            int(sheet.get("estimated_rows") or 0),
            period_json,
        ),
    )
    return int(cur.lastrowid)


def get_import(conn: sqlite3.Connection, import_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM gis_nomina_imports WHERE id = ?", (import_id,)).fetchone()
    return dict(row) if row else None


def list_sheets(conn: sqlite3.Connection, import_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM gis_nomina_sheets WHERE import_id = ? ORDER BY sheet_index",
        (import_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_sheet_classification(
    conn: sqlite3.Connection,
    sheet_id: int,
    classification: str,
) -> None:
    conn.execute(
        """
        UPDATE gis_nomina_sheets
        SET confirmed_classification = ?
        WHERE id = ?
        """,
        (classification, sheet_id),
    )


def upsert_period(conn: sqlite3.Connection, sheet_id: int, period: dict[str, Any], *, confirmed: bool) -> int:
    existing = conn.execute(
        "SELECT id FROM gis_nomina_periods WHERE sheet_id = ?",
        (sheet_id,),
    ).fetchone()
    values = (
        period.get("fecha_inicio"),
        period.get("fecha_fin"),
        period.get("semana_num"),
        period.get("source"),
        1 if confirmed else 0,
        period.get("cut_warning"),
        sheet_id,
    )
    if existing:
        conn.execute(
            """
            UPDATE gis_nomina_periods
            SET fecha_inicio = ?, fecha_fin = ?, semana_num = ?, detection_source = ?,
                user_confirmed = ?, cut_warning = ?
            WHERE sheet_id = ?
            """,
            values,
        )
        return int(existing["id"])
    cur = conn.execute(
        """
        INSERT INTO gis_nomina_periods
            (fecha_inicio, fecha_fin, semana_num, detection_source, user_confirmed, cut_warning, sheet_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        values,
    )
    return int(cur.lastrowid)


def get_period_for_sheet(conn: sqlite3.Connection, sheet_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM gis_nomina_periods WHERE sheet_id = ?", (sheet_id,)).fetchone()
    return dict(row) if row else None


def insert_workers(conn: sqlite3.Connection, period_id: int, workers: list[dict[str, Any]]) -> list[int]:
    ids: list[int] = []
    for worker in workers:
        cur = conn.execute(
            """
            INSERT INTO gis_nomina_workers
                (period_id, row_number, num_empleado, nombre_original, nombre_normalizado,
                 puesto, planta_original, planta_normalizada, cuenta, row_json,
                 cliente_sugerido, suggestion_source, suggestion_confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                period_id,
                worker["row_number"],
                worker.get("num_empleado"),
                worker["nombre_original"],
                worker["nombre_normalizado"],
                worker.get("puesto"),
                worker.get("planta_original"),
                worker.get("planta_normalizada"),
                worker.get("cuenta"),
                worker.get("row_json"),
                worker.get("cliente_sugerido"),
                worker.get("suggestion_source"),
                worker.get("suggestion_confidence"),
            ),
        )
        ids.append(int(cur.lastrowid))
    return ids


def list_workers(conn: sqlite3.Connection, period_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM gis_nomina_workers WHERE period_id = ? ORDER BY row_number",
        (period_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_worker_cliente(conn: sqlite3.Connection, worker_id: int, cliente: str) -> None:
    conn.execute(
        "UPDATE gis_nomina_workers SET cliente_confirmado = ? WHERE id = ?",
        (cliente, worker_id),
    )


def upsert_match(conn: sqlite3.Connection, worker_id: int, match: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO gis_nomina_matches
            (worker_id, headcount_key, match_method, confidence, status,
             nss, rfc, curp, hc_nombre, hc_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(worker_id) DO UPDATE SET
            headcount_key = excluded.headcount_key,
            match_method = excluded.match_method,
            confidence = excluded.confidence,
            status = excluded.status,
            nss = excluded.nss,
            rfc = excluded.rfc,
            curp = excluded.curp,
            hc_nombre = excluded.hc_nombre,
            hc_json = excluded.hc_json
        """,
        (
            worker_id,
            match.get("headcount_key"),
            match.get("match_method") or "none",
            match.get("confidence"),
            match.get("status") or "unmatched",
            match.get("nss"),
            match.get("rfc"),
            match.get("curp"),
            match.get("hc_nombre"),
            match.get("hc_json"),
        ),
    )


def get_match(conn: sqlite3.Connection, worker_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM gis_nomina_matches WHERE worker_id = ?", (worker_id,)).fetchone()
    return dict(row) if row else None


def create_comparative(
    conn: sqlite3.Connection,
    *,
    period_id: int,
    cliente: str,
    generated_by: str | None,
    warnings: list[str] | None = None,
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """
        INSERT INTO gis_nomina_comparatives
            (period_id, cliente, generated_at, generated_by, status, warnings_json)
        VALUES (?, ?, ?, ?, 'completed', ?)
        """,
        (period_id, cliente, now, generated_by, json_dumps(warnings or [])),
    )
    return int(cur.lastrowid)


def insert_result(conn: sqlite3.Connection, comparative_id: int, result: dict[str, Any]) -> int:
    cur = conn.execute(
        """
        INSERT INTO gis_nomina_results
            (comparative_id, worker_id, headcount_only, hc_nombre, resultado, semaforo,
             tipo_sugerido, fecha_sugerida, decision_final, observaciones)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            comparative_id,
            result.get("worker_id"),
            1 if result.get("headcount_only") else 0,
            result.get("hc_nombre"),
            result["resultado"],
            result["semaforo"],
            result.get("tipo_sugerido"),
            result.get("fecha_sugerida") or "",
            result.get("decision_final"),
            result.get("observaciones"),
        ),
    )
    return int(cur.lastrowid)


def list_results(conn: sqlite3.Connection, comparative_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.*, w.num_empleado, w.nombre_original, w.nombre_normalizado, w.planta_normalizada,
               w.cliente_confirmado, w.puesto, m.match_method, m.status AS match_status, m.confidence,
               m.nss, m.rfc, m.curp, m.hc_json, m.hc_nombre AS match_hc_nombre
        FROM gis_nomina_results r
        LEFT JOIN gis_nomina_workers w ON w.id = r.worker_id
        LEFT JOIN gis_nomina_matches m ON m.worker_id = r.worker_id
        WHERE r.comparative_id = ?
        ORDER BY r.id
        """,
        (comparative_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_comparative(conn: sqlite3.Connection, comparative_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM gis_nomina_comparatives WHERE id = ?", (comparative_id,)).fetchone()
    return dict(row) if row else None


def mark_result_conversion(
    conn: sqlite3.Connection,
    result_id: int,
    *,
    status: str,
    movimiento_id: str | None = None,
    exclusion_reason: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE gis_nomina_results
        SET conversion_status = ?, movimiento_id = ?, exclusion_reason = ?
        WHERE id = ?
        """,
        (status, movimiento_id, exclusion_reason, result_id),
    )


def set_import_status(conn: sqlite3.Connection, import_id: int, status: str) -> None:
    conn.execute(
        "UPDATE gis_nomina_imports SET status = ? WHERE id = ?",
        (status, import_id),
    )


def find_conflicting_periods(
    conn: sqlite3.Connection,
    *,
    fecha_inicio: str,
    fecha_fin: str,
    exclude_sheet_id: int | None = None,
) -> list[dict[str, Any]]:
    params: list[Any] = [fecha_inicio, fecha_fin]
    extra = ""
    if exclude_sheet_id is not None:
        extra = " AND p.sheet_id != ?"
        params.append(exclude_sheet_id)
    rows = conn.execute(
        f"""
        SELECT p.id, p.fecha_inicio, p.fecha_fin, s.sheet_name, i.original_filename
        FROM gis_nomina_periods p
        JOIN gis_nomina_sheets s ON s.id = p.sheet_id
        JOIN gis_nomina_imports i ON i.id = s.import_id
        WHERE p.user_confirmed = 1
          AND p.fecha_inicio = ?
          AND p.fecha_fin = ?
        {extra}
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def update_result_decision(
    conn: sqlite3.Connection,
    result_id: int,
    *,
    decision_final: str,
    tipo_sugerido: str | None = None,
    fecha_sugerida: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE gis_nomina_results
        SET decision_final = ?,
            tipo_sugerido = COALESCE(?, tipo_sugerido),
            fecha_sugerida = COALESCE(?, fecha_sugerida)
        WHERE id = ?
        """,
        (decision_final, tipo_sugerido, fecha_sugerida, result_id),
    )
