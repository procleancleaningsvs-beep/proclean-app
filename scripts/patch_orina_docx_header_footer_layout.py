"""
Ajuste controlado del DOCX de orina (solo encabezado / pies visuales):
- Orden del branding: Laboratorio + slogan + web/tel (como PDF de referencia).
- Más aire vertical entre branding → datos paciente → título.
- Separación entre tabla de resultados y bloque inferior (firma + franja).

No modifica la tabla central de 3 columnas ni el texto de parámetros.
"""
from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCX = ROOT / "examenes_medicos_templates" / "FORMATO EXAMEN DE ORINA.docx"
ENTRY = "word/document.xml"

BRANDING_BLOCK_OLD = (
    '<w:p><w:pPr><w:spacing w:after="0" w:before="0" w:line="240" w:lineRule="auto"/>'
    '<w:jc w:val="left"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/>'
    '<w:b/><w:color w:val="FFFFFF"/><w:sz w:val="28"/></w:rPr>'
    "<w:t>Laboratorio Clinico</w:t></w:r></w:p>"
    '<w:p><w:pPr><w:spacing w:after="0" w:before="0" w:line="240" w:lineRule="auto"/>'
    '<w:jc w:val="right"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/>'
    '<w:b/><w:color w:val="FFFFFF"/><w:sz w:val="17"/></w:rPr>'
    "<w:t>www.3cglab.com.mx   T 81.8361.8350 / 81.8679.6597</w:t></w:r></w:p>"
    '<w:p><w:pPr><w:spacing w:after="0" w:before="0" w:line="240" w:lineRule="auto"/>'
    '<w:jc w:val="center"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/>'
    '<w:b/><w:color w:val="FFFFFF"/><w:sz w:val="17"/></w:rPr>'
    "<w:t>Calidad en los ex\u00e1menes, calidez en el trato a precios justos</w:t></w:r></w:p>"
)

BRANDING_BLOCK_NEW = (
    '<w:p><w:pPr><w:spacing w:after="120" w:before="0" w:line="240" w:lineRule="auto"/>'
    '<w:jc w:val="left"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/>'
    '<w:b/><w:color w:val="FFFFFF"/><w:sz w:val="28"/></w:rPr>'
    '<w:t xml:space="preserve">Laboratorio\u00a0Clinico</w:t></w:r></w:p>'
    '<w:p><w:pPr><w:spacing w:after="160" w:before="80" w:line="240" w:lineRule="auto"/>'
    '<w:jc w:val="center"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/>'
    '<w:b/><w:color w:val="FFFFFF"/><w:sz w:val="17"/></w:rPr>'
    "<w:t>Calidad en los ex\u00e1menes, calidez en el trato a precios justos</w:t></w:r></w:p>"
    '<w:p><w:pPr><w:spacing w:after="0" w:before="80" w:line="240" w:lineRule="auto"/>'
    '<w:jc w:val="right"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/>'
    '<w:b/><w:color w:val="FFFFFF"/><w:sz w:val="17"/></w:rPr>'
    "<w:t>www.3cglab.com.mx   T 81.8361.8350 / 81.8679.6597</w:t></w:r></w:p>"
)

# Entre branding y tabla de paciente: duplicar párrafo vacío con “twips” de separación.
BRAND_TO_PATIENT_OLD = "</w:tbl><w:p/><w:tbl><w:tblPr><w:tblW w:type=\"auto\" w:w=\"0\"/><w:tblLayout w:type=\"fixed\"/><w:tblLook w:firstColumn=\"1\" w:firstRow=\"1\" w:lastColumn=\"0\" w:lastRow=\"0\" w:noHBand=\"0\" w:noVBand=\"1\" w:val=\"04A0\"/></w:tblPr><w:tblGrid><w:gridCol w:w=\"5760\"/><w:gridCol w:w=\"5846\"/></w:tblGrid><w:tr><w:tc><w:tcPr><w:tcW w:type=\"dxa\" w:w=\"5803\"/><w:tcMar><w:top w:w=\"10\" w:type=\"dxa\"/><w:start w:w=\"25\" w:type=\"dxa\"/><w:bottom w:w=\"10\" w:type=\"dxa\"/><w:end w:w=\"25\" w:type=\"dxa\"/></w:tcMar></w:tcPr><w:p><w:pPr><w:spacing w:after=\"0\" w:before=\"0\" w:line=\"240\" w:lineRule=\"auto\"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii=\"Arial\" w:hAnsi=\"Arial\"/><w:b/><w:sz w:val=\"21\"/></w:rPr><w:t>PACIENTE:"

BRAND_TO_PATIENT_NEW = (
    "</w:tbl>"
    '<w:p><w:pPr><w:spacing w:after="120" w:before="120" w:line="240" w:lineRule="auto"/></w:pPr></w:p>'
    '<w:p><w:pPr><w:spacing w:after="120" w:before="0" w:line="240" w:lineRule="auto"/></w:pPr></w:p>'
    "<w:tbl><w:tblPr><w:tblW w:type=\"auto\" w:w=\"0\"/><w:tblLayout w:type=\"fixed\"/><w:tblLook w:firstColumn=\"1\" w:firstRow=\"1\" w:lastColumn=\"0\" w:lastRow=\"0\" w:noHBand=\"0\" w:noVBand=\"1\" w:val=\"04A0\"/></w:tblPr><w:tblGrid><w:gridCol w:w=\"5760\"/><w:gridCol w:w=\"5846\"/></w:tblGrid><w:tr><w:tc><w:tcPr><w:tcW w:type=\"dxa\" w:w=\"5803\"/><w:tcMar><w:top w:w=\"10\" w:type=\"dxa\"/><w:start w:w=\"25\" w:type=\"dxa\"/><w:bottom w:w=\"10\" w:type=\"dxa\"/><w:end w:w=\"25\" w:type=\"dxa\"/></w:tcMar></w:tcPr><w:p><w:pPr><w:spacing w:after=\"0\" w:before=\"0\" w:line=\"240\" w:lineRule=\"auto\"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii=\"Arial\" w:hAnsi=\"Arial\"/><w:b/><w:sz w:val=\"21\"/></w:rPr><w:t>PACIENTE:"
)

TITLE_BLOCK_OLD = (
    "</w:tbl><w:p/><w:p><w:pPr><w:spacing w:after=\"0\" w:before=\"0\" w:line=\"240\" w:lineRule=\"auto\"/><w:jc w:val=\"center\"/></w:pPr>"
    '<w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:b/><w:sz w:val="22"/></w:rPr>'
    "<w:t>EXAMEN GENERAL DE ORINA</w:t></w:r></w:p><w:p/><w:tbl><w:tblPr><w:tblW w:type=\"auto\" w:w=\"0\"/><w:tblLayout w:type=\"fixed\"/><w:tblLook w:firstColumn=\"1\" w:firstRow=\"1\" w:lastColumn=\"0\" w:lastRow=\"0\" w:noHBand=\"0\" w:noVBand=\"1\" w:val=\"04A0\"/></w:tblPr><w:tblGrid><w:gridCol w:w=\"3456\"/><w:gridCol w:w=\"3600\"/><w:gridCol w:w=\"4550\"/></w:tblGrid><w:tr><w:tc><w:tcPr><w:tcW w:type=\"dxa\" w:w=\"3869\"/><w:tcMar><w:top w:w=\"6\" w:type=\"dxa\"/><w:start w:w=\"18\" w:type=\"dxa\"/><w:bottom w:w=\"6\" w:type=\"dxa\"/><w:end w:w=\"18\" w:type=\"dxa\"/></w:tcMar></w:tcPr><w:p><w:pPr><w:spacing w:after=\"0\" w:before=\"0\" w:line=\"240\" w:lineRule=\"auto\"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii=\"Arial\" w:hAnsi=\"Arial\"/><w:b/><w:sz w:val=\"21\"/><w:u w:val=\"single\"/></w:rPr><w:t>PARAMETRO</w:t>"
)

TITLE_BLOCK_NEW = (
    "</w:tbl>"
    '<w:p><w:pPr><w:spacing w:after="120" w:before="160" w:line="240" w:lineRule="auto"/></w:pPr></w:p>'
    '<w:p><w:pPr><w:spacing w:after="120" w:before="0" w:line="240" w:lineRule="auto"/></w:pPr></w:p>'
    '<w:p><w:pPr><w:spacing w:after="120" w:before="0" w:line="240" w:lineRule="auto"/><w:jc w:val="center"/></w:pPr>'
    '<w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:b/><w:sz w:val="22"/></w:rPr>'
    "<w:t>EXAMEN GENERAL DE ORINA</w:t></w:r></w:p>"
    '<w:p><w:pPr><w:spacing w:after="120" w:before="0" w:line="240" w:lineRule="auto"/></w:pPr></w:p>'
    "<w:tbl><w:tblPr><w:tblW w:type=\"auto\" w:w=\"0\"/><w:tblLayout w:type=\"fixed\"/><w:tblLook w:firstColumn=\"1\" w:firstRow=\"1\" w:lastColumn=\"0\" w:lastRow=\"0\" w:noHBand=\"0\" w:noVBand=\"1\" w:val=\"04A0\"/></w:tblPr><w:tblGrid><w:gridCol w:w=\"3456\"/><w:gridCol w:w=\"3600\"/><w:gridCol w:w=\"4550\"/></w:tblGrid><w:tr><w:tc><w:tcPr><w:tcW w:type=\"dxa\" w:w=\"3869\"/><w:tcMar><w:top w:w=\"6\" w:type=\"dxa\"/><w:start w:w=\"18\" w:type=\"dxa\"/><w:bottom w:w=\"6\" w:type=\"dxa\"/><w:end w:w=\"18\" w:type=\"dxa\"/></w:tcMar></w:tcPr><w:p><w:pPr><w:spacing w:after=\"0\" w:before=\"0\" w:line=\"240\" w:lineRule=\"auto\"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii=\"Arial\" w:hAnsi=\"Arial\"/><w:b/><w:sz w:val=\"21\"/><w:u w:val=\"single\"/></w:rPr><w:t>PARAMETRO</w:t>"
)

FOOTER_OLD = (
    "</w:tr></w:tbl><w:p><w:pPr><w:spacing w:after=\"0\" w:before=\"0\" w:line=\"240\" w:lineRule=\"auto\"/><w:jc w:val=\"right\"/></w:pPr><w:r><w:drawing>"
)

FOOTER_NEW = (
    "</w:tr></w:tbl>"
    '<w:p><w:pPr><w:spacing w:after="0" w:before="220" w:line="240" w:lineRule="auto"/><w:jc w:val="right"/></w:pPr><w:r><w:drawing>'
)


def main() -> int:
    if not DOCX.is_file():
        print("No existe:", DOCX, file=sys.stderr)
        return 1

    backup = DOCX.with_suffix(".docx.bak_layout_hf")
    shutil.copy2(DOCX, backup)

    with zipfile.ZipFile(DOCX, "r") as zin:
        xml = zin.read(ENTRY).decode("utf-8")

    orig = xml

    # Compatibilidad: si el bloque viejo no coincide (p.ej. ya corregido), intentar variante sin carácter corrupto.
    if BRANDING_BLOCK_OLD not in xml:
        print("No se encontró el bloque de branding esperado.", file=sys.stderr)
        return 1
    xml = xml.replace(BRANDING_BLOCK_OLD, BRANDING_BLOCK_NEW, 1)

    for label, old, new in (
        ("brand->patient", BRAND_TO_PATIENT_OLD, BRAND_TO_PATIENT_NEW),
        ("patient->title", TITLE_BLOCK_OLD, TITLE_BLOCK_NEW),
        ("results->footer", FOOTER_OLD, FOOTER_NEW),
    ):
        if old not in xml:
            print(f"No se encontró bloque: {label}", file=sys.stderr)
            return 1
        xml = xml.replace(old, new, 1)

    if xml == orig:
        print("Sin cambios (XML idéntico).", file=sys.stderr)
        return 1

    out = DOCX.with_suffix(".docx.patched_hf")
    with zipfile.ZipFile(DOCX, "r") as zin:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == ENTRY:
                    data = xml.encode("utf-8")
                zout.writestr(item, data)

    out.replace(DOCX)
    print("OK:", DOCX)
    print("Respaldo:", backup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
