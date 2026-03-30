"""Arma el dict de placeholders del finiquito y exporta DOCX/PDF."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from modules.finiquitos.calc import format_importe
from modules.finiquitos.docx_placeholders import replace_placeholders_in_docx_bytes
from modules.finiquitos.fecha_es import fecha_emision_larga
from modules.finiquitos.numero_letra import importe_mxn_a_letra
from modules.vitroflex_docs.libreoffice_pdf import docx_bytes_to_pdf_bytes


def build_finiquito_placeholders(
    *,
    lugar_emision: str,
    estado_emision: str,
    fecha_emision: date,
    empleado_nombre: str,
    calc: dict[str, Any],
    incluir_prima_antig: bool,
) -> dict[str, str]:
    lab = calc["laboral"]
    tot = calc["totales"]
    pdf = calc["pdf_filas"]
    neto = Decimal(str(tot["neto_final"]))
    ajuste = Decimal(str(tot["ajuste_neto"]))

    pa = Decimal(str(lab.get("prima_antiguedad_monto") or 0))
    if incluir_prima_antig and pa > 0:
        n7, c_pant, t7 = "29", "Prima de antigüedad", format_importe(pa)
    else:
        n7, c_pant, t7 = "", "", ""

    return {
        "{lugar_emision}": lugar_emision or "",
        "{estado_emision}": estado_emision or "",
        "{fecha_emision_larga}": fecha_emision_larga(fecha_emision),
        "{empleado_nombre_completo}": empleado_nombre or "",
        "{neto_p}": format_importe(neto),
        "{neto_pagar_letra}": importe_mxn_a_letra(neto),
        "{t1}": format_importe(Decimal(str(lab["sueldo"]))),
        "{t2}": format_importe(Decimal(str(lab["septimo_dia"]))),
        "{t3}": format_importe(Decimal(str(lab["vacaciones_a_tiempo"]))),
        "{t5}": format_importe(Decimal(str(lab["prima_vacacional"]))),
        "{t6}": format_importe(Decimal(str(lab["aguinaldo"]))),
        "{n7}": n7,
        "{c_pant}": c_pant,
        "{t7}": t7,
        "{n8}": pdf["n8"],
        "{c_isa}": pdf["c_isa"],
        "{t8}": pdf["t8"],
        "{n9}": pdf["n9"],
        "{c_i174}": pdf["c_i174"],
        "{t9}": pdf["t9"],
        "{n10}": pdf["n10"],
        "{c_imes}": pdf["c_imes"],
        "{t10}": pdf["t10"],
        "{t11}": format_importe(ajuste),
        "{suma_p}": format_importe(Decimal(str(tot["total_percepciones"]))),
        "{suma_d}": pdf["suma_d"],
    }


def render_finiquito_docx(template_path: Path, mapping: dict[str, str]) -> bytes:
    raw = template_path.read_bytes()
    return replace_placeholders_in_docx_bytes(raw, mapping)


def render_finiquito_pdf(docx_bytes: bytes) -> bytes:
    return docx_bytes_to_pdf_bytes(docx_bytes, suffix="finiquito")
