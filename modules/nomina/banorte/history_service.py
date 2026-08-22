"""Read model de exportaciones Banorte basado solo en snapshots históricos."""
from __future__ import annotations

import io
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote

from openpyxl import Workbook
from openpyxl.styles import Font

from modules.nomina.banorte.money import format_pesos_from_cents


class HistoricalExportNotFound(LookupError):
    pass


_HISTORICAL_ITEM_COLUMNS = """
    position, nombre_recibido, employee_number_effective, account_number,
    amount_cents, match_kind, validation_status, record_status,
    is_manual_beneficiary, warnings_json, user_decision_json
"""


def _connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    resolved = Path(db_path).resolve().as_posix()
    uri = f"file:{quote(resolved, safe='/:')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _load_export_header(conn: sqlite3.Connection, export_id: int) -> sqlite3.Row:
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
    return export_row


def _load_export_item_rows(conn: sqlite3.Connection, export_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        f"""
        SELECT {_HISTORICAL_ITEM_COLUMNS}
        FROM nomina_banorte_export_items
        WHERE export_id=?
        ORDER BY position
        """,
        (int(export_id),),
    ).fetchall()


def historical_export_excel_filename(*, export_id: int, pag_filename: str) -> str:
    stem = str(pag_filename or f"banorte-export-{export_id}")
    if stem.lower().endswith(".pag"):
        stem = stem[:-4]
    safe = re.sub(r"[^\w.\-]+", "_", stem).strip("._") or f"banorte-export-{export_id}"
    return f"{safe}-movimientos-historico.xlsx"


def load_historical_export_movements(
    db_path: str | Path,
    export_id: int,
) -> dict[str, object]:
    """Devuelve header e items persistidos sin consultar estado vivo."""
    conn = _connect_readonly(db_path)
    try:
        export_row = _load_export_header(conn, export_id)
        item_rows = _load_export_item_rows(conn, export_id)
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


def build_historical_export_excel(
    db_path: str | Path,
    export_id: int,
) -> dict[str, object]:
    """Build .xlsx bytes from export_items snapshot only."""
    conn = _connect_readonly(db_path)
    try:
        export_row = _load_export_header(conn, export_id)
        item_rows = _load_export_item_rows(conn, export_id)
    finally:
        conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Movimientos"
    headers = [
        "Posición",
        "Nombre histórico",
        "No. empleado",
        "Cuenta",
        "Importe",
        "Tipo coincidencia",
        "Estatus validación",
        "Estatus registro",
        "Beneficiario manual",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    total_cents = 0
    for row in item_rows:
        cents = int(row["amount_cents"])
        total_cents += cents
        ws.append(
            [
                int(row["position"]),
                str(row["nombre_recibido"]),
                str(row["employee_number_effective"]),
                str(row["account_number"]),
                format_pesos_from_cents(cents),
                str(row["match_kind"]),
                str(row["validation_status"]),
                str(row["record_status"]),
                "Sí" if int(row["is_manual_beneficiary"] or 0) == 1 else "No",
            ]
        )
    ws.append([])
    ws.append(["Total histórico", "", "", "", format_pesos_from_cents(total_cents)])
    ws.append(["Pagos históricos", int(export_row["payment_count"])])
    buffer = io.BytesIO()
    wb.save(buffer)
    filename = historical_export_excel_filename(
        export_id=int(export_row["id"]),
        pag_filename=str(export_row["filename"]),
    )
    return {
        "filename": filename,
        "data": buffer.getvalue(),
        "row_count": len(item_rows),
        "total_cents": total_cents,
    }
