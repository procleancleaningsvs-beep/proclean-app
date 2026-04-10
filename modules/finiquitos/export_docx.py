"""Arma el dict de placeholders del finiquito y exporta DOCX/PDF."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from modules.finiquitos.calc import format_importe
from modules.finiquitos.docx_placeholders import replace_placeholders_in_docx_bytes
from modules.finiquitos.finiquito_template_patch import patch_finiquito_docx_template_bytes
from modules.finiquitos.fecha_es import fecha_emision_larga
from modules.finiquitos.fecha_limite_pago import fecha_limite_pago_finiquito_larga
from modules.finiquitos.nombre_archivo_finiquito import normalizar_nombre_empleado_documento
from modules.finiquitos.numero_letra import importe_mxn_a_letra
from modules.vitroflex_docs.libreoffice_pdf import docx_bytes_to_pdf_bytes


def _as_positive_amount_str(s: str) -> str:
    """Convierte monto formateado a valor absoluto formateado (visual deducciones)."""
    raw = (s or "").replace(",", "").strip()
    if not raw:
        return ""
    return format_importe(abs(Decimal(raw)))


def _fila_deduccion_docx_visible(num: str, concepto: str, importe_fmt: str) -> bool:
    return bool((num or "").strip() or (concepto or "").strip() or (importe_fmt or "").strip())


def empaquetar_filas_deduccion_para_docx(
    pdf: dict[str, Any],
    *,
    nd: str,
    cd: str,
    t11d: str,
) -> dict[str, str]:
    """
    Reacomoda solo los placeholders de deducción (mismos slots del template n8–nd):
    conceptos con datos suben a las primeras filas de la columna sin dejar huecos entre activos.
    No modifica la tabla del DOCX, solo valores sustituidos.
    """
    orden: list[tuple[str, str, str]] = []
    if _fila_deduccion_docx_visible(pdf.get("n8", ""), pdf.get("c_isa", ""), pdf.get("t8", "")):
        orden.append((pdf["n8"], pdf["c_isa"], pdf["t8"]))
    if _fila_deduccion_docx_visible(pdf.get("n9", ""), pdf.get("c_i174", ""), pdf.get("t9", "")):
        orden.append((pdf["n9"], pdf["c_i174"], pdf["t9"]))
    if _fila_deduccion_docx_visible(pdf.get("n10", ""), pdf.get("c_imes", ""), pdf.get("t10", "")):
        orden.append((pdf["n10"], pdf["c_imes"], pdf["t10"]))
    sep_n = (pdf.get("n_sep") or "").strip()
    sep_c = (pdf.get("c_sep") or "").strip()
    sep_t = (pdf.get("t_sep") or "").strip()
    if _fila_deduccion_docx_visible(sep_n, sep_c, sep_t):
        orden.append((pdf.get("n_sep", ""), pdf.get("c_sep", ""), pdf.get("t_sep", "")))
    if _fila_deduccion_docx_visible(nd, cd, t11d):
        orden.append((nd, cd, t11d))

    slots: tuple[tuple[str, str, str], ...] = (
        ("n8", "c_isa", "t8"),
        ("n9", "c_i174", "t9"),
        ("n10", "c_imes", "t10"),
        ("nd", "cd", "t11d"),
    )
    out: dict[str, str] = {k: "" for trio in slots for k in trio}
    for i, trio in enumerate(slots):
        if i < len(orden):
            num, conc, imp = orden[i]
            nk, ck, tk = trio
            out[nk] = num
            out[ck] = conc
            out[tk] = imp
    return out


def build_finiquito_placeholders(
    *,
    lugar_emision: str,
    estado_emision: str,
    fecha_emision: date,
    fecha_baja: date,
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

    if ajuste > 0:
        np, cp, t11p = "99", "Ajuste al neto", format_importe(abs(ajuste))
        nd, cd, t11d = "", "", ""
    elif ajuste < 0:
        np, cp, t11p = "", "", ""
        nd, cd, t11d = "99", "Ajuste al neto", format_importe(abs(ajuste))
    else:
        np = cp = t11p = nd = cd = t11d = ""

    fecha_larga = fecha_emision_larga(fecha_emision)
    fecha_limite = fecha_limite_pago_finiquito_larga(fecha_emision)
    nombre_doc = normalizar_nombre_empleado_documento(empleado_nombre)
    t11_val = "" if ajuste == 0 else format_importe(ajuste)
    ded_docx = empaquetar_filas_deduccion_para_docx(pdf, nd=nd, cd=cd, t11d=t11d)
    return {
        "{lugar_emision}": lugar_emision or "",
        "{estado_emision}": estado_emision or "",
        "{fecha_emision_larga}": fecha_larga,
        "{fecha_letra}": fecha_larga,
        "{fecha_baja_larga}": fecha_emision_larga(fecha_baja),
        "{fecha_limite_pago}": fecha_limite,
        "{empleado_nombre_completo}": nombre_doc,
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
        # En formato final las deducciones se imprimen en positivo (valor absoluto).
        "{t8}": _as_positive_amount_str(pdf["t8"]),
        "{n9}": pdf["n9"],
        "{c_i174}": pdf["c_i174"],
        "{t9}": _as_positive_amount_str(pdf["t9"]),
        "{n10}": pdf["n10"],
        "{c_imes}": pdf["c_imes"],
        "{t10}": _as_positive_amount_str(pdf["t10"]),
        "{n_sep}": pdf.get("n_sep", ""),
        "{c_sep}": pdf.get("c_sep", ""),
        "{t_sep}": _as_positive_amount_str(pdf.get("t_sep", "")),
        "{t11}": t11_val,
        "{np}": np,
        "{cp}": cp,
        "{t11p}": t11p,
        "{nd}": ded_docx["nd"],
        "{cd}": ded_docx["cd"],
        "{t11d}": _as_positive_amount_str(ded_docx["t11d"]),
        "{suma_p}": format_importe(Decimal(str(tot["total_percepciones"]))),
        "{suma_d}": pdf["suma_d"],
    }


def render_finiquito_docx(template_path: Path, mapping: dict[str, str]) -> bytes:
    """
    Lee la plantilla del path dado, aplica parches de layout (misma lógica en app y CLI)
    y sustituye placeholders.
    """
    raw = template_path.read_bytes()
    patched = patch_finiquito_docx_template_bytes(raw)
    return replace_placeholders_in_docx_bytes(patched, mapping)


def render_finiquito_pdf(docx_bytes: bytes, *, pdf_stem: str | None = None) -> bytes:
    return docx_bytes_to_pdf_bytes(docx_bytes, pdf_stem=pdf_stem)


def render_finiquito_final(
    template_path: Path,
    mapping: dict[str, str],
    *,
    pdf_stem: str | None = None,
) -> tuple[bytes, bytes]:
    """
    Pipeline único: plantilla → parches → DOCX con placeholders → PDF (LibreOffice).
    Usar desde Flask y desde scripts de prueba.
    """
    docx_b = render_finiquito_docx(template_path, mapping)
    pdf_b = render_finiquito_pdf(docx_b, pdf_stem=pdf_stem)
    return docx_b, pdf_b


def finiquito_docx_template_bundle_path() -> Path:
    """Plantilla empaquetada en el repo (referencia para scripts sin Flask)."""
    return Path(__file__).resolve().parents[2] / "docx_templates" / "FINIQUITO FORMATO.docx"

