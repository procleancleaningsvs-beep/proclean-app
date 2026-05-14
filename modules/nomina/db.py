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
    nss: str = ""


def _normalize_cliente_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _migrate_nomina_imports_schema(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(nomina_asistencia_imports)").fetchall()}
    if "original_filename" not in cols:
        conn.execute("ALTER TABLE nomina_asistencia_imports ADD COLUMN original_filename TEXT")
    if "file_hash" not in cols:
        conn.execute("ALTER TABLE nomina_asistencia_imports ADD COLUMN file_hash TEXT")
    if "clientes_json" not in cols:
        conn.execute("ALTER TABLE nomina_asistencia_imports ADD COLUMN clientes_json TEXT")
    if "headcount_source" not in cols:
        conn.execute("ALTER TABLE nomina_asistencia_imports ADD COLUMN headcount_source TEXT")


def _migrate_nomina_rows_schema(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(nomina_asistencia_rows)").fetchall()}
    if "nss" not in cols:
        conn.execute("ALTER TABLE nomina_asistencia_rows ADD COLUMN nss TEXT")
    if "headcount_match_status" not in cols:
        conn.execute("ALTER TABLE nomina_asistencia_rows ADD COLUMN headcount_match_status TEXT")
    if "headcount_match_score" not in cols:
        conn.execute("ALTER TABLE nomina_asistencia_rows ADD COLUMN headcount_match_score REAL")
    if "headcount_source" not in cols:
        conn.execute("ALTER TABLE nomina_asistencia_rows ADD COLUMN headcount_source TEXT")


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
            original_filename TEXT,
            file_hash TEXT,
            clientes_json TEXT,
            headcount_source TEXT,
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
    _migrate_nomina_imports_schema(conn)
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
            nss TEXT,
            headcount_match_status TEXT,
            headcount_match_score REAL,
            headcount_source TEXT,
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
    _migrate_nomina_rows_schema(conn)
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
                filename, original_filename, file_hash,
                clientes_json, headcount_source,
                status, total_rows, error_count, warning_count,
                created_by, created_at, updated_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(payload.get("semana") or ""),
                str(payload.get("fecha_inicio") or ""),
                str(payload.get("fecha_fin") or ""),
                str(payload.get("cliente") or ""),
                str(payload.get("coordinador") or ""),
                str(payload.get("filename") or ""),
                str(payload.get("original_filename") or ""),
                str(payload.get("file_hash") or ""),
                json.dumps(payload.get("clientes") or [], ensure_ascii=False),
                str(payload.get("headcount_source") or ""),
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
                    nss, headcount_match_status, headcount_match_score, headcount_source,
                    dia_1_header, dia_1_value, dia_2_header, dia_2_value, dia_3_header, dia_3_value,
                    dia_4_header, dia_4_value, dia_5_header, dia_5_value, dia_6_header, dia_6_value,
                    dia_7_header, dia_7_value, he, turnos_extra_normales, dias_cubiertos_normales,
                    festivo_laborado, vacaciones_laboradas, prima_vacacional, bono, deducciones,
                    observaciones, errors_json, warnings_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
                    row.get("nss"),
                    row.get("headcount_match_status"),
                    float(row.get("headcount_match_score") or 0.0),
                    row.get("headcount_source"),
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
        result["clientes"] = json.loads(result.get("clientes_json") or "[]")
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
            SELECT i.id
            FROM nomina_asistencia_imports i
            JOIN nomina_asistencia_rows r ON r.import_id = i.id
            WHERE LOWER(TRIM(r.cliente)) = LOWER(TRIM(?))
              AND i.status = 'draft'
              AND date(i.fecha_inicio) < date(?)
            ORDER BY date(i.fecha_inicio) DESC, i.id DESC
            LIMIT 1
            """,
            (_normalize_cliente_key(cliente), before_start_date.isoformat()),
        ).fetchone()
        if imp is None:
            return []
        rows = conn.execute(
            """
            SELECT nombre_empleado, cliente, planta, puesto, banco, cuenta,
                   COALESCE(nss, '') AS nss
            FROM nomina_asistencia_rows
            WHERE import_id = ?
              AND LOWER(TRIM(cliente)) = LOWER(TRIM(?))
              AND TRIM(COALESCE(nombre_empleado, '')) <> ''
              AND TRIM(COALESCE(errors_json, '[]')) IN ('[]', '')
            ORDER BY row_number ASC, id ASC
            """,
            (int(imp["id"]), _normalize_cliente_key(cliente)),
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
                    nss=str(row["nss"] or ""),
                )
            )
        return out
    finally:
        conn.close()


def get_latest_import_base_rows_multi(
    db_path: str, clientes: list[str], before_start_date: date
) -> tuple[list[NominaBaseRow], list[str]]:
    seen_nss: set[str] = set()
    seen_name: set[tuple[str, str]] = set()
    out: list[NominaBaseRow] = []
    warnings: list[str] = []
    for cliente in clientes:
        for row in get_latest_import_base_rows(db_path, cliente, before_start_date):
            nss_key = str(row.nss or "").strip()
            name_key = (
                str(row.nombre_empleado or "").strip().casefold(),
                str(row.cliente or "").strip().casefold(),
            )
            if nss_key and nss_key in seen_nss:
                warnings.append(
                    f"Duplicado base omitido por NSS {nss_key} en cliente {row.cliente or cliente}."
                )
                continue
            if not nss_key and name_key in seen_name:
                warnings.append(
                    f"Duplicado base omitido por nombre '{row.nombre_empleado}' en cliente {row.cliente or cliente}."
                )
                continue
            if nss_key:
                seen_nss.add(nss_key)
            seen_name.add(name_key)
            out.append(row)
    return out, warnings


def nomina_dashboard_overview(db_path: str, recent_limit: int = 10) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        total_imports = int(
            conn.execute("SELECT COUNT(*) FROM nomina_asistencia_imports").fetchone()[0]
        )
        pending_warnings = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_asistencia_imports WHERE warning_count > 0"
            ).fetchone()[0]
        )
        recientes = conn.execute(
            """
            SELECT id, semana, cliente, coordinador, status, total_rows, error_count, warning_count, created_at
            FROM nomina_asistencia_imports
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ?
            """,
            (int(recent_limit),),
        ).fetchall()
        return {
            "total_imports": total_imports,
            "pending_warnings": pending_warnings,
            "recent_imports": [dict(row) for row in recientes],
        }
    finally:
        conn.close()


def nomina_clientes_from_history(db_path: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT TRIM(cliente) AS cliente
            FROM nomina_asistencia_rows
            WHERE TRIM(COALESCE(cliente, '')) <> ''
            ORDER BY cliente
            """
        ).fetchall()
        return [str(r["cliente"] or "").strip() for r in rows if str(r["cliente"] or "").strip()]
    finally:
        conn.close()


def nomina_history_rows_for_headcount_fallback(db_path: str) -> list[dict[str, str]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT nombre_empleado, cliente, nss
            FROM nomina_asistencia_rows
            WHERE TRIM(COALESCE(nombre_empleado, '')) <> ''
              AND TRIM(COALESCE(nss, '')) <> ''
            ORDER BY id DESC
            """
        ).fetchall()
        out: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for row in rows:
            nombre = str(row["nombre_empleado"] or "").strip()
            cliente = str(row["cliente"] or "").strip()
            nss = str(row["nss"] or "").strip()
            key = (nombre.casefold(), cliente.casefold(), nss)
            if key in seen:
                continue
            seen.add(key)
            out.append({"nombre_empleado": nombre, "cliente": cliente, "nss": nss})
        return out
    finally:
        conn.close()

