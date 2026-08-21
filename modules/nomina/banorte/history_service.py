"""Read model de exportaciones Banorte basado solo en snapshots históricos."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import quote


class HistoricalExportNotFound(LookupError):
    pass


def _connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    resolved = Path(db_path).resolve().as_posix()
    uri = f"file:{quote(resolved, safe='/:')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def load_historical_export_movements(
    db_path: str | Path,
    export_id: int,
) -> dict[str, object]:
    """Devuelve header e items persistidos sin consultar estado vivo."""
    conn = _connect_readonly(db_path)
    try:
        export_row = conn.execute(
            """
            SELECT id, filename, layout_date, payment_count, total_cents
            FROM nomina_banorte_exports
            WHERE id=?
            """,
            (int(export_id),),
        ).fetchone()
        if export_row is None:
            raise HistoricalExportNotFound("export_not_found")
        item_rows = conn.execute(
            """
            SELECT position, nombre_recibido, employee_number_effective,
                   account_number, amount_cents
            FROM nomina_banorte_export_items
            WHERE export_id=?
            ORDER BY position
            """,
            (int(export_id),),
        ).fetchall()
    finally:
        conn.close()

    return {
        "export": {
            "export_id": int(export_row["id"]),
            "filename": str(export_row["filename"]),
            "layout_date": str(export_row["layout_date"]),
            "payment_count": int(export_row["payment_count"]),
            "total_cents": int(export_row["total_cents"]),
        },
        "items": [
            {
                "position": int(row["position"]),
                "historical_name": str(row["nombre_recibido"]),
                "employee_number": str(row["employee_number_effective"]),
                "account_number": str(row["account_number"]),
                "amount_cents": int(row["amount_cents"]),
            }
            for row in item_rows
        ],
    }
