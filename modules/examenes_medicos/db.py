from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


def ensure_examenes_medicos_tables(conn: sqlite3.Connection) -> None:
    from modules.examenes_medicos.expediente_db import (
        ensure_examenes_expediente_table,
        migrate_legacy_historial_to_expediente,
    )
    from modules.examenes_medicos.identifiers import migrate_examenes_medicos_identifier_tables

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS examenes_medicos_historial (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            exam_type TEXT NOT NULL CHECK(exam_type IN ('orina','sangre','imc')),
            patient_display_name TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            docx_relpath TEXT,
            pdf_relpath TEXT,
            docx_download_name TEXT,
            pdf_download_name TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_examenes_hist_created ON examenes_medicos_historial (created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_examenes_hist_user ON examenes_medicos_historial (user_id, created_at DESC)"
    )
    migrate_examenes_medicos_identifier_tables(conn)
    ensure_examenes_expediente_table(conn)
    migrate_legacy_historial_to_expediente(conn)


def ensure_examenes_medicos_tables_path(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        ensure_examenes_medicos_tables(conn)
        conn.commit()
    finally:
        conn.close()


@dataclass
class ExamenHistorialRow:
    id: int
    user_id: int
    created_at: str
    exam_type: str
    patient_display_name: str
    payload_json: str
    docx_relpath: str | None
    pdf_relpath: str | None
    docx_download_name: str | None
    pdf_download_name: str | None
    username: str | None = None


def _row(r: sqlite3.Row) -> ExamenHistorialRow:
    keys = set(r.keys())
    return ExamenHistorialRow(
        id=int(r["id"]),
        user_id=int(r["user_id"]),
        created_at=str(r["created_at"]),
        exam_type=str(r["exam_type"]),
        patient_display_name=str(r["patient_display_name"] or ""),
        payload_json=str(r["payload_json"] or "{}"),
        docx_relpath=str(r["docx_relpath"]) if r["docx_relpath"] else None,
        pdf_relpath=str(r["pdf_relpath"]) if r["pdf_relpath"] else None,
        docx_download_name=str(r["docx_download_name"]) if r["docx_download_name"] else None,
        pdf_download_name=str(r["pdf_download_name"]) if r["pdf_download_name"] else None,
        username=str(r["username"]) if "username" in keys and r["username"] is not None else None,
    )


def insert_examen_historial(
    db_path: str,
    *,
    user_id: int,
    created_at: str,
    exam_type: str,
    patient_display_name: str,
    payload: dict[str, Any],
    docx_relpath: str | None,
    pdf_relpath: str | None,
    docx_download_name: str | None,
    pdf_download_name: str | None,
) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO examenes_medicos_historial (
                user_id, created_at, exam_type, patient_display_name, payload_json,
                docx_relpath, pdf_relpath, docx_download_name, pdf_download_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                created_at,
                exam_type,
                patient_display_name,
                json.dumps(payload, ensure_ascii=False, default=str),
                docx_relpath,
                pdf_relpath,
                docx_download_name,
                pdf_download_name,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_examen_historial(db_path: str, record_id: int) -> ExamenHistorialRow | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute(
            """
            SELECT h.*, u.username AS username
            FROM examenes_medicos_historial h
            JOIN users u ON u.id = h.user_id
            WHERE h.id = ?
            """,
            (record_id,),
        ).fetchone()
        return _row(r) if r else None
    finally:
        conn.close()


def list_examen_historial(
    db_path: str, *, user_id: int | None = None, limit: int = 200
) -> list[ExamenHistorialRow]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        lim = max(1, min(500, int(limit)))
        if user_id is not None:
            rows = conn.execute(
                """
                SELECT h.*, u.username AS username
                FROM examenes_medicos_historial h
                JOIN users u ON u.id = h.user_id
                WHERE h.user_id = ?
                ORDER BY datetime(h.created_at) DESC
                LIMIT ?
                """,
                (user_id, lim),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT h.*, u.username AS username
                FROM examenes_medicos_historial h
                JOIN users u ON u.id = h.user_id
                ORDER BY datetime(h.created_at) DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def delete_examen_historial(db_path: str, record_id: int) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("DELETE FROM examenes_medicos_historial WHERE id = ?", (record_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
