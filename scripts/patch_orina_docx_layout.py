"""
Parchea examenes_medicos_templates/FORMATO EXAMEN DE ORINA.docx para el PDF vía LibreOffice:
- NBSP entre Laboratorio y Clinico (evita pérdida de la segunda palabra).
- Salto de línea tras el título del examen.
- Párrafos vacíos antes de PARAMETRO en el cuadro de la tabla (separa del título flotante).
"""
from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCX = ROOT / "examenes_medicos_templates" / "FORMATO EXAMEN DE ORINA.docx"
ENTRY = "word/document.xml"

SPACERS = (
    "<w:p w14:paraId=\"B0B0B001\" w14:textId=\"77777777\" w:rsidR=\"00BF7A0C\" w:rsidRDefault=\"00BF7A0C\" w:rsidP=\"00443875\">"
    "<w:pPr><w:jc w:val=\"both\"/><w:spacing w:before=\"100\" w:after=\"100\"/>"
    "<w:rPr><w:rFonts w:ascii=\"ADLaM Display\" w:hAnsi=\"ADLaM Display\" w:cs=\"ADLaM Display\"/>"
    "<w:sz w:val=\"12\"/><w:szCs w:val=\"12\"/></w:rPr></w:pPr></w:p>"
    "<w:p w14:paraId=\"B0B0B002\" w14:textId=\"77777777\" w:rsidR=\"00BF7A0C\" w:rsidRDefault=\"00BF7A0C\" w:rsidP=\"00443875\">"
    "<w:pPr><w:jc w:val=\"both\"/><w:spacing w:before=\"100\" w:after=\"100\"/>"
    "<w:rPr><w:rFonts w:ascii=\"ADLaM Display\" w:hAnsi=\"ADLaM Display\" w:cs=\"ADLaM Display\"/>"
    "<w:sz w:val=\"12\"/><w:szCs w:val=\"12\"/></w:rPr></w:pPr></w:p>"
    "<w:p w14:paraId=\"B0B0B003\" w14:textId=\"77777777\" w:rsidR=\"00BF7A0C\" w:rsidRDefault=\"00BF7A0C\" w:rsidP=\"00443875\">"
    "<w:pPr><w:jc w:val=\"both\"/><w:spacing w:before=\"100\" w:after=\"100\"/>"
    "<w:rPr><w:rFonts w:ascii=\"ADLaM Display\" w:hAnsi=\"ADLaM Display\" w:cs=\"ADLaM Display\"/>"
    "<w:sz w:val=\"12\"/><w:szCs w:val=\"12\"/></w:rPr></w:pPr></w:p>"
)

TABLE_TXBX_START = (
    '<w:txbxContent><w:p w14:paraId="2ED1A997" w14:textId="3C63E3D1" w:rsidR="00BF7A0C" '
    'w:rsidRPr="002F47D4" w:rsidRDefault="00443875" w:rsidP="00443875">'
)


def main() -> int:
    if not DOCX.is_file():
        print("No existe:", DOCX, file=sys.stderr)
        return 1

    backup = DOCX.with_suffix(".docx.bak")
    shutil.copy2(DOCX, backup)

    with zipfile.ZipFile(DOCX, "r") as zin:
        xml = zin.read(ENTRY).decode("utf-8")

    orig = xml

    xml = xml.replace(
        "<w:t>Laboratorio Clinico</w:t>",
        '<w:t xml:space="preserve">Laboratorio\u00a0Clinico</w:t>',
    )

    xml = xml.replace(
        "<w:t>EXAMEN GENERAL DE ORINA</w:t></w:r></w:p></w:txbxContent>",
        '<w:t xml:space="preserve">EXAMEN GENERAL DE ORINA\n</w:t></w:r></w:p>'
        '<w:p w14:paraId="E1B2C3D4" w14:textId="77777777" w:rsidR="00443875" w:rsidRDefault="00443875" w:rsidP="00443875">'
        '<w:pPr><w:jc w:val="center"/><w:spacing w:before="120" w:after="120"/>'
        '<w:rPr><w:rFonts w:ascii="ADLaM Display" w:hAnsi="ADLaM Display" w:cs="ADLaM Display"/>'
        '<w:sz w:val="10"/><w:szCs w:val="10"/></w:rPr></w:pPr></w:p></w:txbxContent>',
    )

    rep_tbl = "<w:txbxContent>" + SPACERS + TABLE_TXBX_START[len("<w:txbxContent>") :]
    while TABLE_TXBX_START in xml:
        xml = xml.replace(TABLE_TXBX_START, rep_tbl, 1)

    if xml == orig:
        print("Sin cambios aplicables (¿ya parcheado?).", file=sys.stderr)
        return 1

    out = DOCX.with_suffix(".docx.patched")
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
