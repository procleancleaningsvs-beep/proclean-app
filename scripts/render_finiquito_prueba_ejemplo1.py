"""
Parchea la plantilla, genera DOCX+PDF del ejemplo 1 (test_finiquito_calc) y escribe evidencia.

Uso (desde la raíz del repo):
  python scripts/render_finiquito_prueba_ejemplo1.py

Salida: tests/evidence_finiquito/ejemplo1_correcto_fiscal/
"""

from __future__ import annotations

import re
import zipfile
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "tests" / "evidence_finiquito" / "ejemplo1_correcto_fiscal"
TPL = ROOT / "docx_templates" / "FINIQUITO FORMATO.docx"


def _patch_template() -> None:
    import runpy

    runpy.run_path(str(ROOT / "scripts" / "patch_finiquito_footer_signature.py"), run_name="__main__")


def _analyze_docx(label: str, data: bytes) -> dict[str, str | int | bool]:
    z = zipfile.ZipFile(BytesIO(data))
    h = z.read("word/header1.xml").decode("utf-8")
    d = z.read("word/document.xml").decode("utf-8")
    f = z.read("word/footer1.xml").decode("utf-8")
    src = re.search(r"srcRect[^>]+/>", h)
    tr_spacer = bool(re.search(r'w:trHeight w:val="2986"', d))
    tr_atleast = bool(re.search(r'w:trHeight w:val="360"[^>]*w:hRule="atLeast"', d))
    at_after = re.search(
        r'<w:t>ATENTAMENTE</w:t>',
        d,
    )
    block = ""
    if at_after:
        i = at_after.start()
        block = d[max(0, i - 200) : i + 20]
    cy_all = re.findall(r'cx="6872024" cy="(\d+)"', f)
    # Primer match = contenedor ancho; el del textbox del nombre suele ir después.
    cy_name = cy_all[-1] if len(cy_all) > 1 else (cy_all[0] if cy_all else "")
    footer_m = re.search(r'w:footer="(\d+)"', d)
    return {
        "label": label,
        "header_srcRect_snippet": (src.group(0) if src else ""),
        "header_recorte_5393_presente": "5393" in h,
        "document_trHeight_2986": tr_spacer,
        "document_spacer_atLeast_360": tr_atleast,
        "atentamente_context": block.replace("\n", " ")[:280],
        "footer_textbox_cy_6872024_inner": cy_name,
        "pgMar_footer_twips": footer_m.group(1) if footer_m else "",
        "placeholders_suma_pendientes": ("{suma_p}" in d) or ("{suma_d}" in d),
        "tablas_document": d.count("<w:tbl"),
    }


def main() -> None:
    _patch_template()
    if not TPL.is_file():
        raise SystemExit(f"Falta plantilla: {TPL}")
    st = TPL.stat()
    tpl_bytes = TPL.read_bytes()

    from modules.finiquitos.calc import calcular_finiquito
    from modules.finiquitos.export_docx import (
        build_finiquito_placeholders,
        render_finiquito_docx,
        render_finiquito_pdf,
    )

    calc = calcular_finiquito(
        ingreso=date(2024, 10, 15),
        baja=date(2026, 3, 26),
        fecha_emision=date(2026, 3, 26),
        salario_diario=Decimal("315.04"),
        zona="general",
        periodicidad_isr="semanal_mensualizada",
        modo="correcto_fiscal",
        dias_sueldo_pendientes=Decimal("6"),
        septimos_pendientes=Decimal("1"),
        dias_aguinaldo_politica=Decimal("15"),
        prima_vacacional_pct=Decimal("25"),
        vacaciones_ya_usadas=Decimal("0"),
        aguinaldo_ya_pagado=Decimal("0"),
        prima_vac_ya_pagada=Decimal("0"),
        incluir_prima_antiguedad=False,
        motivo_baja="despido",
    )
    mapping = build_finiquito_placeholders(
        lugar_emision="Guadalajara",
        estado_emision="Jalisco",
        fecha_emision=date(2026, 3, 26),
        fecha_baja=date(2026, 3, 26),
        empleado_nombre="María del Rosario de los Ángeles Camarena",
        calc=calc,
        incluir_prima_antig=False,
    )
    docx_b = render_finiquito_docx(TPL, mapping)
    stem = "Finiquito Prueba Ejemplo1 Correcto Fiscal"
    pdf_b = render_finiquito_pdf(docx_b, pdf_stem=stem)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    docx_path = OUT_DIR / f"{stem}.docx"
    pdf_path = OUT_DIR / f"{stem}.pdf"
    docx_path.write_bytes(docx_b)
    pdf_path.write_bytes(pdf_b)

    ev_tpl = _analyze_docx("plantilla_parchada", tpl_bytes)
    ev_out = _analyze_docx("generado_placeholder", docx_b)

    # Totales: no deben quedar llaves; buscar monto de percepciones en XML
    zg = zipfile.ZipFile(BytesIO(docx_b))
    dg = zg.read("word/document.xml").decode("utf-8")
    suma_p_val = mapping["{suma_p}"]
    suma_d_val = mapping["{suma_d}"]
    suma_en_documento = suma_p_val in dg
    # Evitar "todo en un solo w:t": buscar nodos que mezclen etiqueta + monto
    wt_nodes = re.findall(r"<w:t[^>]*>([^<]*)</w:t>", dg)
    wt_mezcla_totales = any(
        "Suma de Percepciones" in t and suma_p_val in t for t in wt_nodes
    ) or any("Suma de Deducciones" in t and suma_d_val in t for t in wt_nodes if suma_d_val)

    lines = [
        "=== Confirmación plantilla en disco ===",
        f"Ruta: {TPL}",
        f"Tamaño bytes: {st.st_size}",
        f"mtime_ns: {st.st_mtime_ns}",
        "",
        "=== Evidencia XML plantilla (tras parche) ===",
        str(ev_tpl),
        "",
        "=== Evidencia XML generado (ejemplo 1) ===",
        str(ev_out),
        "",
        "=== Totales ===",
        f"suma_p sustituido en mapping: {suma_p_val!r}",
        f"Ese texto aparece en document.xml generado: {suma_en_documento}",
        f"Placeholders {{suma_p}}/{{suma_d}} aún en XML: {ev_out['placeholders_suma_pendientes']}",
        f"Cantidad <w:tbl> en document.xml generado: {ev_out['tablas_document']}",
        f"Algún w:t mezcla etiqueta+monto totales: {wt_mezcla_totales}",
        "",
        "=== Archivos generados ===",
        str(docx_path),
        str(pdf_path),
        f"PDF bytes: {len(pdf_b)}",
    ]
    report = "\n".join(lines) + "\n"
    (OUT_DIR / "REPORTE_EVIDENCIA.txt").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
