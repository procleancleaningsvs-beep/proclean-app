from __future__ import annotations

import sqlite3
from typing import Any


def active_catalog_version_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT id FROM nomina_banorte_catalog_versions WHERE status='ACTIVE'"
    ).fetchone()
    return int(row["id"]) if row is not None else None


def has_ever_had_catalog_activation(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM nomina_banorte_catalog_events
        WHERE event_type='VERSION_ACTIVATED'
        LIMIT 1
        """
    ).fetchone()
    return row is not None


def legacy_authority_allowed(conn: sqlite3.Connection) -> bool:
    """Pre-first-activation: legacy resolver remains permitted."""
    if active_catalog_version_id(conn) is not None:
        return False
    return not has_ever_had_catalog_activation(conn)


def effective_catalog_mode(conn: sqlite3.Connection) -> tuple[str, int | None]:
    active_id = active_catalog_version_id(conn)
    if active_id is not None:
        return "CATALOG", active_id
    if has_ever_had_catalog_activation(conn):
        return "CATALOG", None
    return "LEGACY", None


def catalog_draft_binding(conn: sqlite3.Connection) -> dict[str, Any]:
    mode, version_id = effective_catalog_mode(conn)
    return {
        "catalog_mode": mode,
        "catalog_version_id": version_id,
        "legacy_allowed": legacy_authority_allowed(conn),
        "fail_closed": mode == "CATALOG" and version_id is None,
    }
