from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass
class NominaBaseRow:
    nombre_empleado: str
    cliente: str
    planta: str
    puesto: str
    banco: str
    cuenta: str


def ensure_nomina_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_asistencia_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            semana TEXT NOT NULL,
            fecha_inicio TEXT NOT NULL,
            fecha_fin TEXT NOT NULL,
            cliente TEXT NOT NULL,
            coordinador TEXT NOT NULL,
            filename TEXT NOT NULL,
            status TEXT NOT NULL,
            total_rows INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            warning_count INTEGER NOT NULL DEFAULT 0,
            created_by INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            raw_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_asistencia_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_id INTEGER NOT NULL,
            row_number INTEGER NOT NULL,
            nombre_empleado TEXT,
            cliente TEXT,
            planta TEXT,
            puesto TEXT,
            banco TEXT,
            cuenta TEXT,
            dia_1_header TEXT,
            dia_1_value TEXT,
            dia_2_header TEXT,
            dia_2_value TEXT,
            dia_3_header TEXT,
            dia_3_value TEXT,
            dia_4_header TEXT,
            dia_4_value TEXT,
            dia_5_header TEXT,
            dia_5_value TEXT,
            dia_6_header TEXT,
            dia_6_value TEXT,
            dia_7_header TEXT,
            dia_7_value TEXT,
            he TEXT,
            turnos_extra_normales TEXT,
            dias_cubiertos_normales TEXT,
            festivo_laborado TEXT,
            vacaciones_laboradas TEXT,
            prima_vacacional TEXT,
            bono TEXT,
            deducciones TEXT,
            observaciones TEXT,
            errors_json TEXT,
            warnings_json TEXT,
            FOREIGN KEY(import_id) REFERENCES nomina_asistencia_imports(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_asistencia_imports_cliente_fechas ON nomina_asistencia_imports(cliente, fecha_inicio, fecha_fin)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_asistencia_rows_import_id ON nomina_asistencia_rows(import_id)"
    )


def save_asistencia_import(
    db_path: str,
    payload: dict[str, Any],
    created_by: int | None,
    now_iso: str,
) -> int:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute(
            """
            INSERT INTO nomina_asistencia_imports (
                semana, fecha_inicio, fecha_fin, cliente, coordinador,
                filename, status, total_rows, error_count, warning_count,
                created_by, created_at, updated_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(payload.get("semana") or ""),
                str(payload.get("fecha_inicio") or ""),
                str(payload.get("fecha_fin") or ""),
                str(payload.get("cliente") or ""),
                str(payload.get("coordinador") or ""),
                str(payload.get("filename") or ""),
                str(payload.get("status") or "draft"),
                int(payload.get("total_rows") or 0),
                int(payload.get("error_count") or 0),
                int(payload.get("warning_count") or 0),
                created_by,
                now_iso,
                now_iso,
                json.dumps(payload.get("raw_json"), ensure_ascii=False),
            ),
        )
        import_id = int(cur.lastrowid)
        for row in payload.get("rows") or []:
            conn.execute(
                """
                INSERT INTO nomina_asistencia_rows (
                    import_id, row_number, nombre_empleado, cliente, planta, puesto, banco, cuenta,
                    dia_1_header, dia_1_value, dia_2_header, dia_2_value, dia_3_header, dia_3_value,
                    dia_4_header, dia_4_value, dia_5_header, dia_5_value, dia_6_header, dia_6_value,
                    dia_7_header, dia_7_value, he, turnos_extra_normales, dias_cubiertos_normales,
                    festivo_laborado, vacaciones_laboradas, prima_vacacional, bono, deducciones,
                    observaciones, errors_json, warnings_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    import_id,
                    int(row.get("row_number") or 0),
                    row.get("nombre_empleado"),
                    row.get("cliente"),
                    row.get("planta"),
                    row.get("puesto"),
                    row.get("banco"),
                    row.get("cuenta"),
                    row.get("dia_1_header"),
                    row.get("dia_1_value"),
                    row.get("dia_2_header"),
                    row.get("dia_2_value"),
                    row.get("dia_3_header"),
                    row.get("dia_3_value"),
                    row.get("dia_4_header"),
                    row.get("dia_4_value"),
                    row.get("dia_5_header"),
                    row.get("dia_5_value"),
                    row.get("dia_6_header"),
                    row.get("dia_6_value"),
                    row.get("dia_7_header"),
                    row.get("dia_7_value"),
                    row.get("he"),
                    row.get("turnos_extra_normales"),
                    row.get("dias_cubiertos_normales"),
                    row.get("festivo_laborado"),
                    row.get("vacaciones_laboradas"),
                    row.get("prima_vacacional"),
                    row.get("bono"),
                    row.get("deducciones"),
                    row.get("observaciones"),
                    json.dumps(row.get("errors") or [], ensure_ascii=False),
                    json.dumps(row.get("warnings") or [], ensure_ascii=False),
                ),
            )
        conn.commit()
        return import_id
    finally:
        conn.close()


def get_asistencia_import(db_path: str, import_id: int) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        imp = conn.execute(
            "SELECT * FROM nomina_asistencia_imports WHERE id = ?",
            (import_id,),
        ).fetchone()
        if imp is None:
            return None
        rows = conn.execute(
            "SELECT * FROM nomina_asistencia_rows WHERE import_id = ? ORDER BY row_number ASC, id ASC",
            (import_id,),
        ).fetchall()
        result = dict(imp)
        parsed_rows: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            d["errors"] = json.loads(d.get("errors_json") or "[]")
            d["warnings"] = json.loads(d.get("warnings_json") or "[]")
            parsed_rows.append(d)
        result["rows"] = parsed_rows
        result["raw_json"] = json.loads(result.get("raw_json") or "{}")
        return result
    finally:
        conn.close()


def get_latest_import_base_rows(
    db_path: str, cliente: str, before_start_date: date
) -> list[NominaBaseRow]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        imp = conn.execute(
            """
            SELECT id
            FROM nomina_asistencia_imports
            WHERE cliente = ?
              AND status = 'draft'
              AND date(fecha_inicio) < date(?)
            ORDER BY date(fecha_inicio) DESC, id DESC
            LIMIT 1
            """,
            (cliente.strip(), before_start_date.isoformat()),
        ).fetchone()
        if imp is None:
            return []
        rows = conn.execute(
            """
            SELECT nombre_empleado, cliente, planta, puesto, banco, cuenta
            FROM nomina_asistencia_rows
            WHERE import_id = ?
              AND TRIM(COALESCE(nombre_empleado, '')) <> ''
            ORDER BY row_number ASC, id ASC
            """,
            (int(imp["id"]),),
        ).fetchall()
        out: list[NominaBaseRow] = []
        for row in rows:
            out.append(
                NominaBaseRow(
                    nombre_empleado=str(row["nombre_empleado"] or ""),
                    cliente=str(row["cliente"] or ""),
                    planta=str(row["planta"] or ""),
                    puesto=str(row["puesto"] or ""),
                    banco=str(row["banco"] or ""),
                    cuenta=str(row["cuenta"] or ""),
                )
            )
        return out
    finally:
        conn.close()

