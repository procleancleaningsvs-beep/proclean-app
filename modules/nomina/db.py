from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

from modules.nomina.config import (
    WARN_LOCALIDAD_FRONTERA_DEMOTION_BLOCKED,
    WARN_LOCALIDAD_FRONTERA_IMPORT_UNKNOWN,
    WARN_SAME_NSS_MULTIPLE_CLIENTS,
)


@dataclass
class NominaBaseRow:
    nombre_empleado: str
    cliente: str
    planta: str
    puesto: str
    banco: str
    cuenta: str
    nss: str = ""
    numero_empleado: str = ""


def _normalize_cliente_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _salario_operativo_differs(a: Any, b: Any, *, eps: float = 0.01) -> bool:
    if a is None or b is None:
        return False
    try:
        return abs(float(a) - float(b)) > eps
    except (TypeError, ValueError):
        return False


def _nss_merge_conflict(existing: dict[str, Any], row: dict[str, Any]) -> bool:
    """Mismo NSS pero datos incompatibles (cliente/planta/salario): no fusionar en silencio."""
    nss = str(row.get("nss") or "").strip()
    if not nss or str(existing.get("nss") or "").strip() != nss:
        return False
    ex_c = _normalize_cliente_key(str(existing.get("cliente") or ""))
    row_c = _normalize_cliente_key(str(row.get("cliente") or ""))
    if ex_c and row_c and ex_c != row_c:
        return True
    ex_p = _normalize_cliente_key(str(existing.get("planta") or ""))
    row_p = _normalize_cliente_key(str(row.get("planta") or ""))
    if ex_p and row_p and ex_p != row_p:
        return True
    if _salario_operativo_differs(existing.get("salario_operativo"), row.get("salario_operativo")):
        return True
    return False


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
    if "original_file_blob" not in cols:
        conn.execute("ALTER TABLE nomina_asistencia_imports ADD COLUMN original_file_blob BLOB")
    if "deleted_at" not in cols:
        conn.execute("ALTER TABLE nomina_asistencia_imports ADD COLUMN deleted_at TEXT")
    if "deleted_by" not in cols:
        conn.execute("ALTER TABLE nomina_asistencia_imports ADD COLUMN deleted_by INTEGER")


def _asistencia_import_activa_sql(alias: str) -> str:
    """Importación no oculta por soft delete desde dashboard (auditoría)."""
    return f"(COALESCE(TRIM({alias}.deleted_at), '') = '')"


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
    if "horas_extra_normales" not in cols:
        # Master v4: TURNOS EXTRA NORMALES renombrado a HORAS EXTRA NORMALES.
        # Se mantiene la columna legacy para no romper importaciones previas.
        conn.execute("ALTER TABLE nomina_asistencia_rows ADD COLUMN horas_extra_normales TEXT")


def _migrate_nomina_parametros_schema(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(nomina_empleado_parametros)").fetchall()}
    if "record_kind" not in cols:
        conn.execute(
            "ALTER TABLE nomina_empleado_parametros ADD COLUMN record_kind TEXT DEFAULT 'import'"
        )
    if "is_active" not in cols:
        conn.execute(
            "ALTER TABLE nomina_empleado_parametros ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
        )


def _migrate_nomina_calculo_rows_neto411(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(nomina_calculo_rows)").fetchall()}
    for name, decl in (
        ("base_neto_simple", "REAL"),
        ("neto_simple_operativo", "REAL"),
        ("neto_redondeado", "REAL"),
        ("ajuste_al_neto", "REAL"),
        ("neto_a_pagar_final", "REAL"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE nomina_calculo_rows ADD COLUMN {name} {decl}")


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
            horas_extra_normales TEXT,
            dias_cubiertos_normales TEXT,
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
            headcount_source TEXT,
            headcount_raw_status TEXT,
            warnings_json TEXT,
            editable_json TEXT,
            updated_by INTEGER,
            updated_at TEXT,
            FOREIGN KEY(import_id) REFERENCES nomina_vacaciones_imports(id) ON DELETE CASCADE
        )
        """
    )
    _migrate_nomina_vacaciones_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_vacaciones_eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empleado_id INTEGER,
            worker_nss TEXT,
            worker_nombre_normalizado TEXT,
            source TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_date TEXT,
            period_label TEXT,
            days REAL,
            amount REAL,
            notes TEXT,
            imported_from_file TEXT,
            import_batch_id INTEGER,
            created_by INTEGER,
            created_at TEXT NOT NULL,
            is_reviewed INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(empleado_id) REFERENCES nomina_vacaciones_empleados(id) ON DELETE SET NULL,
            FOREIGN KEY(import_batch_id) REFERENCES nomina_vacaciones_imports(id) ON DELETE SET NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_vacaciones_backups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            backup_type TEXT NOT NULL,
            summary_json TEXT,
            payload_json TEXT NOT NULL,
            created_by INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_vacaciones_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            usuario_id INTEGER,
            registros_afectados INTEGER NOT NULL DEFAULT 0,
            backup_id INTEGER,
            detalle_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_vacaciones_import_id ON nomina_vacaciones_empleados(import_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_vacaciones_match_status ON nomina_vacaciones_empleados(match_status)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_infonavit_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            registro_patronal TEXT,
            fecha_corte TEXT,
            total_avisos_reportado INTEGER NOT NULL DEFAULT 0,
            total_rows INTEGER NOT NULL DEFAULT 0,
            active_count INTEGER NOT NULL DEFAULT 0,
            modified_count INTEGER NOT NULL DEFAULT 0,
            suspended_count INTEGER NOT NULL DEFAULT 0,
            vsm_count INTEGER NOT NULL DEFAULT 0,
            warning_count INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            source_filename TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            created_by INTEGER,
            created_at TEXT NOT NULL,
            raw_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_infonavit_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_id INTEGER NOT NULL,
            nss TEXT,
            numero_credito TEXT,
            folio_aviso TEXT,
            nombre_trabajador TEXT,
            nombre_normalizado TEXT,
            tipo_aviso TEXT,
            motivo_aviso TEXT,
            fecha_aviso TEXT,
            descuento_raw TEXT,
            descuento_monto_pesos REAL,
            descuento_factor_vsm REAL,
            umi_usada REAL,
            descuento_cf_calculada REAL,
            tipo_descuento TEXT,
            tipo_valor_descuento TEXT,
            estatus_infonavit TEXT,
            nombre_headcount TEXT,
            cliente_headcount TEXT,
            planta_headcount TEXT,
            estatus_headcount TEXT,
            match_status TEXT,
            match_score REAL,
            warnings_json TEXT,
            editable_json TEXT,
            updated_by INTEGER,
            updated_at TEXT,
            FOREIGN KEY(import_id) REFERENCES nomina_infonavit_imports(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_infonavit_rows_import_id ON nomina_infonavit_rows(import_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_infonavit_rows_match_status ON nomina_infonavit_rows(match_status)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_parametros_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo_importacion TEXT NOT NULL,
            cliente TEXT,
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
        CREATE TABLE IF NOT EXISTS nomina_empleado_parametros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT,
            nombre_normalizado TEXT,
            nss TEXT,
            numero_empleado TEXT,
            codigo_contpaq TEXT,
            cliente TEXT,
            planta TEXT,
            puesto TEXT,
            banco TEXT,
            cuenta TEXT,
            localidad TEXT,
            localidad_normalizada TEXT,
            salario_operativo REAL,
            valor_x_he REAL,
            zona_salario_raw TEXT,
            es_frontera INTEGER,
            salario_minimo_usado REAL,
            exento_he_usado REAL,
            fuente_salario_operativo TEXT,
            fuente_valor_x_he TEXT,
            fuente_numero_empleado TEXT,
            fuente_nss TEXT,
            headcount_match_status TEXT,
            contpaq_match_status TEXT,
            nomina_match_status TEXT,
            warnings_json TEXT,
            editable_json TEXT,
            last_import_id INTEGER,
            updated_by INTEGER,
            updated_at TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_empleado_parametros_nss ON nomina_empleado_parametros(nss)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_empleado_parametros_nombre ON nomina_empleado_parametros(nombre_normalizado)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_empleado_parametros_cliente ON nomina_empleado_parametros(cliente)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_empleado_parametros_hc_match ON nomina_empleado_parametros(headcount_match_status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_parametros_imports_created ON nomina_parametros_imports(created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_parametros_imports_tipo ON nomina_parametros_imports(tipo_importacion)"
    )
    _migrate_nomina_parametros_schema(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_empleado_parametros_is_active ON nomina_empleado_parametros(is_active)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_empleado_parametros_record_kind ON nomina_empleado_parametros(record_kind)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_localidades_frontera (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente TEXT,
            localidad TEXT,
            localidad_normalizada TEXT NOT NULL,
            es_frontera INTEGER NOT NULL DEFAULT 0,
            source_filename TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            UNIQUE(cliente, localidad_normalizada)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_calculo_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asistencia_import_id INTEGER NOT NULL,
            cliente TEXT,
            clientes_json TEXT,
            fecha_inicio TEXT NOT NULL,
            fecha_fin TEXT NOT NULL,
            config_json TEXT,
            status TEXT NOT NULL,
            total_empleados INTEGER NOT NULL DEFAULT 0,
            warning_count INTEGER NOT NULL DEFAULT 0,
            block_count INTEGER NOT NULL DEFAULT 0,
            created_by INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            raw_json TEXT,
            FOREIGN KEY(asistencia_import_id) REFERENCES nomina_asistencia_imports(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_calculo_runs_created ON nomina_calculo_runs(created_at DESC, id DESC)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nomina_calculo_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            calculo_id INTEGER NOT NULL,
            asistencia_row_id INTEGER NOT NULL,
            parametro_empleado_id INTEGER,
            vacaciones_empleado_id INTEGER,
            infonavit_row_id INTEGER,
            nss TEXT,
            numero_empleado TEXT,
            nombre_empleado TEXT,
            cliente TEXT,
            planta TEXT,
            puesto TEXT,
            banco TEXT,
            cuenta TEXT,
            salario_operativo REAL,
            valor_x_he REAL,
            es_frontera INTEGER,
            smg_usado REAL,
            exento_he_usado REAL,
            dias_computables REAL,
            septimo_dia REAL,
            dias_pago REAL,
            horas_extra REAL,
            valor_he_fiscal REAL,
            valor_extra_operativo REAL,
            horas_extra_normales REAL,
            importe_horas_extra_normales REAL,
            dias_cubiertos_normales REAL,
            importe_dias_cubiertos_normales REAL,
            festivo_laborado_detected INTEGER,
            importe_festivo_laborado REAL,
            domingo_laborado_detected INTEGER,
            importe_domingo_laborado REAL,
            vacaciones_laboradas REAL,
            importe_vacaciones_laboradas REAL,
            prima_vacacional_aplicada INTEGER,
            dias_prima_vacacional_pendientes REAL,
            importe_prima_vacacional REAL,
            bono_manual REAL,
            bono_manual_clasificacion TEXT,
            deduccion_manual REAL,
            sueldo_base_smg REAL,
            concepto_gravable REAL,
            concepto_exento REAL,
            base_gravada REAL,
            isr REAL,
            bono_tpt REAL,
            prima_eficiencia REAL,
            infonavit_mensual REAL,
            infonavit_semanal REAL,
            infonavit_status TEXT,
            total_percepciones REAL,
            total_deducciones REAL,
            neto_simple REAL,
            neto_a_pagar REAL,
            base_neto_simple REAL,
            neto_simple_operativo REAL,
            neto_redondeado REAL,
            ajuste_al_neto REAL,
            neto_a_pagar_final REAL,
            warnings_json TEXT,
            blocks_json TEXT,
            detail_json TEXT,
            manual_overrides_json TEXT,
            row_status TEXT,
            updated_by INTEGER,
            updated_at TEXT,
            FOREIGN KEY(calculo_id) REFERENCES nomina_calculo_runs(id) ON DELETE CASCADE,
            FOREIGN KEY(asistencia_row_id) REFERENCES nomina_asistencia_rows(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nomina_calculo_rows_calculo ON nomina_calculo_rows(calculo_id)"
    )
    _migrate_nomina_calculo_rows_neto411(conn)
    from modules.nomina.headcount_snapshot import ensure_headcount_snapshot_tables

    ensure_headcount_snapshot_tables(conn)


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
                created_by, created_at, updated_at, raw_json, original_file_blob
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                payload.get("original_file_blob"),
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
                    dia_7_header, dia_7_value, he, horas_extra_normales, dias_cubiertos_normales,
                    vacaciones_laboradas, prima_vacacional, bono, deducciones,
                    observaciones, errors_json, warnings_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
                    row.get("horas_extra_normales"),
                    row.get("dias_cubiertos_normales"),
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


def list_asistencia_imports_master_hub(
    db_path: str,
    *,
    viewer_user_id: int | None,
    role: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Historial de Master de Asistencia: coordinador solo ve sus cargas; admin/nómina ven todo."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        role_l = (role or "").strip().lower()
        if role_l in {"admin", "nomina"}:
            rows = conn.execute(
                f"""
                SELECT id, semana, fecha_inicio, fecha_fin, cliente, coordinador, filename,
                       original_filename, clientes_json, total_rows, error_count, warning_count,
                       created_by, created_at,
                       CASE WHEN original_file_blob IS NULL THEN 0 ELSE 1 END AS has_file_blob
                FROM nomina_asistencia_imports
                WHERE {_asistencia_import_activa_sql("nomina_asistencia_imports")}
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT id, semana, fecha_inicio, fecha_fin, cliente, coordinador, filename,
                       original_filename, clientes_json, total_rows, error_count, warning_count,
                       created_by, created_at,
                       CASE WHEN original_file_blob IS NULL THEN 0 ELSE 1 END AS has_file_blob
                FROM nomina_asistencia_imports
                WHERE created_by IS NOT NULL AND created_by = ?
                  AND {_asistencia_import_activa_sql("nomina_asistencia_imports")}
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT ?
                """,
                (int(viewer_user_id or 0), int(limit)),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            d["clientes"] = json.loads(d.get("clientes_json") or "[]")
            out.append(d)
        return out
    finally:
        conn.close()


def delete_asistencia_import(db_path: str, import_id: int) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute("DELETE FROM nomina_asistencia_imports WHERE id = ?", (int(import_id),))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def soft_delete_nomina_asistencia_import(
    db_path: str,
    import_id: int,
    *,
    deleted_by_user_id: int,
    deleted_at_iso: str,
) -> bool:
    """Oculta la importación del historial dashboard (soft delete); no borra filas ni BLOB."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute(
            """
            UPDATE nomina_asistencia_imports
            SET deleted_at = ?, deleted_by = ?
            WHERE id = ?
              AND (COALESCE(TRIM(deleted_at), '') = '')
            """,
            (str(deleted_at_iso), int(deleted_by_user_id), int(import_id)),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def fetch_asistencia_original_file(db_path: str, import_id: int) -> tuple[str, bytes] | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT COALESCE(NULLIF(original_filename,''), filename) AS fn, original_file_blob AS blob
            FROM nomina_asistencia_imports
            WHERE id = ?
              AND (COALESCE(TRIM(deleted_at), '') = '')
            """,
            (int(import_id),),
        ).fetchone()
        if row is None or row["blob"] is None:
            return None
        return str(row["fn"] or "asistencia.xlsx"), bytes(row["blob"])
    finally:
        conn.close()


def get_asistencia_import(db_path: str, import_id: int) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        imp = conn.execute(
            "SELECT * FROM nomina_asistencia_imports WHERE id = ? AND (COALESCE(TRIM(deleted_at), '') = '')",
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
              AND (COALESCE(TRIM(i.deleted_at), '') = '')
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
            conn.execute(
                "SELECT COUNT(*) FROM nomina_asistencia_imports WHERE (COALESCE(TRIM(deleted_at), '') = '')"
            ).fetchone()[0]
        )
        pending_warnings = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM nomina_asistencia_imports
                WHERE warning_count > 0 AND (COALESCE(TRIM(deleted_at), '') = '')
                """
            ).fetchone()[0]
        )
        recientes = conn.execute(
            """
            SELECT id, semana, cliente, coordinador, status, total_rows, error_count, warning_count, created_at
            FROM nomina_asistencia_imports
            WHERE (COALESCE(TRIM(deleted_at), '') = '')
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


def get_nomina_dashboard_summary_fast(db_path: str, *, recent_limit: int = 12) -> dict[str, Any]:
    """KPIs del hub /nomina/ sin OneDrive — solo metadata Headcount (sin cargar filas)."""
    from modules.nomina.headcount_snapshot import (
        get_headcount_snapshot_meta_fast,
        headcount_snapshot_dashboard_message,
        is_headcount_snapshot_refreshing,
        is_headcount_snapshot_stale,
    )

    localidades = list_localidades_frontera(db_path)
    meta = get_headcount_snapshot_meta_fast(db_path)
    snapshot_valid = bool(meta.get("snapshot_valid", True))
    has_snapshot = snapshot_valid and bool(meta) and int(meta.get("total_rows") or 0) > 0
    if has_snapshot and str(meta.get("status") or "") == "ok":
        legacy = get_parametros_stats(db_path, None)
        param_stats = {
            **legacy,
            "activos_headcount": int(meta.get("activos_count") or 0),
            "stats_mode": "headcount_meta",
            "total_empleados": int(meta.get("total_rows") or 0),
        }
        headcount_source = "snapshot"
    elif not snapshot_valid:
        legacy = get_parametros_stats(db_path, None)
        param_stats = {
            **legacy,
            "activos_headcount": None,
            "stats_mode": "invalid",
        }
        headcount_source = "snapshot_invalid"
    else:
        param_stats = get_parametros_stats(db_path, None)
        headcount_source = "snapshot_missing"
    return {
        "dash": nomina_dashboard_overview(db_path, recent_limit=recent_limit),
        "vac_stats": get_vacaciones_stats(db_path),
        "inf_stats": get_infonavit_stats(db_path),
        "param_stats": param_stats,
        "param_localidades_count": len(localidades),
        "param_localidades_frontera_count": sum(1 for it in localidades if it.get("es_frontera")),
        "calc_kpis": nomina_calculo_dashboard_kpis(db_path),
        "headcount_source": headcount_source,
        "headcount_notice": headcount_snapshot_dashboard_message(db_path),
        "headcount_snapshot_meta": meta or None,
        "headcount_stale": is_headcount_snapshot_stale(db_path) if has_snapshot else True,
        "headcount_refreshing": is_headcount_snapshot_refreshing(db_path),
        "snapshot_invalid": not snapshot_valid,
    }


def nomina_clientes_from_history(db_path: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT TRIM(r.cliente) AS cliente
            FROM nomina_asistencia_rows r
            INNER JOIN nomina_asistencia_imports i ON i.id = r.import_id
            WHERE TRIM(COALESCE(r.cliente, '')) <> ''
              AND (COALESCE(TRIM(i.deleted_at), '') = '')
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
            SELECT r.nombre_empleado, r.cliente, r.nss
            FROM nomina_asistencia_rows r
            INNER JOIN nomina_asistencia_imports i ON i.id = r.import_id
            WHERE TRIM(COALESCE(r.nombre_empleado, '')) <> ''
              AND TRIM(COALESCE(r.nss, '')) <> ''
              AND (COALESCE(TRIM(i.deleted_at), '') = '')
            ORDER BY r.id DESC
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
                created_by, created_at, raw_json, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                int(payload.get("is_active", 1)),
            ),
        )
        import_id = int(cur.lastrowid)
        for row in payload.get("rows") or []:
            conn.execute(
                """
                INSERT INTO nomina_vacaciones_empleados (
                    import_id, nss, nombre_historico, nombre_normalizado, nombre_headcount, cliente,
                    planta_historica, planta_headcount, fecha_ingreso_historica, fecha_ingreso_headcount, fecha_ingreso_usada,
                    estatus_headcount, status_headcount, sueldo_historico, sueldo_headcount, sueldo_usado, dias_vacaciones_historico,
                    dias_generados, dias_utilizados, dias_utilizados_excel_resumen, dias_utilizados_calculado_semanal,
                    vacaciones_laboradas, dias_pagados, dias_restantes_historico,
                    dias_restantes_calculado, saldo_calculado, prima_pendiente,
                    prima_2025_pagada, semana_pago_prima_2025, prima_2026_pagada, fecha_pago_prima_2026,
                    monto_total_historico, monto_total_recalculado, comentarios, match_status, match_method, match_notes, match_score,
                    headcount_source, headcount_raw_status, is_active,
                    warnings_json, editable_json, updated_by, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
                    row.get("status_headcount") or row.get("estatus_headcount"),
                    row.get("sueldo_historico"),
                    row.get("sueldo_headcount"),
                    row.get("sueldo_usado"),
                    row.get("dias_vacaciones_historico"),
                    row.get("dias_generados"),
                    row.get("dias_utilizados"),
                    row.get("dias_utilizados_excel_resumen"),
                    row.get("dias_utilizados_calculado_semanal"),
                    row.get("vacaciones_laboradas"),
                    row.get("dias_pagados"),
                    row.get("dias_restantes_historico"),
                    row.get("dias_restantes_calculado"),
                    row.get("saldo_calculado"),
                    row.get("prima_pendiente"),
                    int(bool(row.get("prima_2025_pagada"))),
                    row.get("semana_pago_prima_2025"),
                    int(bool(row.get("prima_2026_pagada"))),
                    row.get("fecha_pago_prima_2026"),
                    row.get("monto_total_historico"),
                    row.get("monto_total_recalculado"),
                    row.get("comentarios"),
                    row.get("match_status"),
                    row.get("match_method"),
                    row.get("match_notes"),
                    row.get("match_score"),
                    row.get("headcount_source"),
                    row.get("headcount_raw_status"),
                    int(row.get("is_active", 1)),
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


def update_vacaciones_import_raw_json(
    db_path: str,
    import_id: int,
    raw_json: dict[str, Any],
    *,
    is_active: int | None = None,
) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        if is_active is None:
            cur = conn.execute(
                "UPDATE nomina_vacaciones_imports SET raw_json = ? WHERE id = ?",
                (json.dumps(raw_json or {}, ensure_ascii=False), int(import_id)),
            )
        else:
            cur = conn.execute(
                "UPDATE nomina_vacaciones_imports SET raw_json = ?, is_active = ? WHERE id = ?",
                (json.dumps(raw_json or {}, ensure_ascii=False), int(is_active), int(import_id)),
            )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def activate_vacaciones_import_batch(db_path: str, import_id: int) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        cur_imp = conn.execute(
            "UPDATE nomina_vacaciones_imports SET is_active = 1 WHERE id = ?",
            (int(import_id),),
        )
        cur_emp = conn.execute(
            """
            UPDATE nomina_vacaciones_empleados
            SET is_active = 1
            WHERE import_id = ?
            """,
            (int(import_id),),
        )
        cur_evt = conn.execute(
            """
            UPDATE nomina_vacaciones_eventos
            SET is_active = 1
            WHERE import_batch_id = ?
            """,
            (int(import_id),),
        )
        conn.commit()
        return {
            "importaciones": int(cur_imp.rowcount),
            "empleados": int(cur_emp.rowcount),
            "eventos": int(cur_evt.rowcount),
        }
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
    revision_status: str | None = None,
    import_id: int | None = None,
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
            if activo in {"activo", "1", "true", "TRUE"}:
                query += " AND UPPER(COALESCE(v.estatus_headcount,'')) LIKE '%ACTIVO%'"
            elif activo in {"inactivo", "0", "false", "FALSE"}:
                query += " AND UPPER(COALESCE(v.estatus_headcount,'')) NOT LIKE '%ACTIVO%'"
        if con_alerta is True:
            query += " AND COALESCE(v.warnings_json,'[]') <> '[]'"
        if prima_pagada:
            if prima_pagada in {"si", "1", "true", "TRUE"}:
                query += " AND (COALESCE(v.prima_2025_pagada,0)=1 OR COALESCE(v.prima_2026_pagada,0)=1)"
            elif prima_pagada in {"no", "0", "false", "FALSE"}:
                query += " AND COALESCE(v.prima_2025_pagada,0)=0 AND COALESCE(v.prima_2026_pagada,0)=0"
        if revision_status:
            query += " AND COALESCE(v.editable_json,'') LIKE ?"
            params.append(f'%\"revision_status\": \"{revision_status}\"%')
        if import_id is not None:
            query += " AND v.import_id = ?"
            params.append(int(import_id))
        query += " AND COALESCE(v.is_active, 1) = 1"
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
    return get_vacaciones_stats_by_import(db_path, import_id=None)


def get_latest_vacaciones_import_id(db_path: str) -> int | None:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT id FROM nomina_vacaciones_imports
            WHERE COALESCE(is_active, 1) = 1
            ORDER BY datetime(created_at) DESC, id DESC LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return int(row[0])
    finally:
        conn.close()


def list_vacaciones_imports(db_path: str, limit: int = 50) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, cliente, source_filename, total_rows, matched_count, warning_count, error_count, created_at
            FROM nomina_vacaciones_imports
            WHERE COALESCE(is_active, 1) = 1
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_vacaciones_stats_by_import(db_path: str, import_id: int | None) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if import_id is None:
            import_id = get_latest_vacaciones_import_id(db_path)
        if import_id is None:
            return {
                "empleados_importados": 0,
                "empleados_match_headcount": 0,
                "empleados_sin_match": 0,
                "discrepancias_fecha_ingreso": 0,
                "primas_pagadas": 0,
                "saldos_pendientes_revision": 0,
            }
        total = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_vacaciones_empleados WHERE import_id = ?",
                (int(import_id),),
            ).fetchone()[0]
        )
        matched = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_vacaciones_empleados WHERE import_id = ? AND match_status IN ('MATCH_OK','exact_nss','match_name','inactive_match','possible_reentry')",
                (int(import_id),),
            ).fetchone()[0]
        )
        no_match = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_vacaciones_empleados WHERE import_id = ? AND match_status IN ('SIN_MATCH','MATCH_AMBIGUO','PENDIENTE_REVISION','no_match','pending_review','probable_match')",
                (int(import_id),),
            ).fetchone()[0]
        )
        discrep_fecha = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_vacaciones_empleados WHERE import_id = ? AND fecha_ingreso_historica <> fecha_ingreso_headcount AND TRIM(COALESCE(fecha_ingreso_historica,''))<>'' AND TRIM(COALESCE(fecha_ingreso_headcount,''))<>''",
                (int(import_id),),
            ).fetchone()[0]
        )
        primas_pagadas = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_vacaciones_empleados WHERE import_id = ? AND (COALESCE(prima_2025_pagada,0)=1 OR COALESCE(prima_2026_pagada,0)=1)",
                (int(import_id),),
            ).fetchone()[0]
        )
        pendientes = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_vacaciones_empleados WHERE import_id = ? AND (COALESCE(warnings_json,'[]') <> '[]' OR match_status IN ('SIN_MATCH','MATCH_AMBIGUO','PENDIENTE_REVISION','no_match','pending_review','probable_match'))",
                (int(import_id),),
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
    current = get_vacaciones_empleado(db_path, row_id)
    if current is None:
        return False
    merged = dict(current)
    merged.update({k: v for k, v in updates.items() if v is not None or k in updates})
    if "warnings" in updates:
        merged["warnings"] = updates["warnings"]
    if "editable_json" in updates:
        merged["editable_json"] = updates["editable_json"]
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
                dias_generados = ?,
                saldo_calculado = ?,
                prima_pendiente = ?,
                dias_vacaciones_historico = ?,
                warnings_json = ?,
                prima_2025_pagada = ?,
                prima_2026_pagada = ?,
                fecha_pago_prima_2026 = ?,
                comentarios = ?,
                editable_json = ?,
                monto_total_recalculado = ?,
                updated_by = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                merged.get("fecha_ingreso_usada"),
                merged.get("sueldo_usado"),
                merged.get("dias_utilizados"),
                merged.get("vacaciones_laboradas"),
                merged.get("dias_pagados"),
                merged.get("dias_restantes_calculado"),
                merged.get("dias_generados"),
                merged.get("saldo_calculado"),
                merged.get("prima_pendiente"),
                merged.get("dias_vacaciones_historico"),
                json.dumps(merged.get("warnings") or [], ensure_ascii=False),
                int(bool(merged.get("prima_2025_pagada"))),
                int(bool(merged.get("prima_2026_pagada"))),
                merged.get("fecha_pago_prima_2026"),
                merged.get("comentarios"),
                json.dumps(merged.get("editable_json") or {}, ensure_ascii=False),
                merged.get("monto_total_recalculado"),
                updated_by,
                updated_at,
                int(row_id),
            ),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_vacaciones_empleado_calculo(
    db_path: str,
    row_id: int,
    row: dict[str, Any],
    *,
    updated_at: str,
) -> bool:
    return update_vacaciones_empleado(
        db_path,
        row_id,
        {
            "fecha_ingreso_usada": row.get("fecha_ingreso_usada"),
            "sueldo_usado": row.get("sueldo_usado"),
            "dias_utilizados": row.get("dias_utilizados"),
            "vacaciones_laboradas": row.get("vacaciones_laboradas"),
            "dias_pagados": row.get("dias_pagados"),
            "dias_restantes_calculado": row.get("dias_restantes_calculado"),
            "dias_generados": row.get("dias_generados"),
            "saldo_calculado": row.get("saldo_calculado"),
            "prima_pendiente": row.get("prima_pendiente"),
            "dias_vacaciones_historico": row.get("dias_vacaciones_historico"),
            "warnings": row.get("warnings") or [],
            "editable_json": row.get("editable_json") or {},
            "monto_total_recalculado": row.get("monto_total_recalculado"),
        },
        updated_by=None,
        updated_at=updated_at,
    )


def save_vacaciones_events(
    db_path: str,
    events: list[dict[str, Any]],
    *,
    created_by: int | None,
) -> int:
    if not events:
        return 0
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        count = 0
        for ev in events:
            conn.execute(
                """
                INSERT INTO nomina_vacaciones_eventos (
                    empleado_id, worker_nss, worker_nombre_normalizado, source, event_type,
                    event_date, period_label, days, amount, notes, imported_from_file,
                    import_batch_id, created_by, created_at, is_reviewed, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ev.get("empleado_id"),
                    ev.get("worker_nss"),
                    ev.get("worker_nombre_normalizado"),
                    str(ev.get("source") or "excel_historico_carrier"),
                    str(ev.get("event_type") or "migracion_historica"),
                    ev.get("event_date"),
                    ev.get("period_label"),
                    ev.get("days"),
                    ev.get("amount"),
                    ev.get("notes"),
                    ev.get("imported_from_file"),
                    ev.get("import_batch_id"),
                    created_by,
                    str(ev.get("created_at") or ""),
                    int(ev.get("is_reviewed") or 0),
                    int(ev.get("is_active", 1)),
                ),
            )
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()


def list_vacaciones_eventos(
    db_path: str,
    *,
    empleado_id: int | None = None,
    import_batch_id: int | None = None,
    active_only: bool = True,
    limit: int = 500,
) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        query = "SELECT * FROM nomina_vacaciones_eventos WHERE 1=1"
        params: list[Any] = []
        if empleado_id is not None:
            query += " AND empleado_id = ?"
            params.append(int(empleado_id))
        if import_batch_id is not None:
            query += " AND import_batch_id = ?"
            params.append(int(import_batch_id))
        if active_only:
            query += " AND COALESCE(is_active, 1) = 1"
        query += " ORDER BY datetime(created_at) DESC, id DESC LIMIT ?"
        params.append(int(limit))
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def create_vacaciones_backup(
    db_path: str,
    *,
    backup_type: str,
    summary: dict[str, Any],
    payload: dict[str, Any],
    created_by: int | None,
    now_iso: str,
) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO nomina_vacaciones_backups (backup_type, summary_json, payload_json, created_by, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                backup_type,
                json.dumps(summary, ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False),
                created_by,
                now_iso,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def archive_vacaciones_import_empleados(
    db_path: str,
    *,
    import_id: int | None,
    exclude_import_id: int | None = None,
) -> int:
    conn = sqlite3.connect(db_path)
    try:
        if import_id is not None:
            cur = conn.execute(
                """
                UPDATE nomina_vacaciones_empleados
                SET is_active = 0
                WHERE import_id = ? AND COALESCE(is_active, 1) = 1
                """,
                (int(import_id),),
            )
            conn.execute(
                """
                UPDATE nomina_vacaciones_eventos
                SET is_active = 0
                WHERE import_batch_id = ? AND COALESCE(is_active, 1) = 1
                """,
                (int(import_id),),
            )
        elif exclude_import_id is not None:
            cur = conn.execute(
                """
                UPDATE nomina_vacaciones_empleados
                SET is_active = 0
                WHERE import_id <> ? AND COALESCE(is_active, 1) = 1
                """,
                (int(exclude_import_id),),
            )
        else:
            return 0
        conn.commit()
        return int(cur.rowcount)
    finally:
        conn.close()


def archive_vacaciones_events_for_import(db_path: str, import_id: int) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            UPDATE nomina_vacaciones_eventos
            SET is_active = 0
            WHERE import_batch_id = ? AND COALESCE(is_active, 1) = 1
            """,
            (int(import_id),),
        )
        conn.commit()
        return int(cur.rowcount)
    finally:
        conn.close()


def list_vacaciones_empleados_all(
    db_path: str,
    *,
    import_id: int | None = None,
    include_inactive: bool = False,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        query = "SELECT * FROM nomina_vacaciones_empleados WHERE 1=1"
        params: list[Any] = []
        if import_id is not None:
            query += " AND import_id = ?"
            params.append(int(import_id))
        if not include_inactive:
            query += " AND COALESCE(is_active, 1) = 1"
        query += " ORDER BY id ASC LIMIT ?"
        params.append(int(limit))
        rows = []
        for row in conn.execute(query, params).fetchall():
            d = dict(row)
            d["warnings"] = json.loads(d.get("warnings_json") or "[]")
            d["editable_json"] = json.loads(d.get("editable_json") or "{}")
            rows.append(d)
        return rows
    finally:
        conn.close()


def validar_vacaciones_base(db_path: str, *, import_id: int | None = None) -> dict[str, Any]:
    rows = list_vacaciones_empleados_all(db_path, import_id=import_id, include_inactive=False)
    conflict_statuses = {
        "pending_review", "no_match", "probable_match",
        "SIN_MATCH", "MATCH_AMBIGUO", "PENDIENTE_REVISION",
    }
    conflictos = [r for r in rows if r.get("match_status") in conflict_statuses]
    negativos = [r for r in rows if float(r.get("saldo_calculado") or r.get("dias_restantes_calculado") or 0) < 0]
    sin_ingreso = [r for r in rows if not str(r.get("fecha_ingreso_usada") or "").strip()]
    con_alerta = [r for r in rows if r.get("warnings")]
    return {
        "total_registros": len(rows),
        "conflictos_match": len(conflictos),
        "saldos_negativos": len(negativos),
        "sin_fecha_ingreso": len(sin_ingreso),
        "con_alertas": len(con_alerta),
        "conflictos": [
            {"id": r["id"], "nombre": r.get("nombre_historico"), "match_status": r.get("match_status")}
            for r in conflictos[:50]
        ],
        "negativos": [
            {"id": r["id"], "nombre": r.get("nombre_historico"), "saldo": r.get("saldo_calculado")}
            for r in negativos[:50]
        ],
    }


def preview_limpieza_vacaciones_base(db_path: str) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        total_empleados = conn.execute(
            "SELECT COUNT(*) FROM nomina_vacaciones_empleados WHERE COALESCE(is_active,1)=1"
        ).fetchone()[0]
        total_eventos = conn.execute(
            "SELECT COUNT(*) FROM nomina_vacaciones_eventos WHERE COALESCE(is_active,1)=1"
        ).fetchone()[0]
        total_imports = conn.execute(
            "SELECT COUNT(*) FROM nomina_vacaciones_imports WHERE COALESCE(is_active,1)=1"
        ).fetchone()[0]
        trabajadores = conn.execute(
            "SELECT COUNT(DISTINCT COALESCE(nss,'') || '|' || COALESCE(nombre_normalizado,'')) "
            "FROM nomina_vacaciones_empleados WHERE COALESCE(is_active,1)=1"
        ).fetchone()[0]
        ultimo = conn.execute(
            "SELECT MAX(created_at) FROM nomina_vacaciones_eventos WHERE COALESCE(is_active,1)=1"
        ).fetchone()[0]
        if not ultimo:
            ultimo = conn.execute(
                "SELECT MAX(updated_at) FROM nomina_vacaciones_empleados WHERE COALESCE(is_active,1)=1"
            ).fetchone()[0]
        if not ultimo:
            ultimo = conn.execute(
                """
                SELECT MAX(i.created_at) FROM nomina_vacaciones_imports i
                WHERE COALESCE(i.is_active,1)=1
                """
            ).fetchone()[0]
        return {
            "total_registros": int(total_empleados or 0),
            "total_eventos": int(total_eventos or 0),
            "total_importaciones": int(total_imports or 0),
            "trabajadores_afectados": int(trabajadores or 0),
            "ultimo_movimiento": ultimo or "—",
        }
    finally:
        conn.close()


def log_vacaciones_auditoria(
    db_path: str,
    *,
    action: str,
    usuario_id: int | None,
    registros_afectados: int,
    backup_id: int | None,
    detalle: dict[str, Any],
    now_iso: str,
) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO nomina_vacaciones_auditoria (
                action, usuario_id, registros_afectados, backup_id, detalle_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                action,
                usuario_id,
                int(registros_afectados),
                backup_id,
                json.dumps(detalle, ensure_ascii=False),
                now_iso,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def archive_all_active_vacaciones_data(db_path: str) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        cur_emp = conn.execute(
            "UPDATE nomina_vacaciones_empleados SET is_active = 0 WHERE COALESCE(is_active, 1) = 1"
        )
        cur_ev = conn.execute(
            "UPDATE nomina_vacaciones_eventos SET is_active = 0 WHERE COALESCE(is_active, 1) = 1"
        )
        cur_imp = conn.execute(
            "UPDATE nomina_vacaciones_imports SET is_active = 0 WHERE COALESCE(is_active, 1) = 1"
        )
        conn.commit()
        return {
            "empleados": int(cur_emp.rowcount),
            "eventos": int(cur_ev.rowcount),
            "importaciones": int(cur_imp.rowcount),
        }
    finally:
        conn.close()


def ejecutar_limpieza_base_vacaciones(
    db_path: str,
    *,
    created_by: int | None,
    now_iso: str,
) -> dict[str, Any]:
    preview = preview_limpieza_vacaciones_base(db_path)
    rows = list_vacaciones_empleados_all(db_path, include_inactive=False, limit=5000)
    eventos = list_vacaciones_eventos(db_path, active_only=True, limit=20000)
    imports = list_vacaciones_imports(db_path, limit=500)
    backup_id = create_vacaciones_backup(
        db_path,
        backup_type="limpieza_base_contaminada_vacaciones",
        summary={
            "motivo": "limpieza_base_contaminada_vacaciones",
            **preview,
        },
        payload={"empleados": rows, "eventos": eventos, "importaciones": imports},
        created_by=created_by,
        now_iso=now_iso,
    )
    archived = archive_all_active_vacaciones_data(db_path)
    audit_id = log_vacaciones_auditoria(
        db_path,
        action="limpiar_base_vacaciones",
        usuario_id=created_by,
        registros_afectados=archived["empleados"] + archived["eventos"],
        backup_id=backup_id,
        detalle={"preview": preview, "archived": archived, "backup_id": backup_id},
        now_iso=now_iso,
    )
    return {
        "backup_id": backup_id,
        "audit_id": audit_id,
        "preview": preview,
        "archived": archived,
    }


def mark_vacaciones_event_reviewed(db_path: str, event_id: int, reviewed: bool = True) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE nomina_vacaciones_eventos SET is_reviewed = ? WHERE id = ?",
            (1 if reviewed else 0, int(event_id)),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def save_infonavit_import(
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
            INSERT INTO nomina_infonavit_imports (
                registro_patronal, fecha_corte, total_avisos_reportado, total_rows,
                active_count, modified_count, suspended_count, vsm_count,
                warning_count, error_count, source_filename, file_hash,
                created_by, created_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(payload.get("registro_patronal") or ""),
                str(payload.get("fecha_corte") or ""),
                int(payload.get("total_avisos_reportado") or 0),
                int(payload.get("total_rows") or 0),
                int(payload.get("active_count") or 0),
                int(payload.get("modified_count") or 0),
                int(payload.get("suspended_count") or 0),
                int(payload.get("vsm_count") or 0),
                int(payload.get("warning_count") or 0),
                int(payload.get("error_count") or 0),
                str(payload.get("source_filename") or ""),
                str(payload.get("file_hash") or ""),
                created_by,
                now_iso,
                json.dumps(payload.get("raw_json") or {}, ensure_ascii=False),
            ),
        )
        import_id = int(cur.lastrowid)
        for row in payload.get("rows") or []:
            conn.execute(
                """
                INSERT INTO nomina_infonavit_rows (
                    import_id, nss, numero_credito, folio_aviso, nombre_trabajador,
                    nombre_normalizado, tipo_aviso, motivo_aviso, fecha_aviso,
                    descuento_raw, descuento_monto_pesos, descuento_factor_vsm, umi_usada,
                    descuento_cf_calculada, tipo_descuento, tipo_valor_descuento, estatus_infonavit,
                    nombre_headcount, cliente_headcount, planta_headcount, estatus_headcount,
                    match_status, match_score, warnings_json, editable_json, updated_by, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    import_id,
                    row.get("nss"),
                    row.get("numero_credito"),
                    row.get("folio_aviso"),
                    row.get("nombre_trabajador"),
                    row.get("nombre_normalizado"),
                    row.get("tipo_aviso"),
                    row.get("motivo_aviso"),
                    row.get("fecha_aviso"),
                    row.get("descuento_raw"),
                    row.get("descuento_monto_pesos"),
                    row.get("descuento_factor_vsm"),
                    row.get("umi_usada"),
                    row.get("descuento_cf_calculada"),
                    row.get("tipo_descuento"),
                    row.get("tipo_valor_descuento"),
                    row.get("estatus_infonavit"),
                    row.get("nombre_headcount"),
                    row.get("cliente_headcount"),
                    row.get("planta_headcount"),
                    row.get("estatus_headcount"),
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


def get_latest_infonavit_import_id(db_path: str) -> int | None:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM nomina_infonavit_imports ORDER BY datetime(created_at) DESC, id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return int(row[0])
    finally:
        conn.close()


def list_infonavit_imports(db_path: str, limit: int = 50) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, registro_patronal, fecha_corte, total_avisos_reportado, total_rows,
                   active_count, modified_count, suspended_count, vsm_count,
                   warning_count, error_count, source_filename, created_at
            FROM nomina_infonavit_imports
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_infonavit_rows(
    db_path: str,
    *,
    import_id: int | None = None,
    match_status: str | None = None,
    estatus_infonavit: str | None = None,
    revision_status: str | None = None,
    limit: int = 800,
) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        query = """
            SELECT r.*, i.created_at AS import_created_at
            FROM nomina_infonavit_rows r
            JOIN nomina_infonavit_imports i ON i.id = r.import_id
            WHERE 1=1
        """
        params: list[Any] = []
        if import_id is not None:
            query += " AND r.import_id = ?"
            params.append(int(import_id))
        if match_status:
            query += " AND COALESCE(r.match_status,'') = ?"
            params.append(match_status)
        if estatus_infonavit:
            query += " AND COALESCE(r.estatus_infonavit,'') = ?"
            params.append(estatus_infonavit)
        if revision_status:
            query += " AND COALESCE(r.editable_json,'') LIKE ?"
            params.append(f'%\"revision_status\": \"{revision_status}\"%')
        query += " ORDER BY r.id DESC LIMIT ?"
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


def get_infonavit_import(db_path: str, import_id: int) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        imp = conn.execute(
            "SELECT * FROM nomina_infonavit_imports WHERE id = ?",
            (import_id,),
        ).fetchone()
        if imp is None:
            return None
        result = dict(imp)
        result["raw_json"] = json.loads(result.get("raw_json") or "{}")
        result["rows"] = list_infonavit_rows(db_path, import_id=import_id, limit=5000)
        return result
    finally:
        conn.close()


def get_infonavit_row(db_path: str, row_id: int) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM nomina_infonavit_rows WHERE id = ?",
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


def update_infonavit_row(
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
            UPDATE nomina_infonavit_rows
            SET estatus_infonavit = ?,
                descuento_monto_pesos = ?,
                descuento_factor_vsm = ?,
                umi_usada = ?,
                descuento_cf_calculada = ?,
                tipo_valor_descuento = ?,
                match_status = ?,
                editable_json = ?,
                updated_by = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                updates.get("estatus_infonavit"),
                updates.get("descuento_monto_pesos"),
                updates.get("descuento_factor_vsm"),
                updates.get("umi_usada"),
                updates.get("descuento_cf_calculada"),
                updates.get("tipo_valor_descuento"),
                updates.get("match_status"),
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


def get_infonavit_stats(db_path: str) -> dict[str, int]:
    return get_infonavit_stats_by_import(db_path, import_id=None)


def get_infonavit_stats_by_import(db_path: str, import_id: int | None) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        if import_id is None:
            import_id = get_latest_infonavit_import_id(db_path)
        if import_id is None:
            return {
                "total_avisos": 0,
                "activos": 0,
                "modificados": 0,
                "suspendidos": 0,
                "sin_match": 0,
                "pendientes_revision": 0,
                "vsm_detectados": 0,
            }
        total = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_infonavit_rows WHERE import_id = ?",
                (int(import_id),),
            ).fetchone()[0]
        )
        activos = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_infonavit_rows WHERE import_id = ? AND estatus_infonavit = 'ACTIVO'",
                (int(import_id),),
            ).fetchone()[0]
        )
        modificados = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_infonavit_rows WHERE import_id = ? AND estatus_infonavit = 'ACTIVO_MODIFICADO'",
                (int(import_id),),
            ).fetchone()[0]
        )
        suspendidos = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_infonavit_rows WHERE import_id = ? AND estatus_infonavit = 'SUSPENDIDO'",
                (int(import_id),),
            ).fetchone()[0]
        )
        sin_match = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_infonavit_rows WHERE import_id = ? AND match_status IN ('no_match','pending_review')",
                (int(import_id),),
            ).fetchone()[0]
        )
        pendientes = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_infonavit_rows WHERE import_id = ? AND (match_status IN ('pending_review','probable_match') OR COALESCE(warnings_json,'[]') <> '[]')",
                (int(import_id),),
            ).fetchone()[0]
        )
        vsm = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_infonavit_rows WHERE import_id = ? AND tipo_valor_descuento = 'VSM'",
                (int(import_id),),
            ).fetchone()[0]
        )
        return {
            "total_avisos": total,
            "activos": activos,
            "modificados": modificados,
            "suspendidos": suspendidos,
            "sin_match": sin_match,
            "pendientes_revision": pendientes,
            "vsm_detectados": vsm,
        }
    finally:
        conn.close()


def save_parametros_import(
    db_path: str,
    payload: dict[str, Any],
    *,
    created_by: int | None,
    now_iso: str,
) -> int:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute(
            """
            INSERT INTO nomina_parametros_imports (
                tipo_importacion, cliente, source_filename, file_hash,
                total_rows, matched_count, warning_count, error_count,
                created_by, created_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(payload.get("tipo_importacion") or "").upper(),
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
        return int(cur.lastrowid)
    finally:
        conn.commit()
        conn.close()


def upsert_empleado_parametros(
    db_path: str,
    rows: list[dict[str, Any]],
    *,
    import_id: int,
    now_iso: str,
    overwrite_keys: set[str] | None = None,
) -> tuple[int, int]:
    """Insert or update parameter rows.

    Matching to find existing rows is done in this order:
    1) NSS exact (if present)
    2) (cliente, nombre_normalizado) pair

    Returns (inserted, updated) counts.
    """
    overwrite = overwrite_keys or set()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    inserted = updated = 0
    try:
        for row in rows:
            nss = str(row.get("nss") or "").strip()
            nombre_norm = str(row.get("nombre_normalizado") or "").strip()
            cliente_norm = str(row.get("cliente") or "").strip()
            existing = None
            if nss:
                existing = conn.execute(
                    "SELECT * FROM nomina_empleado_parametros WHERE nss = ? LIMIT 1",
                    (nss,),
                ).fetchone()
            if existing is None and nombre_norm:
                existing = conn.execute(
                    """SELECT * FROM nomina_empleado_parametros
                       WHERE nombre_normalizado = ?
                         AND (COALESCE(cliente,'') = ? OR ? = '')
                       LIMIT 1""",
                    (nombre_norm, cliente_norm, cliente_norm),
                ).fetchone()
            if existing is None:
                record_kind = row.get("record_kind") or "import"
                conn.execute(
                    """
                    INSERT INTO nomina_empleado_parametros (
                        nombre, nombre_normalizado, nss, numero_empleado, codigo_contpaq,
                        cliente, planta, puesto, banco, cuenta,
                        localidad, localidad_normalizada,
                        salario_operativo, valor_x_he,
                        zona_salario_raw, es_frontera,
                        salario_minimo_usado, exento_he_usado,
                        fuente_salario_operativo, fuente_valor_x_he,
                        fuente_numero_empleado, fuente_nss,
                        headcount_match_status, contpaq_match_status, nomina_match_status,
                        warnings_json, editable_json, last_import_id,
                        record_kind, is_active,
                        created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        row.get("nombre"),
                        row.get("nombre_normalizado"),
                        row.get("nss"),
                        row.get("numero_empleado"),
                        row.get("codigo_contpaq"),
                        row.get("cliente"),
                        row.get("planta"),
                        row.get("puesto"),
                        row.get("banco"),
                        row.get("cuenta"),
                        row.get("localidad"),
                        row.get("localidad_normalizada"),
                        row.get("salario_operativo"),
                        row.get("valor_x_he"),
                        row.get("zona_salario_raw"),
                        int(bool(row.get("es_frontera"))) if row.get("es_frontera") is not None else None,
                        row.get("salario_minimo_usado"),
                        row.get("exento_he_usado"),
                        row.get("fuente_salario_operativo"),
                        row.get("fuente_valor_x_he"),
                        row.get("fuente_numero_empleado"),
                        row.get("fuente_nss"),
                        row.get("headcount_match_status"),
                        row.get("contpaq_match_status"),
                        row.get("nomina_match_status"),
                        json.dumps(row.get("warnings") or [], ensure_ascii=False),
                        json.dumps(row.get("editable_json") or {}, ensure_ascii=False),
                        import_id,
                        record_kind,
                        1,
                        now_iso,
                        now_iso,
                    ),
                )
                inserted += 1
                continue
            existing_dict = dict(existing)
            skip_keys: set[str] = set()
            nss_conflict_notes: list[str] = []
            if _nss_merge_conflict(existing_dict, row):
                skip_keys = {"cliente", "planta", "salario_operativo"}
                nss_conflict_notes.append(
                    f"{WARN_SAME_NSS_MULTIPLE_CLIENTS}: NSS {nss} con cliente/planta/salario "
                    "incompatible vs registro existente; se conservan los valores previos."
                )
            merged: dict[str, Any] = {**existing_dict}
            for key, new_val in row.items():
                if key in {"warnings", "editable_json", "horas_extra_periodo"}:
                    continue
                if key in skip_keys:
                    continue
                cur_val = merged.get(key)
                if (cur_val in (None, "")) or (key in overwrite and new_val not in (None, "")):
                    merged[key] = new_val
            existing_warnings = json.loads(existing_dict.get("warnings_json") or "[]")
            new_warnings = list(existing_warnings) + nss_conflict_notes + list(row.get("warnings") or [])
            existing_editable = json.loads(existing_dict.get("editable_json") or "{}")
            new_editable = {**existing_editable, **(row.get("editable_json") or {})}
            if nss_conflict_notes:
                trace = new_editable.setdefault("same_nss_multiple_clients", [])
                trace.append(
                    {
                        "import_id": import_id,
                        "skipped_fields": sorted(skip_keys),
                        "attempted_cliente": row.get("cliente"),
                        "attempted_planta": row.get("planta"),
                        "attempted_salario_operativo": row.get("salario_operativo"),
                    }
                )
            conn.execute(
                """
                UPDATE nomina_empleado_parametros SET
                    nombre = ?, nombre_normalizado = ?, nss = ?, numero_empleado = ?, codigo_contpaq = ?,
                    cliente = ?, planta = ?, puesto = ?, banco = ?, cuenta = ?,
                    localidad = ?, localidad_normalizada = ?,
                    salario_operativo = ?, valor_x_he = ?,
                    zona_salario_raw = ?, es_frontera = ?,
                    salario_minimo_usado = ?, exento_he_usado = ?,
                    fuente_salario_operativo = ?, fuente_valor_x_he = ?,
                    fuente_numero_empleado = ?, fuente_nss = ?,
                    headcount_match_status = ?, contpaq_match_status = ?, nomina_match_status = ?,
                    warnings_json = ?, editable_json = ?, last_import_id = ?,
                    record_kind = COALESCE(?, record_kind),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    merged.get("nombre"),
                    merged.get("nombre_normalizado"),
                    merged.get("nss"),
                    merged.get("numero_empleado"),
                    merged.get("codigo_contpaq"),
                    merged.get("cliente"),
                    merged.get("planta"),
                    merged.get("puesto"),
                    merged.get("banco"),
                    merged.get("cuenta"),
                    merged.get("localidad"),
                    merged.get("localidad_normalizada"),
                    merged.get("salario_operativo"),
                    merged.get("valor_x_he"),
                    merged.get("zona_salario_raw"),
                    merged.get("es_frontera"),
                    merged.get("salario_minimo_usado"),
                    merged.get("exento_he_usado"),
                    merged.get("fuente_salario_operativo"),
                    merged.get("fuente_valor_x_he"),
                    merged.get("fuente_numero_empleado"),
                    merged.get("fuente_nss"),
                    merged.get("headcount_match_status"),
                    merged.get("contpaq_match_status"),
                    merged.get("nomina_match_status"),
                    json.dumps(new_warnings, ensure_ascii=False),
                    json.dumps(new_editable, ensure_ascii=False),
                    import_id,
                    row.get("record_kind"),
                    now_iso,
                    int(existing_dict["id"]),
                ),
            )
            updated += 1
        conn.commit()
        return inserted, updated
    finally:
        conn.close()


def list_empleado_parametros(
    db_path: str,
    *,
    cliente: str | None = None,
    match_status_any: list[str] | None = None,
    only_missing_salary: bool = False,
    only_missing_valor_he: bool = False,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        query = "SELECT * FROM nomina_empleado_parametros WHERE COALESCE(is_active, 1) = 1"
        params: list[Any] = []
        if cliente:
            query += " AND LOWER(TRIM(COALESCE(cliente,''))) = LOWER(TRIM(?))"
            params.append(cliente)
        if only_missing_salary:
            query += " AND (salario_operativo IS NULL OR salario_operativo <= 0)"
        if only_missing_valor_he:
            query += " AND (valor_x_he IS NULL OR valor_x_he <= 0)"
        if match_status_any:
            placeholders = ",".join("?" for _ in match_status_any)
            query += (
                f" AND (headcount_match_status IN ({placeholders})"
                f" OR contpaq_match_status IN ({placeholders})"
                f" OR nomina_match_status IN ({placeholders}))"
            )
            params.extend(match_status_any * 3)
        query += " ORDER BY nombre ASC LIMIT ?"
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


def get_empleado_parametro(db_path: str, row_id: int) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM nomina_empleado_parametros WHERE id = ?", (int(row_id),)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["warnings"] = json.loads(d.get("warnings_json") or "[]")
        d["editable_json"] = json.loads(d.get("editable_json") or "{}")
        return d
    finally:
        conn.close()


def update_empleado_parametro(
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
            UPDATE nomina_empleado_parametros SET
                numero_empleado = ?,
                salario_operativo = ?,
                valor_x_he = ?,
                localidad = ?,
                localidad_normalizada = ?,
                es_frontera = ?,
                salario_minimo_usado = ?,
                exento_he_usado = ?,
                editable_json = ?,
                updated_by = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                updates.get("numero_empleado"),
                updates.get("salario_operativo"),
                updates.get("valor_x_he"),
                updates.get("localidad"),
                updates.get("localidad_normalizada"),
                int(bool(updates.get("es_frontera"))) if updates.get("es_frontera") is not None else None,
                updates.get("salario_minimo_usado"),
                updates.get("exento_he_usado"),
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


def get_parametros_stats(db_path: str, headcount_rows: list[dict[str, Any]] | None = None) -> dict[str, int]:
    """KPIs de parámetros. Si hay Headcount disponible, la fuente principal es activos Headcount."""
    if headcount_rows is not None:
        from modules.nomina.parametros_consolidado import compute_parametros_stats

        return compute_parametros_stats(db_path, headcount_rows)

    conn = sqlite3.connect(db_path)
    try:
        total = int(
            conn.execute("SELECT COUNT(*) FROM nomina_empleado_parametros").fetchone()[0]
        )
        missing_salary = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_empleado_parametros WHERE salario_operativo IS NULL OR salario_operativo <= 0"
            ).fetchone()[0]
        )
        missing_he = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_empleado_parametros WHERE valor_x_he IS NULL OR valor_x_he <= 0"
            ).fetchone()[0]
        )
        pending = int(
            conn.execute(
                """SELECT COUNT(*) FROM nomina_empleado_parametros
                   WHERE COALESCE(is_active, 1) = 1
                     AND (
                       headcount_match_status IN (
                           'no_match_headcount',
                           'pending_headcount_unavailable',
                           'probable_match',
                           'multiple_candidates',
                           'pending_review',
                           'inactive_headcount'
                       )
                       OR contpaq_match_status IN (
                           'no_match_contpaq',
                           'pending_review',
                           'probable_match'
                       )
                       OR COALESCE(warnings_json,'[]') <> '[]'
                       OR record_kind IN ('external_nomina', 'external_contpaq')
                     )"""
            ).fetchone()[0]
        )
        contpaq_import_rows = int(
            conn.execute(
                """SELECT COALESCE(SUM(total_rows), 0) FROM nomina_parametros_imports
                   WHERE tipo_importacion = 'CONTPAQ'"""
            ).fetchone()[0]
        )
        nomina_import_rows = int(
            conn.execute(
                """SELECT COALESCE(SUM(total_rows), 0) FROM nomina_parametros_imports
                   WHERE tipo_importacion = 'NOMINA_ACTUAL'"""
            ).fetchone()[0]
        )
        external_rows = int(
            conn.execute(
                """SELECT COUNT(*) FROM nomina_empleado_parametros
                   WHERE COALESCE(is_active, 1) = 1
                     AND record_kind IN ('external_nomina', 'external_contpaq')"""
            ).fetchone()[0]
        )
        return {
            "stats_mode": "legacy",
            "total_empleados": total,
            "activos_headcount": 0,
            "total_registros_parametros": total,
            "missing_salario_operativo": missing_salary,
            "missing_valor_x_he": missing_he,
            "pendientes_revision": pending,
            "con_nomina_vinculada": 0,
            "con_contpaq_vinculado": 0,
            "registros_externos_sin_vinculo": external_rows,
            "registros_nomina_importados": nomina_import_rows,
            "registros_contpaq_importados": contpaq_import_rows,
            "vinculos_manuales": 0,
        }
    finally:
        conn.close()


def list_parametros_imports(db_path: str, limit: int = 50) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, tipo_importacion, cliente, source_filename, total_rows,
                   matched_count, warning_count, error_count, created_at, raw_json
            FROM nomina_parametros_imports
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            raw = json.loads(d.pop("raw_json") or "{}")
            d["periodo_detectado"] = raw.get("periodo_detectado") or None
            d["rematch_summary"] = raw.get("rematch_summary") or {}
            out.append(d)
        return out
    finally:
        conn.close()


def upsert_localidades_frontera(
    db_path: str,
    items: list[dict[str, Any]],
    *,
    now_iso: str,
) -> tuple[int, int, list[str]]:
    """Insert/update localidades. No degradar ``es_frontera`` de 1 a 0 por un Excel con FALSO.

    Returns (inserted, updated, file_warnings).
    """
    conn = sqlite3.connect(db_path)
    inserted = updated = 0
    file_warnings: list[str] = []
    try:
        for it in items:
            loc_norm = str(it.get("localidad_normalizada") or "").strip()
            if not loc_norm:
                continue
            cliente = str(it.get("cliente") or "").strip()
            raw_hint = it.get("es_frontera")
            if raw_hint is True:
                new_es = 1
            elif raw_hint is False:
                new_es = 0
            else:
                new_es = None  # indeterminado en esta fila

            existing = conn.execute(
                "SELECT id, es_frontera FROM nomina_localidades_frontera WHERE cliente = ? AND localidad_normalizada = ?",
                (cliente, loc_norm),
            ).fetchone()
            if existing is None:
                insert_es = 1 if new_es == 1 else 0
                if new_es is None:
                    file_warnings.append(
                        f"{WARN_LOCALIDAD_FRONTERA_IMPORT_UNKNOWN}: localidad '{loc_norm}' sin FRONTERA explícita; "
                        "se inserta como GENERAL (0)."
                    )
                conn.execute(
                    """INSERT INTO nomina_localidades_frontera (
                        cliente, localidad, localidad_normalizada, es_frontera,
                        source_filename, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        cliente, it.get("localidad"), loc_norm, insert_es,
                        it.get("source_filename"), now_iso, now_iso,
                    ),
                )
                inserted += 1
            else:
                eid, cur_es = int(existing[0]), int(existing[1])
                if new_es is None:
                    # No tocar bandera aprendida si el Excel no trae dato
                    conn.execute(
                        "UPDATE nomina_localidades_frontera SET updated_at = ? WHERE id = ?",
                        (now_iso, eid),
                    )
                    updated += 1
                elif new_es == 1:
                    conn.execute(
                        """UPDATE nomina_localidades_frontera SET es_frontera = 1, updated_at = ?, source_filename = ?
                           WHERE id = ?""",
                        (now_iso, it.get("source_filename"), eid),
                    )
                    updated += 1
                elif new_es == 0 and cur_es == 1:
                    file_warnings.append(
                        f"{WARN_LOCALIDAD_FRONTERA_DEMOTION_BLOCKED}: '{cliente}' / '{loc_norm}' ya estaba "
                        "catalogada como FRONTERA; no se degradó por FRONTERA=FALSO en el Excel."
                    )
                    conn.execute(
                        "UPDATE nomina_localidades_frontera SET updated_at = ? WHERE id = ?",
                        (now_iso, eid),
                    )
                    updated += 1
                else:
                    conn.execute(
                        """UPDATE nomina_localidades_frontera SET es_frontera = 0, updated_at = ?, source_filename = ?
                           WHERE id = ?""",
                        (now_iso, it.get("source_filename"), eid),
                    )
                    updated += 1
        conn.commit()
        return inserted, updated, file_warnings
    finally:
        conn.close()


def list_localidades_frontera(db_path: str, *, cliente: str | None = None) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if cliente:
            rows = conn.execute(
                "SELECT * FROM nomina_localidades_frontera WHERE cliente = ? ORDER BY localidad",
                (cliente,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM nomina_localidades_frontera ORDER BY cliente, localidad"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def localidad_is_frontera(db_path: str, cliente: str, localidad_normalizada: str) -> bool | None:
    if not localidad_normalizada:
        return None
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT es_frontera FROM nomina_localidades_frontera WHERE cliente = ? AND localidad_normalizada = ?",
            (cliente or "", localidad_normalizada),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT es_frontera FROM nomina_localidades_frontera WHERE localidad_normalizada = ? AND es_frontera = 1 LIMIT 1",
                (localidad_normalizada,),
            ).fetchone()
        return bool(row[0]) if row is not None else None
    finally:
        conn.close()


def _migrate_nomina_vacaciones_schema(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(nomina_vacaciones_empleados)").fetchall()}
    migrations = {
        "headcount_source": "ALTER TABLE nomina_vacaciones_empleados ADD COLUMN headcount_source TEXT",
        "headcount_raw_status": "ALTER TABLE nomina_vacaciones_empleados ADD COLUMN headcount_raw_status TEXT",
        "dias_generados": "ALTER TABLE nomina_vacaciones_empleados ADD COLUMN dias_generados REAL",
        "saldo_calculado": "ALTER TABLE nomina_vacaciones_empleados ADD COLUMN saldo_calculado REAL",
        "prima_pendiente": "ALTER TABLE nomina_vacaciones_empleados ADD COLUMN prima_pendiente REAL",
        "is_active": "ALTER TABLE nomina_vacaciones_empleados ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1",
        "match_method": "ALTER TABLE nomina_vacaciones_empleados ADD COLUMN match_method TEXT",
        "match_notes": "ALTER TABLE nomina_vacaciones_empleados ADD COLUMN match_notes TEXT",
        "dias_utilizados_calculado_semanal": "ALTER TABLE nomina_vacaciones_empleados ADD COLUMN dias_utilizados_calculado_semanal REAL",
        "dias_utilizados_excel_resumen": "ALTER TABLE nomina_vacaciones_empleados ADD COLUMN dias_utilizados_excel_resumen REAL",
        "status_headcount": "ALTER TABLE nomina_vacaciones_empleados ADD COLUMN status_headcount TEXT",
    }
    for col, sql in migrations.items():
        if col not in cols:
            conn.execute(sql)
    import_cols = {row[1] for row in conn.execute("PRAGMA table_info(nomina_vacaciones_imports)").fetchall()}
    if "is_active" not in import_cols:
        conn.execute("ALTER TABLE nomina_vacaciones_imports ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")


def list_asistencia_imports_for_calculo(db_path: str, *, limit: int = 80) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT i.id, i.semana, i.fecha_inicio, i.fecha_fin, i.cliente, i.coordinador, i.status,
                   i.total_rows, i.error_count, i.warning_count, i.created_by, i.created_at, i.updated_at,
                   i.headcount_source, i.clientes_json, i.raw_json,
                   (
                       SELECT COUNT(*)
                       FROM nomina_asistencia_rows r
                       WHERE r.import_id = i.id
                         AND TRIM(COALESCE(r.errors_json, '[]')) IN ('[]', '')
                   ) AS total_valid_rows,
                   (
                       SELECT COUNT(DISTINCT CASE
                           WHEN TRIM(COALESCE(r.nss, '')) <> '' THEN 'nss:' || TRIM(r.nss)
                           ELSE 'nombre:' || LOWER(TRIM(COALESCE(r.nombre_empleado, '')))
                                || '|cliente:' || LOWER(TRIM(COALESCE(r.cliente, '')))
                       END)
                       FROM nomina_asistencia_rows r
                       WHERE r.import_id = i.id
                         AND TRIM(COALESCE(r.errors_json, '[]')) IN ('[]', '')
                   ) AS total_valid_workers,
                   (
                       SELECT GROUP_CONCAT(DISTINCT TRIM(r.planta))
                       FROM nomina_asistencia_rows r
                       WHERE r.import_id = i.id
                         AND TRIM(COALESCE(r.planta, '')) <> ''
                   ) AS plantas_detectadas
            FROM nomina_asistencia_imports i
            WHERE (COALESCE(TRIM(deleted_at), '') = '')
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            d["clientes"] = json.loads(d.get("clientes_json") or "[]")
            d["raw_json"] = json.loads(d.get("raw_json") or "{}")
            plantas_csv = str(d.get("plantas_detectadas") or "")
            d["plantas"] = [p.strip() for p in plantas_csv.split(",") if p.strip()]
            out.append(d)
        return out
    finally:
        conn.close()


def nomina_calculo_dashboard_kpis(db_path: str) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        total = int(conn.execute("SELECT COUNT(*) FROM nomina_calculo_runs").fetchone()[0])
        borradores = int(
            conn.execute(
                "SELECT COUNT(*) FROM nomina_calculo_runs WHERE status IN ('borrador','recalculado','revisado')"
            ).fetchone()[0]
        )
        last = conn.execute(
            "SELECT id, status, created_at, total_empleados, warning_count, block_count FROM nomina_calculo_runs ORDER BY datetime(created_at) DESC, id DESC LIMIT 1"
        ).fetchone()
        return {
            "total_calculos": total,
            "borradores": borradores,
            "last_run": dict(last) if last else None,
        }
    finally:
        conn.close()


def insert_nomina_calculo_run(
    db_path: str,
    payload: dict[str, Any],
    *,
    created_by: int | None,
    now_iso: str,
) -> int:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute(
            """
            INSERT INTO nomina_calculo_runs (
                asistencia_import_id, cliente, clientes_json, fecha_inicio, fecha_fin,
                config_json, status, total_empleados, warning_count, block_count,
                created_by, created_at, updated_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(payload["asistencia_import_id"]),
                str(payload.get("cliente") or ""),
                json.dumps(payload.get("clientes_json") or [], ensure_ascii=False),
                str(payload.get("fecha_inicio") or ""),
                str(payload.get("fecha_fin") or ""),
                json.dumps(payload.get("config_json") or {}, ensure_ascii=False),
                str(payload.get("status") or "borrador"),
                int(payload.get("total_empleados") or 0),
                int(payload.get("warning_count") or 0),
                int(payload.get("block_count") or 0),
                created_by,
                now_iso,
                now_iso,
                json.dumps(payload.get("raw_json") or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_nomina_calculo_run(db_path: str, calculo_id: int) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM nomina_calculo_runs WHERE id = ?", (int(calculo_id),)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["clientes_json"] = json.loads(d.get("clientes_json") or "[]")
        d["config_json"] = json.loads(d.get("config_json") or "{}")
        d["raw_json"] = json.loads(d.get("raw_json") or "{}")
        return d
    finally:
        conn.close()


def update_nomina_calculo_run(
    db_path: str,
    calculo_id: int,
    updates: dict[str, Any],
    *,
    now_iso: str,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        fields = []
        params: list[Any] = []
        for key in (
            "status",
            "total_empleados",
            "warning_count",
            "block_count",
            "raw_json",
            "config_json",
        ):
            if key in updates:
                fields.append(f"{key} = ?")
                val = updates[key]
                if key in {"raw_json", "config_json"} and not isinstance(val, str):
                    val = json.dumps(val, ensure_ascii=False)
                params.append(val)
        if not fields:
            return
        fields.append("updated_at = ?")
        params.append(now_iso)
        params.append(int(calculo_id))
        conn.execute(
            f"UPDATE nomina_calculo_runs SET {', '.join(fields)} WHERE id = ?",
            tuple(params),
        )
        conn.commit()
    finally:
        conn.close()


def delete_nomina_calculo_rows(db_path: str, calculo_id: int) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM nomina_calculo_rows WHERE calculo_id = ?", (int(calculo_id),))
        conn.commit()
    finally:
        conn.close()


def insert_nomina_calculo_rows_batch(db_path: str, calculo_id: int, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        for row in rows:
            conn.execute(
                """
                INSERT INTO nomina_calculo_rows (
                    calculo_id, asistencia_row_id, parametro_empleado_id, vacaciones_empleado_id, infonavit_row_id,
                    nss, numero_empleado, nombre_empleado, cliente, planta, puesto, banco, cuenta,
                    salario_operativo, valor_x_he, es_frontera, smg_usado, exento_he_usado,
                    dias_computables, septimo_dia, dias_pago, horas_extra, valor_he_fiscal, valor_extra_operativo,
                    horas_extra_normales, importe_horas_extra_normales, dias_cubiertos_normales, importe_dias_cubiertos_normales,
                    festivo_laborado_detected, importe_festivo_laborado, domingo_laborado_detected, importe_domingo_laborado,
                    vacaciones_laboradas, importe_vacaciones_laboradas, prima_vacacional_aplicada, dias_prima_vacacional_pendientes, importe_prima_vacacional,
                    bono_manual, bono_manual_clasificacion, deduccion_manual,
                    sueldo_base_smg, concepto_gravable, concepto_exento, base_gravada, isr, bono_tpt, prima_eficiencia,
                    infonavit_mensual, infonavit_semanal, infonavit_status,
                    total_percepciones, total_deducciones, neto_simple, neto_a_pagar,
                    base_neto_simple, neto_simple_operativo, neto_redondeado, ajuste_al_neto, neto_a_pagar_final,
                    warnings_json, blocks_json, detail_json, manual_overrides_json, row_status,
                    updated_by, updated_at
                ) VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
                """,
                (
                    int(calculo_id),
                    int(row["asistencia_row_id"]),
                    row.get("parametro_empleado_id"),
                    row.get("vacaciones_empleado_id"),
                    row.get("infonavit_row_id"),
                    row.get("nss"),
                    row.get("numero_empleado"),
                    row.get("nombre_empleado"),
                    row.get("cliente"),
                    row.get("planta"),
                    row.get("puesto"),
                    row.get("banco"),
                    row.get("cuenta"),
                    row.get("salario_operativo"),
                    row.get("valor_x_he"),
                    int(row.get("es_frontera") or 0),
                    row.get("smg_usado"),
                    row.get("exento_he_usado"),
                    row.get("dias_computables"),
                    row.get("septimo_dia"),
                    row.get("dias_pago"),
                    row.get("horas_extra"),
                    row.get("valor_he_fiscal"),
                    row.get("valor_extra_operativo"),
                    row.get("horas_extra_normales"),
                    row.get("importe_horas_extra_normales"),
                    row.get("dias_cubiertos_normales"),
                    row.get("importe_dias_cubiertos_normales"),
                    int(row.get("festivo_laborado_detected") or 0),
                    row.get("importe_festivo_laborado"),
                    int(row.get("domingo_laborado_detected") or 0),
                    row.get("importe_domingo_laborado"),
                    row.get("vacaciones_laboradas"),
                    row.get("importe_vacaciones_laboradas"),
                    int(row.get("prima_vacacional_aplicada") or 0),
                    row.get("dias_prima_vacacional_pendientes"),
                    row.get("importe_prima_vacacional"),
                    row.get("bono_manual"),
                    row.get("bono_manual_clasificacion"),
                    row.get("deduccion_manual"),
                    row.get("sueldo_base_smg"),
                    row.get("concepto_gravable"),
                    row.get("concepto_exento"),
                    row.get("base_gravada"),
                    row.get("isr"),
                    row.get("bono_tpt"),
                    row.get("prima_eficiencia"),
                    row.get("infonavit_mensual"),
                    row.get("infonavit_semanal"),
                    row.get("infonavit_status"),
                    row.get("total_percepciones"),
                    row.get("total_deducciones"),
                    row.get("neto_simple"),
                    row.get("neto_a_pagar"),
                    row.get("base_neto_simple"),
                    row.get("neto_simple_operativo"),
                    row.get("neto_redondeado"),
                    row.get("ajuste_al_neto"),
                    row.get("neto_a_pagar_final"),
                    json.dumps(row.get("warnings_json") or [], ensure_ascii=False),
                    json.dumps(row.get("blocks_json") or [], ensure_ascii=False),
                    json.dumps(row.get("detail_json") or {}, ensure_ascii=False),
                    json.dumps(row.get("manual_overrides_json") or {}, ensure_ascii=False),
                    str(row.get("row_status") or "pendiente"),
                    row.get("updated_by"),
                    row.get("updated_at"),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def list_nomina_calculo_rows(db_path: str, calculo_id: int) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM nomina_calculo_rows WHERE calculo_id = ? ORDER BY id ASC",
            (int(calculo_id),),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            d["warnings_json"] = json.loads(d.get("warnings_json") or "[]")
            d["blocks_json"] = json.loads(d.get("blocks_json") or "[]")
            d["detail_json"] = json.loads(d.get("detail_json") or "{}")
            d["manual_overrides_json"] = json.loads(d.get("manual_overrides_json") or "{}")
            out.append(d)
        return out
    finally:
        conn.close()


def get_nomina_calculo_row(db_path: str, row_id: int) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM nomina_calculo_rows WHERE id = ?", (int(row_id),)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["warnings_json"] = json.loads(d.get("warnings_json") or "[]")
        d["blocks_json"] = json.loads(d.get("blocks_json") or "[]")
        d["detail_json"] = json.loads(d.get("detail_json") or "{}")
        d["manual_overrides_json"] = json.loads(d.get("manual_overrides_json") or "{}")
        return d
    finally:
        conn.close()


def update_nomina_calculo_row_manual(
    db_path: str,
    row_id: int,
    fields: dict[str, Any],
    *,
    updated_by: int | None,
    now_iso: str,
) -> bool:
    allowed_cols = {
        "concepto_gravable",
        "concepto_exento",
        "bono_manual",
        "deduccion_manual",
        "importe_domingo_laborado",
        "importe_festivo_laborado",
        "infonavit_semanal",
        "isr",
        "bono_tpt",
        "prima_eficiencia",
        "total_percepciones",
        "total_deducciones",
        "neto_simple",
        "neto_a_pagar",
        "base_gravada",
    }
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT manual_overrides_json, detail_json FROM nomina_calculo_rows WHERE id = ?",
            (int(row_id),),
        ).fetchone()
        if row is None:
            return False
        manual = json.loads(row[0] or "{}")
        detail = json.loads(row[1] or "{}")
        set_parts: list[str] = []
        params: list[Any] = []
        for key, val in fields.items():
            if key == "observaciones":
                manual["observaciones"] = val
                detail["observaciones_manual"] = val
                continue
            if key not in allowed_cols:
                continue
            set_parts.append(f"{key} = ?")
            params.append(val)
            manual[key] = val
        set_parts.append("manual_overrides_json = ?")
        params.append(json.dumps(manual, ensure_ascii=False))
        set_parts.append("detail_json = ?")
        params.append(json.dumps(detail, ensure_ascii=False))
        set_parts.append("updated_by = ?")
        params.append(updated_by)
        set_parts.append("updated_at = ?")
        params.append(now_iso)
        set_parts.append("row_status = ?")
        params.append("revisado")
        params.append(int(row_id))
        conn.execute(
            f"UPDATE nomina_calculo_rows SET {', '.join(set_parts)} WHERE id = ?",
            tuple(params),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def patch_nomina_calculo_row_engine_fields(
    db_path: str,
    row_id: int,
    patch: dict[str, Any],
    *,
    now_iso: str | None = None,
) -> None:
    cols = [k for k, v in patch.items() if v is not None and k in {
        "base_gravada",
        "isr",
        "total_percepciones",
        "total_deducciones",
        "neto_simple",
        "neto_a_pagar",
        "base_neto_simple",
        "neto_simple_operativo",
        "neto_redondeado",
        "ajuste_al_neto",
        "neto_a_pagar_final",
        "detail_json",
    }]
    if not cols:
        return
    conn = sqlite3.connect(db_path)
    try:
        params: list[Any] = []
        for c in cols:
            val = patch[c]
            if c == "detail_json" and isinstance(val, dict):
                val = json.dumps(val, ensure_ascii=False)
            params.append(val)
        if now_iso:
            cols.append("updated_at")
            params.append(now_iso)
        params.append(int(row_id))
        conn.execute(
            f"UPDATE nomina_calculo_rows SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?",
            tuple(params),
        )
        conn.commit()
    finally:
        conn.close()


def recount_calculo_run_totals(db_path: str, calculo_id: int, *, now_iso: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        run_rw = conn.execute("SELECT raw_json FROM nomina_calculo_runs WHERE id = ?", (int(calculo_id),)).fetchone()
        extra = 0
        if run_rw and run_rw[0]:
            try:
                raw = json.loads(run_rw[0])
                extra = len(raw.get("run_warnings") or [])
            except json.JSONDecodeError:
                extra = 0
        rows = conn.execute(
            "SELECT warnings_json, blocks_json FROM nomina_calculo_rows WHERE calculo_id = ?",
            (int(calculo_id),),
        ).fetchall()
        w = b = 0
        for wj, bj in rows:
            try:
                w += len(json.loads(wj or "[]"))
            except json.JSONDecodeError:
                pass
            try:
                b += len(json.loads(bj or "[]"))
            except json.JSONDecodeError:
                pass
        conn.execute(
            "UPDATE nomina_calculo_runs SET warning_count = ?, block_count = ?, total_empleados = ?, updated_at = ? WHERE id = ?",
            (w + extra, b, len(rows), now_iso, int(calculo_id)),
        )
        conn.commit()
    finally:
        conn.close()

