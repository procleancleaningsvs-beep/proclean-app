from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Any

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from modules.nomina.vacaciones_util import PENDIENTE_REVISION

SUMMARY_HEADER_KEYS = {
    "DIAS DE VACACIONES",
    "PRIMA VACACIONAL 2025",
    "SEMANA PAGO PRIMA",
    "PRIMA VACACIONAL 2026",
    "FECHA DE PAGO",
    "DIAS UTILIZADOS",
    "VACACIONES LABORADAS",
    "DIAS PAGADOS",
    "DIAS RESTANTES",
    "COMENTARIOS",
    "MONTO TOTAL",
    "NSS",
}
YEAR_HEADER_RE = re.compile(r"^\d{4}$")
WEEKLY_HEADER_RE = re.compile(r"SEM|\d+\s*AL\s*\d+", re.IGNORECASE)


@dataclass
class ParsedVacaciones:
    rows: list[dict[str, Any]]
    warnings: list[str]
    errors: list[str]
    cliente: str
    weekly_events_total: int = 0


def _norm_header(value: Any) -> str:
    s = " ".join(str(value or "").replace("\n", " ").split()).strip().upper()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return s.rstrip("?")


def normalize_name(value: str) -> str:
    s = " ".join(str(value or "").replace("\u00a0", " ").split()).strip().upper()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return " ".join(s.split()).strip()


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value):  # NaN
            return None
        return float(value)
    s = str(value).strip().replace(",", "")
    if not s or s.lower() in {"nan", "none", "null"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _excel_serial_to_date(serial: float) -> date | None:
    if serial <= 0:
        return None
    try:
        return (datetime(1899, 12, 30) + timedelta(days=float(serial))).date()
    except (OverflowError, ValueError):
        return None


def _to_date_iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        parsed = _excel_serial_to_date(float(value))
        return parsed.isoformat() if parsed else str(value).strip()
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", "null"}:
        return ""
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", s)
    if m:
        d = int(m.group(1))
        mm = int(m.group(2))
        yy = int(m.group(3))
        if yy < 100:
            yy += 2000
        return f"{yy:04d}-{mm:02d}-{d:02d}"
    m2 = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m2:
        return f"{int(m2.group(1)):04d}-{int(m2.group(2)):02d}-{int(m2.group(3)):02d}"
    as_num = _to_float(s)
    if as_num is not None and as_num > 1000:
        parsed = _excel_serial_to_date(as_num)
        if parsed:
            return parsed.isoformat()
    return s


def _truthy(value: Any) -> bool:
    s = _norm_header(value)
    if not s:
        return False
    if s in {"1", "SI", "SÍ", "PAGADA", "TRUE", "X", "YES"}:
        return True
    n = _to_float(value)
    return bool(n and n >= 1)


def _to_days_value(value: Any) -> float:
    n = _to_float(value)
    if n is not None:
        return max(0.0, n)
    if _truthy(value):
        return 1.0
    return 0.0


def _paid_flag(value: Any) -> bool:
    return _truthy(value)


def _select_sheet(wb) -> Worksheet:
    preferred = []
    for name in wb.sheetnames:
        norm = _norm_header(name)
        if norm.startswith("VACACIONES"):
            preferred.append(name)
    if preferred:
        for name in preferred:
            if "2" in name:
                return wb[name]
        return wb[preferred[0]]
    return wb[wb.sheetnames[0]]


def _detect_header_row(ws: Worksheet) -> int:
    for row in range(1, min(15, ws.max_row + 1)):
        values = [_norm_header(ws.cell(row=row, column=col).value) for col in range(1, ws.max_column + 1)]
        if "NOMBRE" in values and "FECHA DE INGRESO" in values:
            return row
    raise ValueError("No se encontró fila de encabezados para Vacaciones.")


def _column_map(ws: Worksheet, header_row: int) -> dict[str, int]:
    by_norm: dict[str, int] = {}
    raw_by_col: dict[int, str] = {}
    for col in range(1, ws.max_column + 1):
        raw = ws.cell(row=header_row, column=col).value
        h = _norm_header(raw)
        if h:
            by_norm[h] = col
            raw_by_col[col] = str(raw or "").strip()

    def col_for(*candidates: str) -> int | None:
        for c in candidates:
            n = _norm_header(c)
            if n in by_norm:
                return by_norm[n]
        for key, idx in by_norm.items():
            for c in candidates:
                cand = _norm_header(c)
                if cand == key or cand in key or key in cand:
                    return idx
        return None

    return {
        "fecha_ingreso_historica": col_for("FECHA DE INGRESO"),
        "nombre_historico": col_for("NOMBRE", "NOMBRE COMPLETO"),
        "sueldo_historico": col_for("SUELDO"),
        "planta_historica": col_for("PLANTA"),
        "dias_vacaciones_historico": col_for("DIAS DE VACACIONES"),
        "prima_2025_pagada": col_for("PRIMA VACACIONAL 2025"),
        "semana_pago_prima_2025": col_for("SEMANA PAGO PRIMA"),
        "prima_2026_pagada": col_for("PRIMA VACACIONAL 2026"),
        "fecha_pago_prima_2026": col_for("FECHA DE PAGO"),
        "dias_utilizados": col_for("DIAS UTILIZADOS"),
        "vacaciones_laboradas": col_for("VACACIONES LABORADAS", "VACACIONES LABORADAS?"),
        "dias_pagados": col_for("DIAS PAGADOS", "DIAS PAGADOS "),
        "dias_restantes_historico": col_for("DIAS RESTANTES"),
        "comentarios": col_for("COMENTARIOS"),
        "monto_total_historico": col_for("MONTO TOTAL"),
        "nss": col_for("NSS"),
        "_raw_by_col": raw_by_col,
        "_by_norm": by_norm,
    }


def _is_summary_column(header_norm: str) -> bool:
    if header_norm in SUMMARY_HEADER_KEYS:
        return True
    for key in SUMMARY_HEADER_KEYS:
        if key in header_norm or header_norm in key:
            return True
    return False


def _detect_weekly_columns(ws: Worksheet, header_row: int, cols: dict[str, Any]) -> list[dict[str, Any]]:
    planta_col = cols.get("planta_historica")
    summary_cols = [
        cols.get("dias_vacaciones_historico"),
        cols.get("dias_utilizados"),
    ]
    summary_cols = [c for c in summary_cols if c is not None]
    first_summary = min(summary_cols) if summary_cols else ws.max_column + 1
    start_col = (planta_col or 5) + 1
    raw_by_col: dict[int, str] = cols.get("_raw_by_col") or {}
    weekly: list[dict[str, Any]] = []
    for col in range(start_col, first_summary):
        raw_header = raw_by_col.get(col) or str(ws.cell(row=header_row, column=col).value or "").strip()
        header_norm = _norm_header(raw_header)
        if not header_norm:
            continue
        if _is_summary_column(header_norm):
            continue
        if YEAR_HEADER_RE.match(header_norm):
            weekly.append({"column": col, "header_raw": raw_header, "header_norm": header_norm, "is_year_marker": True})
            continue
        if WEEKLY_HEADER_RE.search(raw_header) or WEEKLY_HEADER_RE.search(header_norm):
            weekly.append({"column": col, "header_raw": raw_header, "header_norm": header_norm, "is_year_marker": False})
    return weekly


def _parse_desglose_semanal(
    ws: Worksheet,
    row_num: int,
    weekly_columns: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], float]:
    desglose: list[dict[str, Any]] = []
    total = 0.0
    current_year = ""
    for col_def in weekly_columns:
        if col_def.get("is_year_marker"):
            current_year = col_def.get("header_norm") or ""
            continue
        val = _to_float(ws.cell(row=row_num, column=col_def["column"]).value)
        if val is None or val <= 0:
            continue
        days = float(val)
        total += days
        desglose.append(
            {
                "anio": current_year,
                "semana": col_def.get("header_raw") or "",
                "period_label": col_def.get("header_raw") or "",
                "days": days,
                "source": "excel_historico_carrier",
                "excel_row": row_num,
            }
        )
    return desglose, round(total, 4)


def parse_vacaciones_historico_excel(file_bytes: bytes, filename: str) -> ParsedVacaciones:
    wb = load_workbook(BytesIO(file_bytes), data_only=True)
    ws = _select_sheet(wb)
    header_row = _detect_header_row(ws)
    cols = _column_map(ws, header_row)
    weekly_columns = _detect_weekly_columns(ws, header_row, cols)

    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []
    weekly_events_total = 0
    cliente = "Carrier"

    for row_num in range(header_row + 1, ws.max_row + 1):
        def g(key: str) -> Any:
            col = cols.get(key)
            if col is None:
                return None
            return ws.cell(row=row_num, column=col).value

        nombre = str(g("nombre_historico") or "").strip()
        raw_values = [
            g("fecha_ingreso_historica"),
            g("sueldo_historico"),
            g("planta_historica"),
            g("dias_vacaciones_historico"),
            g("dias_utilizados"),
            g("comentarios"),
        ]
        if not nombre and all(v in (None, "") for v in raw_values):
            continue
        if not nombre and any(v not in (None, "") for v in raw_values):
            errors.append(f"Fila {row_num}: contiene datos pero no nombre.")
            continue

        fecha_ingreso_iso = _to_date_iso(g("fecha_ingreso_historica"))
        desglose_semanal, dias_utilizados_semanal = _parse_desglose_semanal(ws, row_num, weekly_columns)
        weekly_events_total += len(desglose_semanal)

        dias_vac_excel = _to_float(g("dias_vacaciones_historico")) or 0.0
        dias_utilizados_excel = _to_days_value(g("dias_utilizados"))
        vacaciones_laboradas = _to_days_value(g("vacaciones_laboradas"))
        dias_pagados_raw = g("dias_pagados")
        dias_pagados_excel = _to_days_value(dias_pagados_raw) if dias_pagados_raw not in (None, "") else 0.0
        dias_rest_excel = _to_float(g("dias_restantes_historico"))
        comentarios = str(g("comentarios") or "").strip()

        # Preferir desglose semanal para consumo; Excel resumen solo como control
        dias_utilizados_usados = dias_utilizados_semanal if desglose_semanal else dias_utilizados_excel
        consumed = max(dias_pagados_excel, dias_utilizados_usados + vacaciones_laboradas)
        dias_rest_calc = dias_vac_excel - consumed

        row_warnings: list[str] = []
        if desglose_semanal and dias_utilizados_excel > 0 and abs(dias_utilizados_semanal - dias_utilizados_excel) > 0.01:
            row_warnings.append(
                f"La suma semanal ({dias_utilizados_semanal}) no coincide con DIAS UTILIZADOS del Excel ({dias_utilizados_excel})."
            )
        elif not desglose_semanal and dias_utilizados_excel > 0:
            row_warnings.append("DIAS UTILIZADOS en Excel sin desglose semanal equivalente.")
        if dias_rest_calc < 0:
            row_warnings.append("Saldo calculado negativo; revisar días pagados/utilizados/laborados.")
        if dias_rest_excel is not None and abs(dias_rest_excel - dias_rest_calc) > 0.01:
            row_warnings.append("Días restantes histórico no coincide con cálculo preliminar del Excel.")
        prima_2026 = _paid_flag(g("prima_2026_pagada"))
        fecha_pago_2026 = _to_date_iso(g("fecha_pago_prima_2026"))
        if prima_2026 and not fecha_pago_2026:
            row_warnings.append("Prima vacacional 2026 marcada como pagada sin fecha.")
        prima_2025 = _paid_flag(g("prima_2025_pagada"))
        semana_2025 = str(g("semana_pago_prima_2025") or "").strip()
        if prima_2025 and not semana_2025:
            row_warnings.append("Prima vacacional 2025 marcada como pagada sin semana de pago.")
        if vacaciones_laboradas > 0:
            row_warnings.append("Vacaciones laboradas detectadas: se consideran en saldo.")
        if re.search(r"reingreso", comentarios, re.IGNORECASE):
            row_warnings.append("Comentario de reingreso requiere revisión.")

        excel_resumen = {
            "dias_vacaciones_excel": dias_vac_excel,
            "dias_utilizados_excel_resumen": dias_utilizados_excel,
            "dias_utilizados_calculado_semanal": dias_utilizados_semanal,
            "vacaciones_laboradas_excel": vacaciones_laboradas,
            "dias_pagados_excel": dias_pagados_excel,
            "dias_restantes_excel": dias_rest_excel,
            "prima_vacacional_2025_flag": prima_2025,
            "semana_pago_prima_2025": semana_2025,
            "prima_vacacional_2026_flag": prima_2026,
            "fecha_pago_prima_2026": fecha_pago_2026,
            "monto_total_excel": _to_float(g("monto_total_historico")),
            "comentarios_excel": comentarios,
        }

        row = {
            "source_row_number": row_num,
            "nss": str(g("nss") or "").strip(),
            "excel_nombre_original": nombre,
            "nombre_historico": nombre,
            "nombre_normalizado": normalize_name(nombre),
            "headcount_nombre_original": "",
            "headcount_nombre_normalizado": "",
            "nombre_headcount": "",
            "cliente": cliente,
            "planta_historica": str(g("planta_historica") or "").strip(),
            "planta_headcount": "",
            "fecha_ingreso_historica": fecha_ingreso_iso,
            "fecha_ingreso_headcount": "",
            "fecha_ingreso_usada": fecha_ingreso_iso,
            "status_headcount": "SIN STATUS HEADCOUNT",
            "estatus_headcount": "SIN STATUS HEADCOUNT",
            "sueldo_historico": _to_float(g("sueldo_historico")),
            "sueldo_headcount": None,
            "sueldo_usado": _to_float(g("sueldo_historico")),
            "dias_vacaciones_historico": dias_vac_excel,
            "dias_utilizados": dias_utilizados_usados,
            "dias_utilizados_excel_resumen": dias_utilizados_excel,
            "dias_utilizados_calculado_semanal": dias_utilizados_semanal,
            "vacaciones_laboradas": vacaciones_laboradas,
            "dias_pagados": dias_pagados_excel,
            "dias_restantes_historico": dias_rest_excel,
            "dias_restantes_calculado": dias_rest_calc,
            "prima_2025_pagada": prima_2025,
            "semana_pago_prima_2025": semana_2025,
            "prima_2026_pagada": prima_2026,
            "fecha_pago_prima_2026": fecha_pago_2026,
            "monto_total_historico": _to_float(g("monto_total_historico")),
            "monto_total_recalculado": None,
            "comentarios": comentarios,
            "match_status": PENDIENTE_REVISION,
            "match_method": "",
            "match_notes": "",
            "match_score": None,
            "desglose_semanal": desglose_semanal,
            "warnings": row_warnings,
            "editable_json": {
                "revision_status": "pending_revision",
                "desglose_semanal": desglose_semanal,
                "excel_resumen": excel_resumen,
            },
            "is_active": 1,
        }
        if row["sueldo_usado"] is not None:
            row["monto_total_recalculado"] = round((row["sueldo_usado"] or 0.0) * max(row["dias_pagados"], 0.0) * 0.25, 2)
        rows.append(row)
        warnings.extend([f"Fila {row_num}: {w}" for w in row_warnings])

    return ParsedVacaciones(
        rows=rows,
        warnings=warnings,
        errors=errors,
        cliente=cliente,
        weekly_events_total=weekly_events_total,
    )
