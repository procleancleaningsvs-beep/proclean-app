from __future__ import annotations

import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from modules.gestion_idse_sua.nominas import repository as repo
from modules.gestion_idse_sua.nominas.comparative_service import summarize_results
from modules.gestion_idse_sua.nominas.text_utils import normalize_upper
from modules.gestion_idse_sua.template_contract import HEADER_ROW_BY_SHEET, comparativo_path
from modules.gestion_idse_sua.template_validator import validate_comparativo_template


def _safe_filename(cliente: str, periodo_inicio: str, periodo_fin: str) -> str:
    def clean(value: str) -> str:
        return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)

    return f"comparativo_{clean(cliente)}_{clean(periodo_inicio)}_{clean(periodo_fin)}.xlsx"


def generate_comparative_excel(
    conn: sqlite3.Connection,
    comparative_id: int,
    *,
    username: str | None = None,
    selected_result_ids: list[int] | None = None,
) -> tuple[Path, str]:
    comp = repo.get_comparative(conn, comparative_id)
    if comp is None:
        raise ValueError("Comparativo no encontrado.")

    period = conn.execute(
        """
        SELECT p.*, s.sheet_name, i.original_filename
        FROM gis_nomina_periods p
        JOIN gis_nomina_sheets s ON s.id = p.sheet_id
        JOIN gis_nomina_imports i ON i.id = s.import_id
        WHERE p.id = ?
        """,
        (comp["period_id"],),
    ).fetchone()
    if period is None:
        raise ValueError("Periodo no encontrado.")

    results = repo.list_results(conn, comparative_id)
    totals = summarize_results(results)
    plantas = sorted(
        {
            normalize_upper(r.get("planta_normalizada") or "")
            for r in results
            if r.get("planta_normalizada")
        }
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix="gis_cmp_"))
    out_path = tmp_dir / _safe_filename(
        str(comp["cliente"]),
        str(period["fecha_inicio"]),
        str(period["fecha_fin"]),
    )
    shutil.copy2(comparativo_path(), out_path)

    wb = load_workbook(out_path)
    ws_res = wb["Resumen"]
    ws_res["B6"] = comp["cliente"]
    ws_res["D6"] = ", ".join(plantas)
    ws_res["F6"] = period["original_filename"]
    ws_res["B7"] = period["fecha_inicio"]
    ws_res["D7"] = period["fecha_fin"]
    ws_res["F7"] = period["semana_num"]
    ws_res["B8"] = period["sheet_name"]
    ws_res["D8"] = "Sí"
    ws_res["F8"] = comp["generated_at"]
    ws_res["B9"] = username or comp["generated_by"] or ""
    ws_res["D9"] = comp["status"]
    ws_res["F9"] = "GIS 1.0"

    ws_det = wb["Detalle Comparativo"]
    start_row = HEADER_ROW_BY_SHEET["Detalle Comparativo"] + 1
    for idx, row in enumerate(results):
        r = start_row + idx
        ws_det.cell(r, 1, row.get("id"))
        ws_det.cell(r, 2, comp["cliente"])
        ws_det.cell(r, 3, row.get("planta_normalizada") or "")
        ws_det.cell(r, 4, period["fecha_inicio"])
        ws_det.cell(r, 5, period["fecha_fin"])
        ws_det.cell(r, 6, period["semana_num"])
        ws_det.cell(r, 7, row.get("num_empleado") or "")
        ws_det.cell(r, 8, row.get("nombre_normalizado") or row.get("hc_nombre") or "")
        ws_det.cell(r, 9, row.get("match_hc_nombre") or row.get("hc_nombre") or "")
        ws_det.cell(r, 10, row.get("match_method") or "")
        ws_det.cell(r, 11, row.get("match_status") or "")
        ws_det.cell(r, 12, row.get("confidence") or "")
        ws_det.cell(r, 13, row.get("puesto") or "")
        ws_det.cell(r, 14, row.get("nss") or "")
        ws_det.cell(r, 15, row.get("rfc") or "")
        ws_det.cell(r, 16, row.get("curp") or "")
        ws_det.cell(r, 17, "")
        ws_det.cell(r, 18, row.get("resultado") or "")
        ws_det.cell(r, 19, row.get("semaforo") or "")
        ws_det.cell(r, 20, row.get("fecha_sugerida") or "")
        ws_det.cell(r, 21, row.get("decision_final") or row.get("tipo_sugerido") or "")
        ws_det.cell(r, 22, "")
        ws_det.cell(r, 23, row.get("observaciones") or "")

    ws_asist = wb["Asistencia Semanal"]
    ws_asist["A3"] = (
        "Advertencia: la asistencia semanal no fue interpretada en esta fase; "
        "se conservaron datos de fila para procesamiento posterior."
    )

    ws_mov = wb["Movimientos Seleccionados"]
    mov_start = HEADER_ROW_BY_SHEET["Movimientos Seleccionados"] + 1
    selected = selected_result_ids or [
        int(r["id"])
        for r in results
        if str(r.get("conversion_status") or "") in {"pending", "converted"}
        and str(r.get("tipo_sugerido") or "") in {"ALTA", "BAJA"}
    ]
    mov_idx = 0
    for row in results:
        if int(row["id"]) not in selected:
            continue
        tipo = str(row.get("tipo_sugerido") or row.get("decision_final") or "").upper()
        if tipo not in {"ALTA", "BAJA"}:
            continue
        r = mov_start + mov_idx
        mov_idx += 1
        ws_mov.cell(r, 1, "Sí")
        ws_mov.cell(r, 2, tipo)
        ws_mov.cell(r, 5, row.get("fecha_sugerida") or "")
        ws_mov.cell(r, 6, row.get("nss") or "")
        ws_mov.cell(r, 7, row.get("rfc") or "")
        ws_mov.cell(r, 8, row.get("curp") or "")
        ws_mov.cell(r, 13, comp["cliente"])
        ws_mov.cell(r, 14, row.get("planta_normalizada") or "")
        ws_mov.cell(r, 15, "GIS nominas")
        ws_mov.cell(r, 16, row.get("observaciones") or "")

    wb.save(out_path)
    wb.close()
    validate_comparativo_template(out_path)
    return out_path, out_path.name
