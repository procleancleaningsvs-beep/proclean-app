"""
Construye docx_templates/FINIQUITO FORMATO.docx desde tmp_formato_docx/
con ajustes de layout, textos legales y fila extra de deducciones (ISR separación).

Ejecutar desde la raíz del repo:
  python scripts/patch_finiquito_docx_template.py
"""

from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "tmp_formato_docx"
OUT_DOCX = ROOT / "docx_templates" / "FINIQUITO FORMATO.docx"


def _patch_document_xml(s: str) -> str:
    s = s.replace(
        "con la empresa con fecha del jueves 26 de marzo de 2026, cantidad que resulta",
        "con la empresa con fecha {fecha_baja_larga}, cantidad que resulta",
    )

    needle = '</w:p><w:p w14:paraId="2796DA53"'
    insert = (
        '</w:p><w:p w14:paraId="A1B2C3D1" w14:textId="77777777" w:rsidR="00DC0111" '
        'w:rsidRDefault="00DC0111" w:rsidP="00932985">'
        '<w:pPr><w:pStyle w:val="Textoindependiente"/>'
        '<w:spacing w:before="120"/><w:ind w:left="513" w:right="873"/><w:jc w:val="both"/></w:pPr>'
        '<w:r><w:t xml:space="preserve">Las partes acuerdan que dicha cantidad será cubierta a más tardar el día '
        "{fecha_limite_pago}.</w:t></w:r></w:p>"
        '<w:p w14:paraId="2796DA53"'
    )
    if needle not in s:
        raise RuntimeError("No se encontró el ancla para insertar párrafo de fecha límite de pago.")
    s = s.replace(needle, insert, 1)

    # No reducir w:top / w:header: en LibreOffice suele recortarse el logo del encabezado al exportar PDF.
    # s = s.replace('w:top="1880"', 'w:top="1440"')
    # s = s.replace('w:header="912"', 'w:header="708"')
    s = s.replace('<w:trHeight w:val="3303"/>', '<w:trHeight w:val="2600"/>')

    s = s.replace(
        '<w:p w14:paraId="499C50B9" w14:textId="6E90A277" w:rsidR="00CE1084" w:rsidRDefault="00000000">'
        '<w:pPr><w:pStyle w:val="TableParagraph"/><w:tabs><w:tab w:val="left" w:pos="3638"/></w:tabs>',
        '<w:p w14:paraId="499C50B9" w14:textId="6E90A277" w:rsidR="00CE1084" w:rsidRDefault="00000000">'
        '<w:pPr><w:pStyle w:val="TableParagraph"/><w:tabs><w:tab w:val="left" w:pos="4786"/></w:tabs>',
    )
    s = s.replace(
        '<w:p w14:paraId="09F3A52C" w14:textId="73D7DE48" w:rsidR="00CE1084" w:rsidRDefault="00000000">'
        '<w:pPr><w:pStyle w:val="TableParagraph"/><w:tabs><w:tab w:val="left" w:pos="2611"/></w:tabs>',
        '<w:p w14:paraId="09F3A52C" w14:textId="73D7DE48" w:rsidR="00CE1084" w:rsidRDefault="00000000">'
        '<w:pPr><w:pStyle w:val="TableParagraph"/><w:tabs><w:tab w:val="left" w:pos="4786"/></w:tabs>',
    )

    row_needle = (
        '</w:tr><w:tr w:rsidR="003D30AC" w14:paraId="3068E77D" w14:textId="77777777" w:rsidTr="003E6E60">'
        '<w:trPr><w:trHeight w:val="250"/></w:trPr>'
    )
    extra_row = (
        '</w:tr><w:tr w:rsidR="003D30AC" w14:paraId="B9E1F001" w14:textId="77777777" w:rsidTr="003E6E60">'
        '<w:trPr><w:trHeight w:val="252"/></w:trPr>'
        '<w:tc><w:tcPr><w:tcW w:w="981" w:type="dxa"/><w:tcBorders><w:left w:val="single" w:sz="4" '
        'w:space="0" w:color="000000"/></w:tcBorders></w:tcPr>'
        '<w:p w14:paraId="C0FFEE01" w14:textId="77777777" w:rsidR="003D30AC" w:rsidRDefault="003D30AC">'
        '<w:pPr><w:pStyle w:val="TableParagraph"/><w:spacing w:before="0" w:line="199" w:lineRule="exact"/>'
        '<w:ind w:left="596"/><w:rPr><w:sz w:val="18"/></w:rPr></w:pPr></w:p></w:tc>'
        '<w:tc><w:tcPr><w:tcW w:w="3042" w:type="dxa"/></w:tcPr>'
        '<w:p w14:paraId="C0FFEE02" w14:textId="77777777" w:rsidR="003D30AC" w:rsidRDefault="003D30AC">'
        '<w:pPr><w:pStyle w:val="TableParagraph"/><w:spacing w:before="0" w:line="199" w:lineRule="exact"/>'
        '<w:ind w:left="88"/><w:rPr><w:sz w:val="18"/></w:rPr></w:pPr></w:p></w:tc>'
        '<w:tc><w:tcPr><w:tcW w:w="1765" w:type="dxa"/><w:tcBorders><w:right w:val="single" w:sz="4" '
        'w:space="0" w:color="000000"/></w:tcBorders></w:tcPr>'
        '<w:p w14:paraId="C0FFEE03" w14:textId="77777777" w:rsidR="003D30AC" w:rsidRDefault="003D30AC">'
        '<w:pPr><w:pStyle w:val="TableParagraph"/><w:spacing w:before="0" w:line="199" w:lineRule="exact"/>'
        '<w:ind w:right="21"/><w:jc w:val="right"/><w:rPr><w:sz w:val="18"/></w:rPr></w:pPr></w:p></w:tc>'
        '<w:tc><w:tcPr><w:tcW w:w="715" w:type="dxa"/><w:tcBorders><w:left w:val="single" w:sz="4" '
        'w:space="0" w:color="000000"/></w:tcBorders></w:tcPr>'
        '<w:p w14:paraId="C0FFEE04" w14:textId="77777777" w:rsidR="003D30AC" w:rsidRDefault="003E6E60">'
        '<w:pPr><w:pStyle w:val="TableParagraph"/><w:spacing w:before="0" w:line="199" w:lineRule="exact"/>'
        '<w:ind w:right="177"/><w:jc w:val="right"/><w:rPr><w:sz w:val="18"/></w:rPr></w:pPr>'
        '<w:r w:rsidRPr="003E6E60"><w:rPr><w:color w:val="808080"/><w:spacing w:val="-5"/><w:sz w:val="18"/>'
        '</w:rPr><w:t>{n_sep}</w:t></w:r></w:p></w:tc>'
        '<w:tc><w:tcPr><w:tcW w:w="2801" w:type="dxa"/></w:tcPr>'
        '<w:p w14:paraId="C0FFEE05" w14:textId="77777777" w:rsidR="003D30AC" w:rsidRDefault="00187D6E">'
        '<w:pPr><w:pStyle w:val="TableParagraph"/><w:spacing w:before="0" w:line="199" w:lineRule="exact"/>'
        '<w:ind w:left="89"/><w:rPr><w:sz w:val="18"/></w:rPr></w:pPr>'
        '<w:r w:rsidRPr="00187D6E"><w:rPr><w:color w:val="808080"/><w:sz w:val="18"/></w:rPr>'
        '<w:t>{c_sep}</w:t></w:r></w:p></w:tc>'
        '<w:tc><w:tcPr><w:tcW w:w="1344" w:type="dxa"/><w:tcBorders><w:right w:val="single" w:sz="4" '
        'w:space="0" w:color="000000"/></w:tcBorders></w:tcPr>'
        '<w:p w14:paraId="C0FFEE06" w14:textId="77777777" w:rsidR="003D30AC" w:rsidRDefault="003D30AC">'
        '<w:pPr><w:pStyle w:val="TableParagraph"/><w:spacing w:before="0" w:line="199" w:lineRule="exact"/>'
        '<w:ind w:right="26"/><w:jc w:val="right"/><w:rPr><w:sz w:val="18"/></w:rPr></w:pPr>'
        '<w:r w:rsidRPr="003D30AC"><w:rPr><w:color w:val="808080"/><w:spacing w:val="-2"/><w:sz w:val="18"/>'
        '</w:rPr><w:t>{t_sep}</w:t></w:r></w:p></w:tc></w:tr>'
        '<w:tr w:rsidR="003D30AC" w14:paraId="3068E77D" w14:textId="77777777" w:rsidTr="003E6E60">'
        '<w:trPr><w:trHeight w:val="250"/></w:trPr>'
    )
    if row_needle not in s:
        raise RuntimeError("No se encontró el ancla para insertar fila ISR separación.")
    s = s.replace(row_needle, extra_row, 1)

    sig_needle = (
        '<w:tc><w:tcPr><w:tcW w:w="3042" w:type="dxa"/><w:tcBorders><w:top w:val="single" w:sz="4" '
        'w:space="0" w:color="FFFFFF"/><w:bottom w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
        '</w:tcBorders></w:tcPr><w:p w14:paraId="11B7B088" w14:textId="3AEE4489" w:rsidR="003D30AC" '
        'w:rsidRDefault="003D30AC"><w:pPr><w:pStyle w:val="TableParagraph"/><w:ind w:left="87"/>'
        '<w:rPr><w:sz w:val="18"/></w:rPr></w:pPr></w:p></w:tc>'
    )
    sig_replace = (
        '<w:tc><w:tcPr><w:tcW w:w="3042" w:type="dxa"/><w:tcBorders><w:top w:val="single" w:sz="4" '
        'w:space="0" w:color="FFFFFF"/><w:bottom w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
        '</w:tcBorders></w:tcPr>'
        '<w:p w14:paraId="11B7B088" w14:textId="3AEE4489" w:rsidR="003D30AC" w:rsidRDefault="003D30AC">'
        '<w:pPr><w:pStyle w:val="TableParagraph"/><w:spacing w:before="240"/><w:ind w:left="0"/>'
        '<w:jc w:val="center"/><w:rPr><w:sz w:val="18"/></w:rPr></w:pPr>'
        '<w:r><w:rPr><w:sz w:val="18"/></w:rPr>'
        '<w:t xml:space="preserve">________________________________________</w:t></w:r></w:p>'
        '<w:p w14:paraId="11B7B089" w14:textId="3AEE4490" w:rsidR="003D30AC" w:rsidRDefault="003D30AC">'
        '<w:pPr><w:pStyle w:val="TableParagraph"/><w:spacing w:before="200" w:after="0" w:line="276" '
        'w:lineRule="atLeast"/><w:ind w:left="0"/><w:jc w:val="center"/>'
        '<w:rPr><w:sz w:val="17"/></w:rPr></w:pPr>'
        '<w:r><w:rPr><w:sz w:val="17"/></w:rPr><w:t>{empleado_nombre_completo}</w:t></w:r></w:p></w:tc>'
    )
    if sig_needle not in s:
        raise RuntimeError("No se encontró el bloque de celda central para firma.")
    s = s.replace(sig_needle, sig_replace, 1)

    return s


def main() -> None:
    if not SRC_DIR.is_dir():
        raise SystemExit(f"No existe {SRC_DIR}; descomprime la plantilla DOCX ahí como tmp_formato_docx/")
    doc_path = SRC_DIR / "word" / "document.xml"
    patched = _patch_document_xml(doc_path.read_text(encoding="utf-8"))

    OUT_DOCX.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_DOCX.with_suffix(".docx.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for p in SRC_DIR.rglob("*"):
            if p.is_dir():
                continue
            rel = p.relative_to(SRC_DIR).as_posix()
            data = p.read_bytes()
            if rel == "word/document.xml":
                data = patched.encode("utf-8")
            zout.writestr(rel, data)
    tmp.replace(OUT_DOCX)
    print(f"OK: {OUT_DOCX} ({OUT_DOCX.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
