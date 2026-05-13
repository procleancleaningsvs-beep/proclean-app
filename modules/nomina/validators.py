from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

VALID_DAILY_KEYS = {"A", "D", "F", "V", "I", "PSS", "PCS", "NI", "B", "R", "S", "FE", "FL", "DL", "OT"}
NUMERIC_NON_NEGATIVE_COLUMNS = {
    "he": 15,
    "turnos_extra_normales": 16,
    "dias_cubiertos_normales": 17,
    "festivo_laborado": 18,
    "vacaciones_laboradas": 19,
}


class ValidationError(Exception):
    pass


@dataclass
class HeaderMeta:
    semana: str
    cliente: str
    coordinador: str
    dias_headers: list[str]


def _norm_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\u00a0", " ").replace("\n", " ").split()).strip()


def _norm_header(value: Any) -> str:
    raw = _norm_text(value).upper()
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    return raw


def _as_text_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return _norm_text(value)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _extract_label_value_cell(ws: Worksheet, label: str) -> tuple[int, int] | None:
    wanted = _norm_header(label)
    for row in range(1, 6):
        for col in range(1, ws.max_column + 1):
            current = _norm_header(ws.cell(row=row, column=col).value)
            if current != wanted:
                continue
            label_end_col = col
            for merged in ws.merged_cells.ranges:
                if merged.min_row <= row <= merged.max_row and merged.min_col <= col <= merged.max_col:
                    label_end_col = max(label_end_col, merged.max_col)
            candidate_col = label_end_col + 1
            for merged in ws.merged_cells.ranges:
                if merged.min_row <= row <= merged.max_row and merged.min_col > label_end_col:
                    if candidate_col is None or merged.min_col < candidate_col:
                        candidate_col = merged.min_col
            return (row, candidate_col)
    return None


def _read_header_meta(ws: Worksheet) -> HeaderMeta:
    semana_cell = _extract_label_value_cell(ws, "SEMANA:")
    cliente_cell = _extract_label_value_cell(ws, "CLIENTE:")
    coordinador_cell = _extract_label_value_cell(ws, "COORDINADOR:")
    if semana_cell is None or cliente_cell is None or coordinador_cell is None:
        raise ValidationError(
            "Faltan campos superiores obligatorios: SEMANA, CLIENTE o COORDINADOR."
        )
    semana = _as_text_or_empty(ws.cell(row=semana_cell[0], column=semana_cell[1]).value)
    cliente = _as_text_or_empty(ws.cell(row=cliente_cell[0], column=cliente_cell[1]).value)
    coordinador = _as_text_or_empty(ws.cell(row=coordinador_cell[0], column=coordinador_cell[1]).value)
    dias_headers = [_as_text_or_empty(ws.cell(row=4, column=col).value) for col in range(8, 15)]
    return HeaderMeta(
        semana=semana,
        cliente=cliente,
        coordinador=coordinador,
        dias_headers=dias_headers,
    )


def _validate_header_structure(ws: Worksheet) -> None:
    header_row = 4
    expected_by_col = {
        1: "NOMBRE DE EMPLEADO",
        3: "CLIENTE",
        4: "PLANTA",
        5: "PUESTO",
        6: "BANCO",
        7: "CUENTA",
        15: "HE",
        16: "TURNOS EXTRA NORMALES",
        17: "DIAS CUBIERTOS NORMALES",
        18: "FESTIVO LABORADO",
        19: "VACACIONES LABORADAS",
        20: "PRIMA VACACIONAL",
        21: "BONO",
        22: "DEDUCCIONES",
        23: "OBSERVACIONES",
    }
    missing: list[str] = []
    for col, expected in expected_by_col.items():
        got = _norm_header(ws.cell(row=header_row, column=col).value)
        if got != _norm_header(expected):
            missing.append(f"columna {col} ({expected})")
    if missing:
        raise ValidationError(f"Faltan encabezados obligatorios en Asistencia: {', '.join(missing)}")

    daily = [_as_text_or_empty(ws.cell(row=4, column=col).value) for col in range(8, 15)]
    if len(daily) != 7:
        raise ValidationError("Las columnas H:N deben contener exactamente 7 días de asistencia.")
    for value in daily:
        if not re.match(r"^[LMDJSV]\d{1,2}$", value.strip().upper()):
            raise ValidationError(
                "Las columnas H:N deben tener encabezados válidos con formato abreviatura+día (ejemplo V1)."
            )


def _row_is_empty(ws: Worksheet, row_number: int) -> bool:
    for col in range(1, 24):
        if _as_text_or_empty(ws.cell(row=row_number, column=col).value):
            return False
    return True


def parse_and_validate_asistencia_excel(file_bytes: bytes, filename: str) -> dict[str, Any]:
    wb = load_workbook(BytesIO(file_bytes), data_only=True)
    if "Asistencia" not in wb.sheetnames:
        raise ValidationError("El archivo no contiene la hoja obligatoria 'Asistencia'.")
    ws = wb["Asistencia"]
    _validate_header_structure(ws)
    header_meta = _read_header_meta(ws)

    blocking_errors: list[str] = []
    rows: list[dict[str, Any]] = []

    for row_number in range(5, ws.max_row + 1):
        if _row_is_empty(ws, row_number):
            continue

        nombre = _as_text_or_empty(ws.cell(row=row_number, column=1).value)
        row_data: dict[str, Any] = {
            "row_number": row_number,
            "nombre_empleado": nombre,
            "cliente": _as_text_or_empty(ws.cell(row=row_number, column=3).value),
            "planta": _as_text_or_empty(ws.cell(row=row_number, column=4).value),
            "puesto": _as_text_or_empty(ws.cell(row=row_number, column=5).value),
            "banco": _as_text_or_empty(ws.cell(row=row_number, column=6).value),
            "cuenta": _as_text_or_empty(ws.cell(row=row_number, column=7).value),
            "dia_1_header": header_meta.dias_headers[0],
            "dia_1_value": _as_text_or_empty(ws.cell(row=row_number, column=8).value),
            "dia_2_header": header_meta.dias_headers[1],
            "dia_2_value": _as_text_or_empty(ws.cell(row=row_number, column=9).value),
            "dia_3_header": header_meta.dias_headers[2],
            "dia_3_value": _as_text_or_empty(ws.cell(row=row_number, column=10).value),
            "dia_4_header": header_meta.dias_headers[3],
            "dia_4_value": _as_text_or_empty(ws.cell(row=row_number, column=11).value),
            "dia_5_header": header_meta.dias_headers[4],
            "dia_5_value": _as_text_or_empty(ws.cell(row=row_number, column=12).value),
            "dia_6_header": header_meta.dias_headers[5],
            "dia_6_value": _as_text_or_empty(ws.cell(row=row_number, column=13).value),
            "dia_7_header": header_meta.dias_headers[6],
            "dia_7_value": _as_text_or_empty(ws.cell(row=row_number, column=14).value),
            "he": _as_text_or_empty(ws.cell(row=row_number, column=15).value),
            "turnos_extra_normales": _as_text_or_empty(ws.cell(row=row_number, column=16).value),
            "dias_cubiertos_normales": _as_text_or_empty(ws.cell(row=row_number, column=17).value),
            "festivo_laborado": _as_text_or_empty(ws.cell(row=row_number, column=18).value),
            "vacaciones_laboradas": _as_text_or_empty(ws.cell(row=row_number, column=19).value),
            "prima_vacacional": _as_text_or_empty(ws.cell(row=row_number, column=20).value),
            "bono": _as_text_or_empty(ws.cell(row=row_number, column=21).value),
            "deducciones": _as_text_or_empty(ws.cell(row=row_number, column=22).value),
            "observaciones": _as_text_or_empty(ws.cell(row=row_number, column=23).value),
            "errors": [],
            "warnings": [],
        }

        non_name_values = [
            row_data["cliente"],
            row_data["planta"],
            row_data["puesto"],
            row_data["banco"],
            row_data["cuenta"],
            row_data["dia_1_value"],
            row_data["dia_2_value"],
            row_data["dia_3_value"],
            row_data["dia_4_value"],
            row_data["dia_5_value"],
            row_data["dia_6_value"],
            row_data["dia_7_value"],
            row_data["he"],
            row_data["turnos_extra_normales"],
            row_data["dias_cubiertos_normales"],
            row_data["festivo_laborado"],
            row_data["vacaciones_laboradas"],
            row_data["prima_vacacional"],
            row_data["bono"],
            row_data["deducciones"],
            row_data["observaciones"],
        ]
        if not nombre:
            if any(non_name_values):
                row_data["errors"].append("Fila con datos pero sin nombre de empleado.")
            else:
                continue

        daily_values = [
            row_data["dia_1_value"],
            row_data["dia_2_value"],
            row_data["dia_3_value"],
            row_data["dia_4_value"],
            row_data["dia_5_value"],
            row_data["dia_6_value"],
            row_data["dia_7_value"],
        ]
        for idx, raw_daily in enumerate(daily_values, start=1):
            code = _norm_header(raw_daily)
            if code and code not in VALID_DAILY_KEYS:
                msg = f"Clave diaria inválida en día {idx}: '{raw_daily}'."
                row_data["errors"].append(msg)
                blocking_errors.append(f"Fila {row_number}: {msg}")

        for field_key, col in NUMERIC_NON_NEGATIVE_COLUMNS.items():
            raw_value = ws.cell(row=row_number, column=col).value
            numeric = _to_float(raw_value)
            if numeric is not None and numeric < 0:
                msg = f"Valor negativo no permitido en '{field_key}'."
                row_data["errors"].append(msg)
                blocking_errors.append(f"Fila {row_number}: {msg}")

        prima = _norm_header(row_data["prima_vacacional"])
        if not prima:
            row_data["prima_vacacional"] = "N/A"
            prima = "N/A"
        if prima not in {"N/A", "SOLICITA"}:
            row_data["errors"].append("PRIMA VACACIONAL solo acepta N/A, SOLICITA o vacío.")
        elif prima == "SOLICITA":
            row_data["warnings"].append("PRIMA VACACIONAL en SOLICITA: revisar vacaciones en fase 2.")
            row_data["prima_vacacional"] = "SOLICITA"
        else:
            row_data["prima_vacacional"] = "N/A"

        any_ot = any(_norm_header(v) == "OT" for v in daily_values)
        if any_ot and not row_data["observaciones"]:
            row_data["warnings"].append("Tiene OT en asistencia y no incluye observaciones.")

        for key in ("bono", "deducciones"):
            value = row_data[key]
            if not value:
                continue
            numeric = _to_float(value)
            if numeric is None:
                row_data["warnings"].append(f"{key.upper()} contiene texto: revisar manualmente.")
            row_data["warnings"].append(f"{key.upper()} capturado: requiere revisión de Nómina.")

        rows.append(row_data)

    error_count = sum(len(row.get("errors") or []) for row in rows)
    warning_count = sum(len(row.get("warnings") or []) for row in rows)
    return {
        "filename": filename,
        "semana": header_meta.semana,
        "cliente": header_meta.cliente,
        "coordinador": header_meta.coordinador,
        "dias_headers": header_meta.dias_headers,
        "rows": rows,
        "total_rows": len(rows),
        "error_count": error_count,
        "warning_count": warning_count,
        "blocking_errors": blocking_errors,
    }

