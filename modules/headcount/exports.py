from __future__ import annotations

from io import BytesIO
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from modules.headcount.matching import warning_label


def _write_sheet_headers(ws, headers: list[str], row: int = 1) -> None:
    for col, h in enumerate(headers, start=1):
        cell = ws.cell(row, col, h)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _autosize(ws, max_col: int) -> None:
    for col in range(1, max_col + 1):
        letter = get_column_letter(col)
        ws.column_dimensions[letter].width = min(42, max(12, len(str(ws.cell(1, col).value or "")) + 4))


def _freeze_and_filter(ws, last_col: int, last_row: int) -> None:
    ws.freeze_panes = "A2"
    if last_row > 1:
        ws.auto_filter.ref = f"A1:{get_column_letter(last_col)}{last_row}"


def build_auditoria_excel_bytes(payload: dict[str, Any]) -> bytes:
    resumen = payload.get("resumen") or {}
    detalle = payload.get("detalle") or []
    agrupado = payload.get("agrupado") or []
    hc_sin_sua = payload.get("headcount_sin_sua") or []

    wb = openpyxl.Workbook()

    ws_r = wb.active
    ws_r.title = "Resumen"
    rows_resumen = [
        ("Registro patronal SUA", resumen.get("registro_patronal_sua", "")),
        ("Razón social SUA", resumen.get("razon_social_sua", "")),
        ("Periodo SUA", resumen.get("periodo_proceso_sua", "")),
        ("Fecha proceso SUA", resumen.get("fecha_proceso_sua", "")),
        ("Fecha corte reportada", resumen.get("fecha_corte_sua", "")),
        ("Total cotizantes SUA", resumen.get("total_cotizantes", "")),
        ("Trabajadores extraídos", resumen.get("trabajadores_extraidos", "")),
        ("Headcount RAFAEL activo", resumen.get("headcount_rafael_activo", "")),
        ("Matches correctos", resumen.get("matches_correctos", "")),
        ("Sin match", resumen.get("sin_match", "")),
        ("Warnings", resumen.get("warnings_criticos", "")),
        ("Patrón filtro", resumen.get("patron_filtro", "RAFAEL")),
    ]
    for i, (k, v) in enumerate(rows_resumen, start=1):
        ws_r.cell(i, 1, k).font = Font(bold=True)
        ws_r.cell(i, 2, v)
    ws_r.column_dimensions["A"].width = 28
    ws_r.column_dimensions["B"].width = 48

    ws_d = wb.create_sheet("Detalle SUA vs Headcount")
    det_headers = [
        "NSS SUA",
        "CURP",
        "Nombre SUA",
        "Cliente HC",
        "Ubicación HC",
        "Status Operación",
        "Status IMSS",
        "Patrón HC",
        "Match por",
        "Match status",
        "Días SUA",
        "SDI SUA",
        "Movimiento SUA",
        "Warnings",
    ]
    _write_sheet_headers(ws_d, det_headers)
    for ri, row in enumerate(detalle, start=2):
        ws_d.cell(ri, 1, row.get("nss_sua_original", ""))
        ws_d.cell(ri, 2, row.get("curp", ""))
        ws_d.cell(ri, 3, row.get("nombre_sua_original", ""))
        ws_d.cell(ri, 4, row.get("cliente_headcount", ""))
        ws_d.cell(ri, 5, row.get("ubicacion_headcount", ""))
        ws_d.cell(ri, 6, row.get("status_operacion_headcount", ""))
        ws_d.cell(ri, 7, row.get("status_imss_headcount", ""))
        ws_d.cell(ri, 8, row.get("patron_headcount", ""))
        ws_d.cell(ri, 9, row.get("match_por", ""))
        ws_d.cell(ri, 10, row.get("match_status", ""))
        ws_d.cell(ri, 11, row.get("dias", ""))
        ws_d.cell(ri, 12, row.get("sdi", ""))
        ws_d.cell(ri, 13, row.get("movimiento_clave", ""))
        ws_d.cell(ri, 14, "; ".join(warning_label(w) for w in (row.get("warnings") or [])))
    _freeze_and_filter(ws_d, len(det_headers), max(len(detalle) + 1, 1))
    _autosize(ws_d, len(det_headers))

    ws_g = wb.create_sheet("Agrupado Cliente Ubicación")
    g_headers = ["Cliente", "Ubicación", "Total SUA", "Match", "Sin match", "Bajas", "Warnings"]
    _write_sheet_headers(ws_g, g_headers)
    for ri, g in enumerate(agrupado, start=2):
        ws_g.cell(ri, 1, g.get("cliente", ""))
        ws_g.cell(ri, 2, g.get("ubicacion", ""))
        ws_g.cell(ri, 3, g.get("total_sua", 0))
        ws_g.cell(ri, 4, g.get("match_hc", 0))
        ws_g.cell(ri, 5, g.get("sin_match", 0))
        ws_g.cell(ri, 6, g.get("bajas_hc", 0))
        ws_g.cell(ri, 7, g.get("warnings", 0))
    _freeze_and_filter(ws_g, len(g_headers), max(len(agrupado) + 1, 1))
    _autosize(ws_g, len(g_headers))

    ws_w = wb.create_sheet("Warnings")
    w_headers = ["Tipo", "NSS", "CURP", "Nombre", "Cliente", "Ubicación", "Descripción"]
    _write_sheet_headers(ws_w, w_headers)
    wr = 2
    for row in detalle:
        for code in row.get("warnings") or []:
            ws_w.cell(wr, 1, code)
            ws_w.cell(wr, 2, row.get("nss_sua_original", ""))
            ws_w.cell(wr, 3, row.get("curp", ""))
            ws_w.cell(wr, 4, row.get("nombre_sua_original", ""))
            ws_w.cell(wr, 5, row.get("cliente_headcount", ""))
            ws_w.cell(wr, 6, row.get("ubicacion_headcount", ""))
            ws_w.cell(wr, 7, warning_label(code))
            wr += 1
    for row in hc_sin_sua:
        for code in row.get("warnings") or []:
            ws_w.cell(wr, 1, code)
            ws_w.cell(wr, 2, row.get("nss", ""))
            ws_w.cell(wr, 3, row.get("curp", ""))
            ws_w.cell(wr, 4, row.get("nombre_completo", ""))
            ws_w.cell(wr, 5, row.get("cliente", ""))
            ws_w.cell(wr, 6, row.get("ubicacion", ""))
            ws_w.cell(wr, 7, warning_label(code))
            wr += 1
    _freeze_and_filter(ws_w, len(w_headers), max(wr - 1, 1))
    _autosize(ws_w, len(w_headers))

    ws_sm = wb.create_sheet("SUA sin match Headcount")
    _write_sheet_headers(ws_sm, det_headers)
    sm_rows = [r for r in detalle if r.get("match_status") == "SIN_MATCH"]
    for ri, row in enumerate(sm_rows, start=2):
        ws_sm.cell(ri, 1, row.get("nss_sua_original", ""))
        ws_sm.cell(ri, 2, row.get("curp", ""))
        ws_sm.cell(ri, 3, row.get("nombre_sua_original", ""))
        ws_sm.cell(ri, 4, row.get("cliente_headcount", ""))
        ws_sm.cell(ri, 5, row.get("ubicacion_headcount", ""))
        ws_sm.cell(ri, 6, row.get("status_operacion_headcount", ""))
        ws_sm.cell(ri, 7, row.get("status_imss_headcount", ""))
        ws_sm.cell(ri, 8, row.get("patron_headcount", ""))
        ws_sm.cell(ri, 9, row.get("match_por", ""))
        ws_sm.cell(ri, 10, row.get("match_status", ""))
        ws_sm.cell(ri, 11, row.get("dias", ""))
        ws_sm.cell(ri, 12, row.get("sdi", ""))
        ws_sm.cell(ri, 13, row.get("movimiento_clave", ""))
        ws_sm.cell(ri, 14, "; ".join(warning_label(w) for w in (row.get("warnings") or [])))
    _freeze_and_filter(ws_sm, len(det_headers), max(len(sm_rows) + 1, 1))

    ws_hc = wb.create_sheet("HC activo no en SUA")
    hc_headers = ["Nombre", "NSS", "CURP", "Cliente", "Ubicación", "Status Op", "Status IMSS"]
    _write_sheet_headers(ws_hc, hc_headers)
    for ri, row in enumerate(hc_sin_sua, start=2):
        ws_hc.cell(ri, 1, row.get("nombre_completo", ""))
        ws_hc.cell(ri, 2, row.get("nss", ""))
        ws_hc.cell(ri, 3, row.get("curp", ""))
        ws_hc.cell(ri, 4, row.get("cliente", ""))
        ws_hc.cell(ri, 5, row.get("ubicacion", ""))
        ws_hc.cell(ri, 6, row.get("status_operacion", ""))
        ws_hc.cell(ri, 7, row.get("status_imss", ""))
    _freeze_and_filter(ws_hc, len(hc_headers), max(len(hc_sin_sua) + 1, 1))

    ws_b = wb.create_sheet("Bajas HC en SUA")
    _write_sheet_headers(ws_b, det_headers)
    b_rows = [r for r in detalle if "HEADCOUNT_BAJA_APARECE_EN_SUA" in (r.get("warnings") or [])]
    for ri, row in enumerate(b_rows, start=2):
        ws_b.cell(ri, 1, row.get("nss_sua_original", ""))
        ws_b.cell(ri, 2, row.get("curp", ""))
        ws_b.cell(ri, 3, row.get("nombre_sua_original", ""))
        ws_b.cell(ri, 4, row.get("cliente_headcount", ""))
        ws_b.cell(ri, 5, row.get("ubicacion_headcount", ""))
        ws_b.cell(ri, 6, row.get("status_operacion_headcount", ""))
        ws_b.cell(ri, 7, row.get("status_imss_headcount", ""))
        ws_b.cell(ri, 8, row.get("patron_headcount", ""))
        ws_b.cell(ri, 9, row.get("match_por", ""))
        ws_b.cell(ri, 10, row.get("match_status", ""))
        ws_b.cell(ri, 11, row.get("dias", ""))
        ws_b.cell(ri, 12, row.get("sdi", ""))
        ws_b.cell(ri, 13, row.get("movimiento_clave", ""))
        ws_b.cell(ri, 14, "; ".join(warning_label(w) for w in (row.get("warnings") or [])))
    _freeze_and_filter(ws_b, len(det_headers), max(len(b_rows) + 1, 1))

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()
