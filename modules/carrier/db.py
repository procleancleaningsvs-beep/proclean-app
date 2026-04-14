from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _migrate_carrier_expediente_extra_columns(conn: sqlite3.Connection) -> None:
    """SQLite: añade columnas nuevas en instalaciones ya existentes."""
    cur = conn.execute("PRAGMA table_info(carrier_curso_expediente)")
    cols = {row[1] for row in cur.fetchall()}
    if "constancia_modo" not in cols:
        conn.execute(
            "ALTER TABLE carrier_curso_expediente ADD COLUMN constancia_modo TEXT NOT NULL DEFAULT 'alta'"
        )
    if "alta_movimiento_idx" not in cols:
        conn.execute(
            "ALTER TABLE carrier_curso_expediente ADD COLUMN alta_movimiento_idx INTEGER NOT NULL DEFAULT 0"
        )
    conn.commit()


def ensure_carrier_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS carrier_monthly_base (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year_month TEXT NOT NULL UNIQUE,
            sipare_relpath TEXT,
            sipare_orig_name TEXT,
            pago_imss_relpath TEXT,
            pago_imss_orig_name TEXT,
            updated_at TEXT NOT NULL,
            updated_by INTEGER,
            FOREIGN KEY(updated_by) REFERENCES users(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS carrier_curso_expediente (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            nombre_persona TEXT NOT NULL,
            base_year_month TEXT NOT NULL,
            alta_format_history_id INTEGER,
            constancia_modo TEXT NOT NULL DEFAULT 'alta',
            alta_movimiento_idx INTEGER NOT NULL DEFAULT 0,
            slots_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(alta_format_history_id) REFERENCES format_history(id)
        )
        """
    )
    _migrate_carrier_expediente_extra_columns(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS carrier_curso_export_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            expediente_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            nombre_persona TEXT NOT NULL,
            pdf_stored_relpath TEXT NOT NULL,
            pdf_display_name TEXT NOT NULL,
            alta_format_history_id INTEGER,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(expediente_id) REFERENCES carrier_curso_expediente(id),
            FOREIGN KEY(alta_format_history_id) REFERENCES format_history(id)
        )
        """
    )
    conn.commit()


@dataclass
class MonthlyBaseRow:
    id: int
    year_month: str
    sipare_relpath: str | None
    sipare_orig_name: str | None
    pago_imss_relpath: str | None
    pago_imss_orig_name: str | None
    updated_at: str
    updated_by: int | None


@dataclass
class ExpedienteRow:
    id: int
    user_id: int
    nombre_persona: str
    base_year_month: str
    alta_format_history_id: int | None
    constancia_modo: str
    alta_movimiento_idx: int
    slots_json: str
    created_at: str
    updated_at: str


def _row_monthly(r: sqlite3.Row) -> MonthlyBaseRow:
    return MonthlyBaseRow(
        id=int(r["id"]),
        year_month=str(r["year_month"]),
        sipare_relpath=r["sipare_relpath"],
        sipare_orig_name=r["sipare_orig_name"],
        pago_imss_relpath=r["pago_imss_relpath"],
        pago_imss_orig_name=r["pago_imss_orig_name"],
        updated_at=str(r["updated_at"]),
        updated_by=int(r["updated_by"]) if r["updated_by"] is not None else None,
    )


def _row_exp(r: sqlite3.Row) -> ExpedienteRow:
    keys = set(r.keys())
    modo = str(r["constancia_modo"]) if "constancia_modo" in keys else "alta"
    idx_raw = r["alta_movimiento_idx"] if "alta_movimiento_idx" in keys else 0
    idx = int(idx_raw) if idx_raw is not None else 0
    return ExpedienteRow(
        id=int(r["id"]),
        user_id=int(r["user_id"]),
        nombre_persona=str(r["nombre_persona"]),
        base_year_month=str(r["base_year_month"]),
        alta_format_history_id=int(r["alta_format_history_id"]) if r["alta_format_history_id"] is not None else None,
        constancia_modo=modo if modo in {"alta", "renovacion"} else "alta",
        alta_movimiento_idx=idx,
        slots_json=str(r["slots_json"] or "{}"),
        created_at=str(r["created_at"]),
        updated_at=str(r["updated_at"]),
    )


def get_monthly_base(db_path: str, year_month: str) -> MonthlyBaseRow | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute(
            "SELECT * FROM carrier_monthly_base WHERE year_month = ?",
            (year_month,),
        ).fetchone()
        return _row_monthly(r) if r else None
    finally:
        conn.close()


def list_monthly_bases(db_path: str) -> list[MonthlyBaseRow]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM carrier_monthly_base ORDER BY year_month DESC"
        ).fetchall()
        return [_row_monthly(r) for r in rows]
    finally:
        conn.close()


def upsert_monthly_base(
    db_path: str,
    year_month: str,
    *,
    sipare_relpath: str | None,
    sipare_orig_name: str | None,
    pago_imss_relpath: str | None,
    pago_imss_orig_name: str | None,
    updated_at: str,
    updated_by: int,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        existing = conn.execute(
            "SELECT id FROM carrier_monthly_base WHERE year_month = ?",
            (year_month,),
        ).fetchone()
        if existing:
            sets: list[str] = ["updated_at = ?", "updated_by = ?"]
            vals: list[Any] = [updated_at, updated_by]
            if sipare_relpath is not None:
                sets.append("sipare_relpath = ?")
                vals.append(sipare_relpath)
            if sipare_orig_name is not None:
                sets.append("sipare_orig_name = ?")
                vals.append(sipare_orig_name)
            if pago_imss_relpath is not None:
                sets.append("pago_imss_relpath = ?")
                vals.append(pago_imss_relpath)
            if pago_imss_orig_name is not None:
                sets.append("pago_imss_orig_name = ?")
                vals.append(pago_imss_orig_name)
            vals.append(year_month)
            conn.execute(
                f"UPDATE carrier_monthly_base SET {', '.join(sets)} WHERE year_month = ?",
                vals,
            )
        else:
            conn.execute(
                """
                INSERT INTO carrier_monthly_base (
                    year_month, sipare_relpath, sipare_orig_name,
                    pago_imss_relpath, pago_imss_orig_name, updated_at, updated_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    year_month,
                    sipare_relpath,
                    sipare_orig_name,
                    pago_imss_relpath,
                    pago_imss_orig_name,
                    updated_at,
                    updated_by,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def insert_expediente(
    db_path: str,
    *,
    user_id: int,
    nombre_persona: str,
    base_year_month: str,
    created_at: str,
    updated_at: str,
) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO carrier_curso_expediente (
                user_id, nombre_persona, base_year_month, alta_format_history_id,
                constancia_modo, alta_movimiento_idx,
                slots_json, created_at, updated_at
            ) VALUES (?, ?, ?, NULL, 'alta', 0, '{}', ?, ?)
            """,
            (user_id, nombre_persona, base_year_month, created_at, updated_at),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_expediente(db_path: str, expediente_id: int) -> ExpedienteRow | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute(
            "SELECT * FROM carrier_curso_expediente WHERE id = ?",
            (expediente_id,),
        ).fetchone()
        return _row_exp(r) if r else None
    finally:
        conn.close()


def list_expedientes(db_path: str, *, user_id: int | None = None, limit: int = 500) -> list[ExpedienteRow]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if user_id is not None:
            rows = conn.execute(
                """
                SELECT * FROM carrier_curso_expediente
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM carrier_curso_expediente
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_row_exp(r) for r in rows]
    finally:
        conn.close()


def update_expediente_meta(
    db_path: str,
    expediente_id: int,
    *,
    nombre_persona: str | None = None,
    base_year_month: str | None = None,
    updated_at: str,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        sets: list[str] = ["updated_at = ?"]
        vals: list[Any] = [updated_at]
        if nombre_persona is not None:
            sets.append("nombre_persona = ?")
            vals.append(nombre_persona)
        if base_year_month is not None:
            sets.append("base_year_month = ?")
            vals.append(base_year_month)
        vals.append(expediente_id)
        conn.execute(
            f"UPDATE carrier_curso_expediente SET {', '.join(sets)} WHERE id = ?",
            vals,
        )
        conn.commit()
    finally:
        conn.close()


def update_expediente_slots_json(db_path: str, expediente_id: int, slots_json: str, updated_at: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            UPDATE carrier_curso_expediente
            SET slots_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (slots_json, updated_at, expediente_id),
        )
        conn.commit()
    finally:
        conn.close()


def attach_alta_format_history(
    db_path: str,
    expediente_id: int,
    user_id: int,
    format_history_id: int,
    updated_at: str,
    *,
    movimiento_idx: int = 0,
) -> bool:
    """Vincula un registro de format_history como Alta del expediente. Valida propiedad."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        exp = conn.execute(
            "SELECT id, user_id FROM carrier_curso_expediente WHERE id = ?",
            (expediente_id,),
        ).fetchone()
        if not exp or int(exp["user_id"]) != int(user_id):
            return False
        hist = conn.execute(
            "SELECT id, user_id FROM format_history WHERE id = ?",
            (format_history_id,),
        ).fetchone()
        if not hist or int(hist["user_id"]) != int(user_id):
            return False
        conn.execute(
            """
            UPDATE carrier_curso_expediente
            SET alta_format_history_id = ?, alta_movimiento_idx = ?,
                constancia_modo = 'alta', updated_at = ?
            WHERE id = ?
            """,
            (format_history_id, int(movimiento_idx), updated_at, expediente_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def update_expediente_constancia_modo(
    db_path: str, expediente_id: int, user_id: int, modo: str, updated_at: str
) -> bool:
    """`alta` | `renovacion`. En renovación se limpia la constancia vinculada."""
    if modo not in {"alta", "renovacion"}:
        return False
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        exp = conn.execute(
            "SELECT user_id FROM carrier_curso_expediente WHERE id = ?",
            (expediente_id,),
        ).fetchone()
        if not exp or int(exp["user_id"]) != int(user_id):
            return False
        if modo == "renovacion":
            conn.execute(
                """
                UPDATE carrier_curso_expediente
                SET constancia_modo = ?, alta_format_history_id = NULL,
                    alta_movimiento_idx = 0, updated_at = ?
                WHERE id = ?
                """,
                (modo, updated_at, expediente_id),
            )
        else:
            conn.execute(
                """
                UPDATE carrier_curso_expediente
                SET constancia_modo = ?, updated_at = ?
                WHERE id = ?
                """,
                (modo, updated_at, expediente_id),
            )
        conn.commit()
        return True
    finally:
        conn.close()


def list_format_history_for_carrier(db_path: str, user_id: int, limit: int = 80) -> list[dict[str, Any]]:
    """Lista reciente (compatibilidad). Preferir `list_format_history_for_carrier_page`."""
    return list_format_history_for_carrier_page(db_path, user_id, q=None, offset=0, limit=limit)


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def count_format_history_for_carrier(db_path: str, user_id: int, q: str | None) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        needle = (q or "").strip()
        if not needle:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM format_history WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()
        else:
            pat = f"%{_like_escape(needle)}%"
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM format_history
                WHERE user_id = ?
                  AND (filename LIKE ? ESCAPE '\\' OR IFNULL(payload_json, '') LIKE ? ESCAPE '\\')
                """,
                (int(user_id), pat, pat),
            ).fetchone()
        return int(row["c"]) if row else 0
    finally:
        conn.close()


def list_format_history_for_carrier_page(
    db_path: str,
    user_id: int,
    *,
    q: str | None,
    offset: int,
    limit: int,
) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        off = max(0, int(offset))
        lim = max(1, min(100, int(limit)))
        needle = (q or "").strip()
        if not needle:
            rows = conn.execute(
                """
                SELECT id, filename, pdf_path, movement_count, payload_json, created_at
                FROM format_history
                WHERE user_id = ?
                ORDER BY datetime(created_at) DESC
                LIMIT ? OFFSET ?
                """,
                (int(user_id), lim, off),
            ).fetchall()
        else:
            pat = f"%{_like_escape(needle)}%"
            rows = conn.execute(
                """
                SELECT id, filename, pdf_path, movement_count, payload_json, created_at
                FROM format_history
                WHERE user_id = ?
                  AND (filename LIKE ? ESCAPE '\\' OR IFNULL(payload_json, '') LIKE ? ESCAPE '\\')
                ORDER BY datetime(created_at) DESC
                LIMIT ? OFFSET ?
                """,
                (int(user_id), pat, pat, lim, off),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": int(r["id"]),
                    "filename": str(r["filename"]),
                    "pdf_path": str(r["pdf_path"]),
                    "movement_count": int(r["movement_count"] or 0),
                    "payload_json": str(r["payload_json"] or ""),
                    "created_at": str(r["created_at"]),
                }
            )
        return out
    finally:
        conn.close()


def clear_alta_link(db_path: str, expediente_id: int, user_id: int, updated_at: str) -> bool:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        exp = conn.execute(
            "SELECT user_id FROM carrier_curso_expediente WHERE id = ?",
            (expediente_id,),
        ).fetchone()
        if not exp or int(exp["user_id"]) != int(user_id):
            return False
        conn.execute(
            """
            UPDATE carrier_curso_expediente
            SET alta_format_history_id = NULL, alta_movimiento_idx = 0, updated_at = ?
            WHERE id = ?
            """,
            (updated_at, expediente_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def parse_slots(slots_json: str) -> dict[str, Any]:
    try:
        data = json.loads(slots_json or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def dumps_slots(slots: dict[str, Any]) -> str:
    return json.dumps(slots, ensure_ascii=False)


@dataclass
class ExportLogRow:
    id: int
    user_id: int
    expediente_id: int
    created_at: str
    nombre_persona: str
    pdf_stored_relpath: str
    pdf_display_name: str
    alta_format_history_id: int | None
    username: str | None = None
    expediente_updated_at: str | None = None


def _row_export(r: sqlite3.Row) -> ExportLogRow:
    keys = set(r.keys())
    username = None
    if "username" in keys and r["username"] is not None:
        username = str(r["username"])
    exp_upd = None
    if "expediente_updated_at" in keys and r["expediente_updated_at"] is not None:
        exp_upd = str(r["expediente_updated_at"])
    return ExportLogRow(
        id=int(r["id"]),
        user_id=int(r["user_id"]),
        expediente_id=int(r["expediente_id"]),
        created_at=str(r["created_at"]),
        nombre_persona=str(r["nombre_persona"]),
        pdf_stored_relpath=str(r["pdf_stored_relpath"]),
        pdf_display_name=str(r["pdf_display_name"]),
        alta_format_history_id=int(r["alta_format_history_id"]) if r["alta_format_history_id"] is not None else None,
        username=username,
        expediente_updated_at=exp_upd,
    )


def insert_carrier_curso_export_log(
    db_path: str,
    *,
    user_id: int,
    expediente_id: int,
    created_at: str,
    nombre_persona: str,
    pdf_stored_relpath: str,
    pdf_display_name: str,
    alta_format_history_id: int | None,
) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO carrier_curso_export_log (
                user_id, expediente_id, created_at, nombre_persona,
                pdf_stored_relpath, pdf_display_name, alta_format_history_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                expediente_id,
                created_at,
                nombre_persona,
                pdf_stored_relpath,
                pdf_display_name,
                alta_format_history_id,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_carrier_curso_export_log(db_path: str, log_id: int) -> ExportLogRow | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute(
            "SELECT * FROM carrier_curso_export_log WHERE id = ?",
            (log_id,),
        ).fetchone()
        return _row_export(r) if r else None
    finally:
        conn.close()


def list_carrier_curso_export_logs(
    db_path: str, *, user_id: int | None = None, limit: int = 200
) -> list[ExportLogRow]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if user_id is not None:
            rows = conn.execute(
                """
                SELECT l.*, u.username AS username, e.updated_at AS expediente_updated_at
                FROM carrier_curso_export_log l
                JOIN users u ON u.id = l.user_id
                JOIN carrier_curso_expediente e ON e.id = l.expediente_id
                WHERE l.user_id = ?
                ORDER BY l.created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT l.*, u.username AS username, e.updated_at AS expediente_updated_at
                FROM carrier_curso_export_log l
                JOIN users u ON u.id = l.user_id
                JOIN carrier_curso_expediente e ON e.id = l.expediente_id
                ORDER BY l.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_row_export(r) for r in rows]
    finally:
        conn.close()


def list_carrier_curso_export_logs_for_expediente(
    db_path: str, expediente_id: int
) -> list[ExportLogRow]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT l.*, u.username AS username
            FROM carrier_curso_export_log l
            JOIN users u ON u.id = l.user_id
            WHERE l.expediente_id = ?
            ORDER BY l.created_at DESC
            """,
            (expediente_id,),
        ).fetchall()
        return [_row_export(r) for r in rows]
    finally:
        conn.close()


def sync_expediente_nombre_desde_alta(
    db_path: str,
    expediente_id: int,
    format_history_id: int,
    updated_at: str,
    *,
    movimiento_idx: int = 0,
) -> bool:
    """Copia el nombre del movimiento elegido del Alta al expediente."""
    from modules.carrier.export_naming import worker_name_from_payload_json

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute(
            "SELECT payload_json FROM format_history WHERE id = ?",
            (format_history_id,),
        ).fetchone()
        if not r:
            return False
        nombre = worker_name_from_payload_json(r["payload_json"], int(movimiento_idx))
        if not nombre:
            return False
        conn.execute(
            """
            UPDATE carrier_curso_expediente
            SET nombre_persona = ?, updated_at = ?
            WHERE id = ?
            """,
            (nombre, updated_at, expediente_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_carrier_curso_export_log(db_path: str, log_id: int) -> bool:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute(
            "SELECT pdf_stored_relpath FROM carrier_curso_export_log WHERE id = ?",
            (log_id,),
        ).fetchone()
        if not r:
            return False
        conn.execute("DELETE FROM carrier_curso_export_log WHERE id = ?", (log_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def delete_expediente_row(db_path: str, expediente_id: int) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("DELETE FROM carrier_curso_expediente WHERE id = ?", (expediente_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
