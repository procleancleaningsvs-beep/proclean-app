from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from modules.nomina.config import get_holidays_for_year
from modules.nomina.db import NominaBaseRow

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_XLSX_PATH = BASE_DIR / "templates_excel" / "Plantilla_asistencia_v4.xlsx"

# --- v4 column layout (1-based) ---
COL_NOMBRE = 1   # A
COL_CLIENTE = 2  # B
COL_PLANTA = 3   # C
COL_PUESTO = 4   # D
COL_BANCO = 5    # E
COL_CUENTA = 6   # F
COL_DAY_START = 7  # G..M = 7 days (cols 7..13)
COL_DAY_END = 13
COL_HORAS_EXTRA = 14            # N
COL_HORAS_EXTRA_NORMALES = 15   # O
COL_DIAS_CUBIERTOS = 16         # P
COL_VACACIONES_LABORADAS = 17   # Q
COL_PRIMA_VACACIONAL = 18       # R
COL_BONO = 19                   # S
COL_DEDUCCIONES = 20            # T
COL_OBSERVACIONES = 21          # U

START_DATA_ROW = 5
DAILY_HEADER_ROW = 4


def week_label(fecha_inicio: date, fecha_fin: date) -> str:
    return f"{fecha_inicio.strftime('%d/%m/%Y')} al {fecha_fin.strftime('%d/%m/%Y')}"


def build_daily_headers(fecha_inicio: date) -> list[str]:
    day_codes = {0: "L", 1: "M", 2: "M", 3: "J", 4: "V", 5: "S", 6: "D"}
    out: list[str] = []
    for i in range(7):
        d = fecha_inicio + timedelta(days=i)
        out.append(f"{day_codes[d.weekday()]}{d.day}")
    return out


def _holidays_in_range(fecha_inicio: date) -> dict[int, date]:
    """Return mapping {day_index_in_period (0..6) -> date} for official holidays."""
    out: dict[int, date] = {}
    for i in range(7):
        d = fecha_inicio + timedelta(days=i)
        holidays = get_holidays_for_year(d.year)
        if d in holidays:
            out[i] = d
    return out


def _write_label_with_value(ws: Worksheet, label: str, value: str) -> None:
    """v4: SEMANA:/CLIENTE:/COORDINADOR: labels live in merged top cell.
    Write 'LABEL: VALUE' into the same cell so it renders inside the merged box.
    """
    target = (label or "").strip()
    if not target.endswith(":"):
        target += ":"
    for row in range(1, 6):
        for col in range(1, ws.max_column + 1):
            cell_value = ws.cell(row=row, column=col).value
            if cell_value is None:
                continue
            text = str(cell_value).strip()
            if text.split()[0].upper().rstrip(":") + ":" == target.upper():
                ws.cell(row=row, column=col, value=f"{target} {value}")
                return
            if text.upper() == target.upper():
                ws.cell(row=row, column=col, value=f"{target} {value}")
                return


def _write_superior_fields(ws: Worksheet, semana: str, cliente: str, coordinador: str) -> None:
    _write_label_with_value(ws, "SEMANA", semana)
    _write_label_with_value(ws, "CLIENTE", cliente)
    _write_label_with_value(ws, "COORDINADOR", coordinador)


def _write_daily_headers(ws: Worksheet, headers: list[str]) -> None:
    for idx, header in enumerate(headers):
        ws.cell(row=DAILY_HEADER_ROW, column=COL_DAY_START + idx, value=header)


def _write_base_rows(
    ws: Worksheet,
    base_rows: Iterable[NominaBaseRow],
    holiday_day_indices: list[int],
) -> int:
    written = 0
    for i, item in enumerate(base_rows):
        row = START_DATA_ROW + i
        ws.cell(row=row, column=COL_NOMBRE, value=item.nombre_empleado or "")
        ws.cell(row=row, column=COL_CLIENTE, value=item.cliente or "")
        ws.cell(row=row, column=COL_PLANTA, value=item.planta or "")
        ws.cell(row=row, column=COL_PUESTO, value=item.puesto or "")
        ws.cell(row=row, column=COL_BANCO, value=item.banco or "")
        ws.cell(row=row, column=COL_CUENTA, value=item.cuenta or "")
        for idx in range(7):
            col = COL_DAY_START + idx
            ws.cell(row=row, column=col, value=("FE" if idx in holiday_day_indices else ""))
        ws.cell(row=row, column=COL_HORAS_EXTRA, value="")
        ws.cell(row=row, column=COL_HORAS_EXTRA_NORMALES, value="")
        ws.cell(row=row, column=COL_DIAS_CUBIERTOS, value="")
        ws.cell(row=row, column=COL_VACACIONES_LABORADAS, value="")
        ws.cell(row=row, column=COL_PRIMA_VACACIONAL, value="N/A")
        ws.cell(row=row, column=COL_BONO, value="")
        ws.cell(row=row, column=COL_DEDUCCIONES, value="")
        ws.cell(row=row, column=COL_OBSERVACIONES, value="")
        written += 1
    return written


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
    holiday_map = _holidays_in_range(fecha_inicio)
    _write_base_rows(ws, base_rows, sorted(holiday_map.keys()))

    output = BytesIO()
    wb.save(output)
    return output.getvalue()
