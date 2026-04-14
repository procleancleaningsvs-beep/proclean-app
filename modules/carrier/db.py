from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
            slots_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
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
    return ExpedienteRow(
        id=int(r["id"]),
        user_id=int(r["user_id"]),
        nombre_persona=str(r["nombre_persona"]),
        base_year_month=str(r["base_year_month"]),
        alta_format_history_id=int(r["alta_format_history_id"]) if r["alta_format_history_id"] is not None else None,
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
                slots_json, created_at, updated_at
            ) VALUES (?, ?, ?, NULL, '{}', ?, ?)
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
            SET alta_format_history_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (format_history_id, updated_at, expediente_id),
        )
        conn.commit()
        return True
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
            SET alta_format_history_id = NULL, updated_at = ?
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
