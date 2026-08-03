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
    file_content: bytes | None = None,
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """
        INSERT INTO gis_nomina_imports
            (original_filename, file_hash, uploaded_by, uploaded_at, file_content, status)
        VALUES (?, ?, ?, ?, ?, 'uploaded')
        """,
        (original_filename, file_hash, uploaded_by, now, file_content),
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


def archive_import(
    conn: sqlite3.Connection,
    import_id: int,
    *,
    archived_by: str | None,
    reason: str | None = None,
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE gis_nomina_imports
        SET archived_at = ?, archived_by = ?, archive_reason = ?
        WHERE id = ?
        """,
        (now, archived_by, (reason or "").strip() or None, import_id),
    )


def restore_import(conn: sqlite3.Connection, import_id: int) -> None:
    conn.execute(
        """
        UPDATE gis_nomina_imports
        SET archived_at = NULL, archived_by = NULL, archive_reason = NULL
        WHERE id = ?
        """,
        (import_id,),
    )


def import_dependencies(conn: sqlite3.Connection, import_id: int) -> dict[str, int]:
    periods = conn.execute(
        """
        SELECT p.id
        FROM gis_nomina_periods p
        JOIN gis_nomina_sheets s ON s.id = p.sheet_id
        WHERE s.import_id = ?
        """,
        (import_id,),
    ).fetchall()
    period_ids = [int(row["id"]) for row in periods]
    out = {"periods": len(period_ids), "comparatives": 0, "movements": 0, "reports": 0}
    if not period_ids:
        return out
    marks = ",".join("?" for _ in period_ids)
    out["comparatives"] = int(conn.execute(
        f"SELECT COUNT(*) FROM gis_nomina_comparatives WHERE period_id IN ({marks})", period_ids
    ).fetchone()[0])
    out["movements"] = int(conn.execute(
        f"""
        SELECT COUNT(*)
        FROM gis_nomina_results r
        JOIN gis_nomina_comparatives c ON c.id = r.comparative_id
        WHERE c.period_id IN ({marks}) AND r.movimiento_id IS NOT NULL
        """,
        period_ids,
    ).fetchone()[0])
    has_monthly = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gis_monthly_report_weeks'"
    ).fetchone()
    if has_monthly:
        out["reports"] = int(conn.execute(
            f"SELECT COUNT(DISTINCT report_id) FROM gis_monthly_report_weeks WHERE period_id IN ({marks})",
            period_ids,
        ).fetchone()[0])
    return out


def resolve_import_resume(conn: sqlite3.Connection, import_id: int) -> dict[str, Any]:
    imp = get_import(conn, import_id)
    if imp is None:
        return {"state": "missing"}
    if imp.get("archived_at"):
        return {"state": "archived", "import_id": import_id}
    sheet_count = int(conn.execute(
        "SELECT COUNT(*) FROM gis_nomina_sheets WHERE import_id = ?", (import_id,)
    ).fetchone()[0])
    if not sheet_count:
        return {"state": "incomplete", "import_id": import_id}
    comp = conn.execute(
        """
        SELECT c.id AS comparative_id, c.period_id
        FROM gis_nomina_comparatives c
        JOIN gis_nomina_periods p ON p.id = c.period_id
        JOIN gis_nomina_sheets s ON s.id = p.sheet_id
        WHERE s.import_id = ?
        ORDER BY c.id DESC LIMIT 1
        """,
        (import_id,),
    ).fetchone()
    if comp:
        return {"state": "comparative_ready", **dict(comp)}
    period = conn.execute(
        """
        SELECT p.id AS period_id, p.user_confirmed,
               (SELECT COUNT(*) FROM gis_nomina_workers w WHERE w.period_id = p.id) AS worker_count
        FROM gis_nomina_periods p
        JOIN gis_nomina_sheets s ON s.id = p.sheet_id
        WHERE s.import_id = ?
        ORDER BY p.id DESC LIMIT 1
        """,
        (import_id,),
    ).fetchone()
    if period and int(period["worker_count"] or 0) > 0:
        return {"state": "period_confirmed", **dict(period)}
    classified = conn.execute(
        "SELECT COUNT(*) FROM gis_nomina_sheets WHERE import_id = ? AND confirmed_classification IS NOT NULL",
        (import_id,),
    ).fetchone()[0]
    if classified:
        return {"state": "period_pending", "import_id": import_id}
    return {"state": "classified" if imp.get("status") == "classified" else "uploaded", "import_id": import_id}


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


def insert_attendance(conn: sqlite3.Connection, row: dict[str, Any]) -> int:
    cur = conn.execute(
        """
        INSERT INTO gis_nomina_attendance
            (worker_id, period_id, column_index, column_number, fecha_iso, header_original,
             code_original, code_normalized, interpretation_status, warning, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["worker_id"],
            row["period_id"],
            row["column_index"],
            row.get("column_number"),
            row["fecha_iso"],
            row.get("header_original"),
            row.get("code_original"),
            row.get("code_normalized"),
            row.get("interpretation_status") or "ok",
            row.get("warning"),
            row["created_at"],
            row["updated_at"],
        ),
    )
    return int(cur.lastrowid)


def list_attendance_for_period(conn: sqlite3.Connection, period_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT a.*, w.num_empleado, w.nombre_normalizado, w.nombre_original
        FROM gis_nomina_attendance a
        JOIN gis_nomina_workers w ON w.id = a.worker_id
        WHERE a.period_id = ?
        ORDER BY w.row_number, a.column_index
        """,
        (period_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_attendance_for_worker(conn: sqlite3.Connection, worker_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM gis_nomina_attendance
        WHERE worker_id = ?
        ORDER BY fecha_iso, column_index
        """,
        (worker_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_attendance(conn: sqlite3.Connection, attendance_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM gis_nomina_attendance WHERE id = ?", (attendance_id,)).fetchone()
    return dict(row) if row else None


def apply_attendance_correction(
    conn: sqlite3.Connection,
    attendance_id: int,
    *,
    code_corrected: str,
    corrected_by: str | None,
    reason: str | None = None,
) -> int:
    row = get_attendance(conn, attendance_id)
    if row is None:
        raise ValueError("Registro de asistencia no encontrado.")
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """
        INSERT INTO gis_nomina_attendance_corrections
            (attendance_id, code_original, code_interpreted, code_corrected, corrected_by, corrected_at, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            attendance_id,
            row.get("code_original") or "",
            row.get("code_normalized") or "",
            code_corrected,
            corrected_by,
            now,
            reason,
        ),
    )
    conn.execute(
        """
        UPDATE gis_nomina_attendance
        SET code_normalized = ?, interpretation_status = 'corrected', updated_at = ?
        WHERE id = ?
        """,
        (code_corrected, now, attendance_id),
    )
    return int(cur.lastrowid)


def list_attendance_corrections(conn: sqlite3.Connection, attendance_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM gis_nomina_attendance_corrections WHERE attendance_id = ? ORDER BY id",
        (attendance_id,),
    ).fetchall()
    return [dict(r) for r in rows]
