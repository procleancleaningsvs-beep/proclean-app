from __future__ import annotations

import sqlite3
from pathlib import Path

from modules.nomina.banorte.schema import ensure_banorte_tables


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_schema(db_path: str | Path) -> None:
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        conn.commit()
    finally:
        conn.close()
