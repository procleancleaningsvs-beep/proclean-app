from __future__ import annotations

import sqlite3

from modules.gestion_idse_sua.nominas.schema import ensure_gis_nominas_tables


def ensure_gestion_idse_sua_tables(conn: sqlite3.Connection) -> None:
    ensure_gis_nominas_tables(conn)
