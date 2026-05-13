from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from modules.nomina.db import NominaBaseRow

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_XLSX_PATH = BASE_DIR / "templates_excel" / "Plantilla_asistencia_v3.xlsx"


def week_label(fecha_inicio: date, fecha_fin: date) -> str:
    return f"{fecha_inicio.strftime('%d/%m/%Y')} al {fecha_fin.strftime('%d/%m/%Y')}"


def build_daily_headers(fecha_inicio: date) -> list[str]:
    day_codes = {0: "L", 1: "M", 2: "M", 3: "J", 4: "V", 5: "S", 6: "D"}
    out: list[str] = []
    for i in range(7):
        d = fecha_inicio + timedelta(days=i)
        out.append(f"{day_codes[d.weekday()]}{d.day}")
    return out


def _find_value_cell_for_label(ws: Worksheet, label: str) -> tuple[int, int]:
    wanted = " ".join(label.upper().replace("\n", " ").split())
    for row in range(1, 6):
        for col in range(1, ws.max_column + 1):
            text = " ".join(str(ws.cell(row=row, column=col).value or "").upper().replace("\n", " ").split())
            if text != wanted:
                continue
            label_end_col = col
            for merged in ws.merged_cells.ranges:
                if merged.min_row <= row <= merged.max_row and merged.min_col <= col <= merged.max_col:
                    label_end_col = max(label_end_col, merged.max_col)
            candidate_col = label_end_col + 1
            for merged in ws.merged_cells.ranges:
                if merged.min_row <= row <= merged.max_row and merged.min_col > label_end_col:
                    if merged.min_col < candidate_col:
                        candidate_col = merged.min_col
            return row, candidate_col
    raise ValueError(f"No se encontró etiqueta {label} en plantilla Asistencia.")


def _write_superior_fields(ws: Worksheet, semana: str, cliente: str, coordinador: str) -> None:
    semana_cell = _find_value_cell_for_label(ws, "SEMANA:")
    cliente_cell = _find_value_cell_for_label(ws, "CLIENTE:")
    coordinador_cell = _find_value_cell_for_label(ws, "COORDINADOR:")
    ws.cell(row=semana_cell[0], column=semana_cell[1], value=semana)
    ws.cell(row=cliente_cell[0], column=cliente_cell[1], value=cliente)
    ws.cell(row=coordinador_cell[0], column=coordinador_cell[1], value=coordinador)


def _write_daily_headers(ws: Worksheet, headers: list[str]) -> None:
    for idx, header in enumerate(headers):
        ws.cell(row=4, column=8 + idx, value=header)


def _write_base_rows(ws: Worksheet, base_rows: Iterable[NominaBaseRow]) -> None:
    start_row = 5
    for i, item in enumerate(base_rows):
        row = start_row + i
        ws.cell(row=row, column=1, value=item.nombre_empleado or "")
        ws.cell(row=row, column=3, value=item.cliente or "")
        ws.cell(row=row, column=4, value=item.planta or "")
        ws.cell(row=row, column=5, value=item.puesto or "")
        ws.cell(row=row, column=6, value=item.banco or "")
        ws.cell(row=row, column=7, value=item.cuenta or "")
        for col in range(8, 15):
            ws.cell(row=row, column=col, value="")
        ws.cell(row=row, column=15, value="")
        ws.cell(row=row, column=16, value="")
        ws.cell(row=row, column=17, value="")
        ws.cell(row=row, column=18, value="")
        ws.cell(row=row, column=19, value="")
        ws.cell(row=row, column=20, value="N/A")
        ws.cell(row=row, column=21, value="")
        ws.cell(row=row, column=22, value="")
        ws.cell(row=row, column=23, value="")


def build_asistencia_template_file(
    *,
    fecha_inicio: date,
    fecha_fin: date,
    cliente: str,
    coordinador: str,
    base_rows: Iterable[NominaBaseRow],
) -> bytes:
    if not TEMPLATE_XLSX_PATH.exists():
        raise FileNotFoundError(f"No existe plantilla base: {TEMPLATE_XLSX_PATH}")
    wb = load_workbook(TEMPLATE_XLSX_PATH)
    if "Asistencia" not in wb.sheetnames:
        raise ValueError("La plantilla base no contiene la hoja 'Asistencia'.")
    ws = wb["Asistencia"]
    _write_superior_fields(ws, week_label(fecha_inicio, fecha_fin), cliente.strip(), coordinador.strip())
    _write_daily_headers(ws, build_daily_headers(fecha_inicio))
    _write_base_rows(ws, base_rows)

    output = BytesIO()
    wb.save(output)
    return output.getvalue()

