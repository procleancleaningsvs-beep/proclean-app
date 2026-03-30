"""Historial persistente de finiquitos y liquidaciones comparativas (snapshots JSON)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def ensure_finiquitos_tables(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS historial_finiquitos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                modo_calculo TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                pdf_path TEXT,
                pdf_filename TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS historial_liquidaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def insert_finiquito_history(
    db_path: str,
    *,
    user_id: int,
    created_at: str,
    modo_calculo: str,
    payload: dict[str, Any],
    pdf_path: str | None,
    pdf_filename: str | None,
) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO historial_finiquitos
            (user_id, created_at, modo_calculo, payload_json, pdf_path, pdf_filename)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                created_at,
                modo_calculo,
                json.dumps(payload, ensure_ascii=False, default=str),
                pdf_path,
                pdf_filename,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def insert_liquidacion_history(
    db_path: str,
    *,
    user_id: int,
    created_at: str,
    payload: dict[str, Any],
) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO historial_liquidaciones (user_id, created_at, payload_json)
            VALUES (?, ?, ?)
            """,
            (user_id, created_at, json.dumps(payload, ensure_ascii=False, default=str)),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_finiquito_history(db_path: str, limit: int = 200) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT h.*, u.username
            FROM historial_finiquitos h
            JOIN users u ON u.id = h.user_id
            ORDER BY h.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()


def list_liquidacion_history(db_path: str, limit: int = 200) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT h.*, u.username
            FROM historial_liquidaciones h
            JOIN users u ON u.id = h.user_id
            ORDER BY h.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()
