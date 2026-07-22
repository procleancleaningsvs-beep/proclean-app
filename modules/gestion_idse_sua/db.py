from __future__ import annotations

import sqlite3

from modules.gestion_idse_sua.nominas.schema import ensure_gis_nominas_tables
from modules.gestion_idse_sua.reportes.schema import ensure_gis_monthly_tables


def ensure_gestion_idse_sua_tables(conn: sqlite3.Connection) -> None:
    ensure_gis_nominas_tables(conn)
    ensure_gis_monthly_tables(conn)
