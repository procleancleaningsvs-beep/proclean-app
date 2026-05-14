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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_vacaciones_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente TEXT NOT NULL,
            source_filename TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            total_rows INTEGER NOT NULL DEFAULT 0,
            matched_count INTEGER NOT NULL DEFAULT 0,
            warning_count INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            created_by INTEGER,
            created_at TEXT NOT NULL,
            raw_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_vacaciones_empleados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_id INTEGER NOT NULL,
            nss TEXT,
            nombre_historico TEXT,
            nombre_normalizado TEXT,
            nombre_headcount TEXT,
            cliente TEXT,
            planta_historica TEXT,
            planta_headcount TEXT,
            fecha_ingreso_historica TEXT,
            fecha_ingreso_headcount TEXT,
            fecha_ingreso_usada TEXT,
            estatus_headcount TEXT,
            sueldo_historico REAL,
            sueldo_headcount REAL,
            sueldo_usado REAL,
            dias_vacaciones_historico REAL,
            dias_utilizados REAL,
            vacaciones_laboradas REAL,
            dias_pagados REAL,
            dias_restantes_historico REAL,
            dias_restantes_calculado REAL,
            prima_2025_pagada INTEGER NOT NULL DEFAULT 0,
            semana_pago_prima_2025 TEXT,
            prima_2026_pagada INTEGER NOT NULL DEFAULT 0,
            fecha_pago_prima_2026 TEXT,
            monto_total_historico REAL,
            monto_total_recalculado REAL,
            comentarios TEXT,
            match_status TEXT,
            match_score REAL,
            warnings_json TEXT,
            editable_json TEXT,
            updated_by INTEGER,
            updated_at TEXT,
            FOREIGN KEY(import_id) REFERENCES nomina_vacaciones_imports(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_vacaciones_import_id ON nomina_vacaciones_empleados(import_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_vacaciones_match_status ON nomina_vacaciones_empleados(match_status)"
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


def save_vacaciones_import(
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
            INSERT INTO nomina_vacaciones_imports (
                cliente, source_filename, file_hash, total_rows,
                matched_count, warning_count, error_count,
                created_by, created_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(payload.get("cliente") or ""),
                str(payload.get("source_filename") or ""),
                str(payload.get("file_hash") or ""),
                int(payload.get("total_rows") or 0),
                int(payload.get("matched_count") or 0),
                int(payload.get("warning_count") or 0),
                int(payload.get("error_count") or 0),
                created_by,
                now_iso,
                json.dumps(payload.get("raw_json") or {}, ensure_ascii=False),
            ),
        )
        import_id = int(cur.lastrowid)
        for row in payload.get("rows") or []:
            conn.execute(
                """
                INSERT INTO nomina_vacaciones_empleados (
                    import_id, nss, nombre_historico, nombre_normalizado, nombre_headcount, cliente,
                    planta_historica, planta_headcount, fecha_ingreso_historica, fecha_ingreso_headcount, fecha_ingreso_usada,
                    estatus_headcount, sueldo_historico, sueldo_headcount, sueldo_usado, dias_vacaciones_historico,
                    dias_utilizados, vacaciones_laboradas, dias_pagados, dias_restantes_historico, dias_restantes_calculado,
                    prima_2025_pagada, semana_pago_prima_2025, prima_2026_pagada, fecha_pago_prima_2026,
                    monto_total_historico, monto_total_recalculado, comentarios, match_status, match_score,
                    warnings_json, editable_json, updated_by, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    import_id,
                    row.get("nss"),
                    row.get("nombre_historico"),
                    row.get("nombre_normalizado"),
                    row.get("nombre_headcount"),
                    row.get("cliente"),
                    row.get("planta_historica"),
                    row.get("planta_headcount"),
                    row.get("fecha_ingreso_historica"),
                    row.get("fecha_ingreso_headcount"),
                    row.get("fecha_ingreso_usada"),
                    row.get("estatus_headcount"),
                    row.get("sueldo_historico"),
                    row.get("sueldo_headcount"),
                    row.get("sueldo_usado"),
                    row.get("dias_vacaciones_historico"),
                    row.get("dias_utilizados"),
                    row.get("vacaciones_laboradas"),
                    row.get("dias_pagados"),
                    row.get("dias_restantes_historico"),
                    row.get("dias_restantes_calculado"),
                    int(bool(row.get("prima_2025_pagada"))),
                    row.get("semana_pago_prima_2025"),
                    int(bool(row.get("prima_2026_pagada"))),
                    row.get("fecha_pago_prima_2026"),
                    row.get("monto_total_historico"),
                    row.get("monto_total_recalculado"),
                    row.get("comentarios"),
                    row.get("match_status"),
                    row.get("match_score"),
                    json.dumps(row.get("warnings") or [], ensure_ascii=False),
                    json.dumps(row.get("editable_json") or {}, ensure_ascii=False),
                    row.get("updated_by"),
                    row.get("updated_at"),
                ),
            )
        conn.commit()
        return import_id
    finally:
        conn.close()


def get_vacaciones_import(db_path: str, import_id: int) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        imp = conn.execute(
            "SELECT * FROM nomina_vacaciones_imports WHERE id = ?",
            (import_id,),
        ).fetchone()
        if imp is None:
            return None
        rows = conn.execute(
            "SELECT * FROM nomina_vacaciones_empleados WHERE import_id = ? ORDER BY id ASC",
            (import_id,),
        ).fetchall()
        result = dict(imp)
        result["raw_json"] = json.loads(result.get("raw_json") or "{}")
        parsed_rows: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            d["warnings"] = json.loads(d.get("warnings_json") or "[]")
            d["editable_json"] = json.loads(d.get("editable_json") or "{}")
            parsed_rows.append(d)
        result["rows"] = parsed_rows
        return result
    finally:
        conn.close()


def list_vacaciones_empleados(
    db_path: str,
    *,
    cliente: str | None = None,
    match_status: str | None = None,
    activo: str | None = None,
    con_alerta: bool | None = None,
    prima_pagada: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        query = """
            SELECT v.*, i.created_at AS import_created_at
            FROM nomina_vacaciones_empleados v
            JOIN nomina_vacaciones_imports i ON i.id = v.import_id
            WHERE 1=1
        """
        params: list[Any] = []
        if cliente:
            query += " AND LOWER(TRIM(COALESCE(v.cliente,''))) = LOWER(TRIM(?))"
            params.append(cliente)
        if match_status:
            query += " AND COALESCE(v.match_status,'') = ?"
            params.append(match_status)
        if activo:
            if activo == "activo":
                query += " AND UPPER(COALESCE(v.estatus_headcount,'')) LIKE '%ACTIVO%'"
            elif activo == "inactivo":
                query += " AND UPPER(COALESCE(v.estatus_headcount,'')) NOT LIKE '%ACTIVO%'"
        if con_alerta is True:
            query += " AND COALESCE(v.warnings_json,'[]') <> '[]'"
        if prima_pagada:
            if prima_pagada == "si":
                query += " AND (COALESCE(v.prima_2025_pagada,0)=1 OR COALESCE(v.prima_2026_pagada,0)=1)"
            elif prima_pagada == "no":
                query += " AND COALESCE(v.prima_2025_pagada,0)=0 AND COALESCE(v.prima_2026_pagada,0)=0"
        query += " ORDER BY v.id DESC LIMIT ?"
        params.append(int(limit))
        rows = conn.execute(query, tuple(params)).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            d["warnings"] = json.loads(d.get("warnings_json") or "[]")
            d["editable_json"] = json.loads(d.get("editable_json") or "{}")
            out.append(d)
        return out
    finally:
        conn.close()


def get_vacaciones_stats(db_path: str) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        total = int(conn.execute("SELECT COUNT(*) FROM nomina_vacaciones_empleados").fetchone()[0])
        matched = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_vacaciones_empleados WHERE match_status IN ('exact_nss','match_name')"
            ).fetchone()[0]
        )
        no_match = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_vacaciones_empleados WHERE match_status IN ('no_match','pending_review')"
            ).fetchone()[0]
        )
        discrep_fecha = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_vacaciones_empleados WHERE fecha_ingreso_historica <> fecha_ingreso_headcount AND TRIM(COALESCE(fecha_ingreso_historica,''))<>'' AND TRIM(COALESCE(fecha_ingreso_headcount,''))<>''"
            ).fetchone()[0]
        )
        primas_pagadas = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_vacaciones_empleados WHERE COALESCE(prima_2025_pagada,0)=1 OR COALESCE(prima_2026_pagada,0)=1"
            ).fetchone()[0]
        )
        pendientes = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_vacaciones_empleados WHERE COALESCE(warnings_json,'[]') <> '[]' OR match_status IN ('pending_review','no_match')"
            ).fetchone()[0]
        )
        return {
            "empleados_importados": total,
            "empleados_match_headcount": matched,
            "empleados_sin_match": no_match,
            "discrepancias_fecha_ingreso": discrep_fecha,
            "primas_pagadas": primas_pagadas,
            "saldos_pendientes_revision": pendientes,
        }
    finally:
        conn.close()


def get_vacaciones_empleado(db_path: str, row_id: int) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM nomina_vacaciones_empleados WHERE id = ?",
            (row_id,),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["warnings"] = json.loads(d.get("warnings_json") or "[]")
        d["editable_json"] = json.loads(d.get("editable_json") or "{}")
        return d
    finally:
        conn.close()


def update_vacaciones_empleado(
    db_path: str,
    row_id: int,
    updates: dict[str, Any],
    *,
    updated_by: int | None,
    updated_at: str,
) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            UPDATE nomina_vacaciones_empleados
            SET fecha_ingreso_usada = ?,
                sueldo_usado = ?,
                dias_utilizados = ?,
                vacaciones_laboradas = ?,
                dias_pagados = ?,
                dias_restantes_calculado = ?,
                prima_2025_pagada = ?,
                prima_2026_pagada = ?,
                fecha_pago_prima_2026 = ?,
                comentarios = ?,
                editable_json = ?,
                updated_by = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                updates.get("fecha_ingreso_usada"),
                updates.get("sueldo_usado"),
                updates.get("dias_utilizados"),
                updates.get("vacaciones_laboradas"),
                updates.get("dias_pagados"),
                updates.get("dias_restantes_calculado"),
                int(bool(updates.get("prima_2025_pagada"))),
                int(bool(updates.get("prima_2026_pagada"))),
                updates.get("fecha_pago_prima_2026"),
                updates.get("comentarios"),
                json.dumps(updates.get("editable_json") or {}, ensure_ascii=False),
                updated_by,
                updated_at,
                int(row_id),
            ),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()

