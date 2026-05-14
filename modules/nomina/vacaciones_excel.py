from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet


@dataclass
class ParsedVacaciones:
    rows: list[dict[str, Any]]
    warnings: list[str]
    errors: list[str]
    cliente: str


def _norm_header(value: Any) -> str:
    s = " ".join(str(value or "").replace("\n", " ").split()).strip().upper()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return s


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
        return float(value)
    s = str(value).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_date_text(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
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
    return s


def _paid_flag(value: Any) -> bool:
    s = _norm_header(value)
    if not s:
        return False
    if s in {"1", "SI", "PAGADA", "TRUE", "X"}:
        return True
    n = _to_float(value)
    return bool(n and n >= 1)


def _detect_header_row(ws: Worksheet) -> int:
    for row in range(1, min(15, ws.max_row + 1)):
        values = [_norm_header(ws.cell(row=row, column=col).value) for col in range(1, ws.max_column + 1)]
        if "NOMBRE" in values and "FECHA DE INGRESO" in values:
            return row
    raise ValueError("No se encontró fila de encabezados para Vacaciones.")


def _column_map(ws: Worksheet, header_row: int) -> dict[str, int]:
    by_norm: dict[str, int] = {}
    for col in range(1, ws.max_column + 1):
        h = _norm_header(ws.cell(row=header_row, column=col).value)
        if h:
            by_norm[h] = col

    def col_for(*candidates: str) -> int | None:
        for c in candidates:
            n = _norm_header(c)
            if n in by_norm:
                return by_norm[n]
        for key, idx in by_norm.items():
            for c in candidates:
                if _norm_header(c) in key:
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
        "vacaciones_laboradas": col_for("VACACIONES LABORADAS"),
        "dias_pagados": col_for("DIAS PAGADOS"),
        "dias_restantes_historico": col_for("DIAS RESTANTES"),
        "comentarios": col_for("COMENTARIOS"),
        "monto_total_historico": col_for("MONTO TOTAL"),
        "nss": col_for("NSS"),
    }


def parse_vacaciones_historico_excel(file_bytes: bytes, filename: str) -> ParsedVacaciones:
    wb = load_workbook(BytesIO(file_bytes), data_only=True)
    if "Vacaciones" in wb.sheetnames:
        ws = wb["Vacaciones"]
    else:
        ws = wb[wb.sheetnames[0]]
    header_row = _detect_header_row(ws)
    cols = _column_map(ws, header_row)

    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []
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
            g("prima_2025_pagada"),
            g("prima_2026_pagada"),
            g("dias_utilizados"),
            g("vacaciones_laboradas"),
            g("dias_pagados"),
            g("dias_restantes_historico"),
            g("comentarios"),
            g("monto_total_historico"),
        ]
        if not nombre and all(v in (None, "") for v in raw_values):
            continue
        if not nombre and any(v not in (None, "") for v in raw_values):
            errors.append(f"Fila {row_num}: contiene datos pero no nombre.")
            continue

        dias_vac = _to_float(g("dias_vacaciones_historico")) or 0.0
        dias_utilizados = _to_float(g("dias_utilizados")) or 0.0
        vacaciones_laboradas = _to_float(g("vacaciones_laboradas")) or 0.0
        dias_pagados = _to_float(g("dias_pagados")) or 0.0
        dias_rest_hist = _to_float(g("dias_restantes_historico"))
        consumed = max(dias_pagados, dias_utilizados + vacaciones_laboradas)
        dias_rest_calc = dias_vac - consumed

        row_warnings: list[str] = []
        if dias_rest_hist is not None and abs(dias_rest_hist - dias_rest_calc) > 0.01:
            row_warnings.append("Días restantes histórico no coincide con cálculo.")
        prima_2026 = _paid_flag(g("prima_2026_pagada"))
        fecha_pago_2026 = _to_date_text(g("fecha_pago_prima_2026"))
        if prima_2026 and not fecha_pago_2026:
            row_warnings.append("Prima vacacional 2026 marcada como pagada sin fecha.")
        if _to_float(g("monto_total_historico")) is not None and _to_float(g("sueldo_historico")) is None:
            row_warnings.append("Monto total existe pero sueldo histórico está vacío.")
        if vacaciones_laboradas > 0:
            row_warnings.append("Vacaciones laboradas detectadas: se consideran en saldo.")

        row = {
            "source_row_number": row_num,
            "nss": str(g("nss") or "").strip(),
            "nombre_historico": nombre,
            "nombre_normalizado": normalize_name(nombre),
            "nombre_headcount": "",
            "cliente": cliente,
            "planta_historica": str(g("planta_historica") or "").strip(),
            "planta_headcount": "",
            "fecha_ingreso_historica": _to_date_text(g("fecha_ingreso_historica")),
            "fecha_ingreso_headcount": "",
            "fecha_ingreso_usada": _to_date_text(g("fecha_ingreso_historica")),
            "estatus_headcount": "PENDING",
            "sueldo_historico": _to_float(g("sueldo_historico")),
            "sueldo_headcount": None,
            "sueldo_usado": _to_float(g("sueldo_historico")),
            "dias_vacaciones_historico": dias_vac,
            "dias_utilizados": dias_utilizados,
            "vacaciones_laboradas": vacaciones_laboradas,
            "dias_pagados": dias_pagados,
            "dias_restantes_historico": dias_rest_hist,
            "dias_restantes_calculado": dias_rest_calc,
            "prima_2025_pagada": _paid_flag(g("prima_2025_pagada")),
            "semana_pago_prima_2025": str(g("semana_pago_prima_2025") or "").strip(),
            "prima_2026_pagada": prima_2026,
            "fecha_pago_prima_2026": fecha_pago_2026,
            "monto_total_historico": _to_float(g("monto_total_historico")),
            "monto_total_recalculado": None,
            "comentarios": str(g("comentarios") or "").strip(),
            "match_status": "pending_review",
            "match_score": 0.0,
            "warnings": row_warnings,
            "editable_json": {},
        }
        if row["sueldo_usado"] is not None:
            row["monto_total_recalculado"] = round((row["sueldo_usado"] or 0.0) * max(row["dias_pagados"], 0.0) * 0.25, 2)
        rows.append(row)
        warnings.extend([f"Fila {row_num}: {w}" for w in row_warnings])

    return ParsedVacaciones(rows=rows, warnings=warnings, errors=errors, cliente=cliente)

