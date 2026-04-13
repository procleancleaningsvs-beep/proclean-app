"""
Genera DOCX+PDF del ejemplo 1 usando el MISMO pipeline que la app (`render_finiquito_final`).

Uso (desde la raíz del repo):
  python scripts/render_finiquito_prueba_ejemplo1.py

Plantilla: `finiquito_docx_template_bundle_path()` (= repo docx_templates en local Windows;
  en producción la app usa DOCX_TEMPLATES_DIR, que tras ensure_default_templates queda
  alineado con patch(bundle).)

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


def _analyze_docx(label: str, data: bytes) -> dict[str, str | int | bool]:
    z = zipfile.ZipFile(BytesIO(data))
    h = z.read("word/header1.xml").decode("utf-8")
    d = z.read("word/document.xml").decode("utf-8")
    f = z.read("word/footer1.xml").decode("utf-8")
    src = re.search(r"srcRect[^>]+/>", h)
    tr_spacer = bool(re.search(r'w:trHeight w:val="2986"', d))
    tr_atleast_900 = bool(re.search(r'w:trHeight w:val="900"[^>]*w:hRule="atLeast"', d))
    at_after = re.search(r"<w:t>ATENTAMENTE</w:t>", d)
    block = ""
    if at_after:
        i = at_after.start()
        block = d[max(0, i - 200) : i + 20]
    cy_all = re.findall(r'cx="6872024" cy="(\d+)"', f)
    cy_name = cy_all[-1] if len(cy_all) > 1 else (cy_all[0] if cy_all else "")
    footer_m = re.search(r'w:footer="(\d+)"', d)
    return {
        "label": label,
        "header_srcRect_snippet": (src.group(0) if src else ""),
        "header_recorte_5393_presente": "5393" in h,
        "document_trHeight_2986": tr_spacer,
        "document_spacer_atLeast_900": tr_atleast_900,
        "atentamente_context": block.replace("\n", " ")[:280],
        "footer_textbox_cy_6872024_inner": cy_name,
        "pgMar_footer_twips": footer_m.group(1) if footer_m else "",
        "firma_guiones": "______________" in d,
        "neto_celda_ancha_6500": 'w:w="6500"' in d[max(0, d.find("09F3A52C") - 400) : d.find("09F3A52C") + 50],
        "placeholders_suma_pendientes": ("{suma_p}" in d) or ("{suma_d}" in d),
        "tablas_document": d.count("<w:tbl"),
    }


def main() -> None:
    from modules.finiquitos.calc import calcular_finiquito
    from modules.finiquitos.export_docx import (
        build_finiquito_placeholders,
        finiquito_docx_template_bundle_path,
        render_finiquito_final,
    )

    tpl = finiquito_docx_template_bundle_path()
    if not tpl.is_file():
        raise SystemExit(f"Falta plantilla: {tpl.resolve()}")
    st = tpl.stat()
    tpl_bytes = tpl.read_bytes()

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
    stem = "Finiquito Prueba Ejemplo1 Correcto Fiscal"
    docx_b, pdf_b = render_finiquito_final(tpl, mapping, pdf_stem=stem)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    docx_path = OUT_DIR / f"{stem}.docx"
    pdf_path = OUT_DIR / f"{stem}.pdf"
    docx_path.write_bytes(docx_b)
    pdf_path.write_bytes(pdf_b)

    ev_tpl = _analyze_docx("plantilla_disco_sin_placeholder", tpl_bytes)
    ev_out = _analyze_docx("generado_pipeline_unificado", docx_b)

    zg = zipfile.ZipFile(BytesIO(docx_b))
    dg = zg.read("word/document.xml").decode("utf-8")
    suma_p_val = mapping["{suma_p}"]
    suma_d_val = mapping["{suma_d}"]
    suma_en_documento = suma_p_val in dg
    wt_nodes = re.findall(r"<w:t[^>]*>([^<]*)</w:t>", dg)
    wt_mezcla_totales = any(
        "Suma de Percepciones" in t and suma_p_val in t for t in wt_nodes
    ) or any("Suma de Deducciones" in t and suma_d_val in t for t in wt_nodes if suma_d_val)

    lines = [
        "=== Pipeline ===",
        "render_finiquito_final(template_path, mapping, pdf_stem) — mismo que api_pdf",
        f"Template path: {tpl.resolve()}",
        f"Tamaño bytes (disco): {st.st_size}",
        "",
        "=== Evidencia XML plantilla en disco (raw, sin parche previo al análisis) ===",
        str(ev_tpl),
        "",
        "=== Evidencia XML generado (tras patch+placeholders en memoria) ===",
        str(ev_out),
        "",
        "=== Totales ===",
        f"suma_p en mapping: {suma_p_val!r}",
        f"presente en document.xml: {suma_en_documento}",
        f"placeholders pendientes: {ev_out['placeholders_suma_pendientes']}",
        f"w:t mezcla etiqueta+monto: {wt_mezcla_totales}",
        "",
        "=== Salida ===",
        str(docx_path),
        str(pdf_path),
        f"PDF bytes: {len(pdf_b)}",
    ]
    report = "\n".join(lines) + "\n"
    (OUT_DIR / "REPORTE_EVIDENCIA.txt").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
