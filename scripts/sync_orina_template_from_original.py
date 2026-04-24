"""
Instala la plantilla de orina desde el DOCX oficial (ORIGINAL) y aplica solo
parches mínimos necesarios para la exportación actual de la app.

Uso (desde la raíz del repo):
  python scripts/sync_orina_template_from_original.py

Por defecto lee:
  %USERPROFILE%\\Downloads\\FORMATO EXAMEN DE ORINA (ORIGINAL).docx

Sobrescribe:
  examenes_medicos_templates/FORMATO EXAMEN DE ORINA.docx

Parche actual:
- Sustituye el valor fijo de resultado de LEUCOCITOS (\"5/C\") por el placeholder
  `{leucocitos}` en la columna RESULTADO (dos textboxes duplicados en el XML).
"""
from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DST = ROOT / "examenes_medicos_templates" / "FORMATO EXAMEN DE ORINA.docx"
ENTRY = "word/document.xml"

LEUC_RESULT_OLD = (
    "{eritrocitos}</w:t></w:r></w:p>"
    '<w:p w14:paraId="667000B3" w14:textId="42860027" w:rsidR="0072017F" w:rsidRDefault="00257CFE" w:rsidP="00443875">'
    '<w:pPr><w:jc w:val="both"/><w:rPr><w:rFonts w:ascii="ADLaM Display" w:hAnsi="ADLaM Display" w:cs="ADLaM Display"/>'
    '<w:b/><w:lang w:val="es-MX"/></w:rPr></w:pPr>'
    "<w:r><w:rPr><w:rFonts w:ascii=\"ADLaM Display\" w:hAnsi=\"ADLaM Display\" w:cs=\"ADLaM Display\"/>"
    '<w:b/><w:lang w:val="es-MX"/></w:rPr><w:t>5/C</w:t></w:r></w:p>'
)

LEUC_RESULT_NEW = (
    "{eritrocitos}</w:t></w:r></w:p>"
    '<w:p w14:paraId="667000B3" w14:textId="42860027" w:rsidR="0072017F" w:rsidRDefault="00257CFE" w:rsidP="00443875">'
    '<w:pPr><w:jc w:val="both"/><w:rPr><w:rFonts w:ascii="ADLaM Display" w:hAnsi="ADLaM Display" w:cs="ADLaM Display"/>'
    '<w:b/><w:lang w:val="es-MX"/></w:rPr></w:pPr>'
    "<w:r><w:rPr><w:rFonts w:ascii=\"ADLaM Display\" w:hAnsi=\"ADLaM Display\" w:cs=\"ADLaM Display\"/>"
    '<w:b/><w:lang w:val="es-MX"/></w:rPr><w:t>{leucocitos}</w:t></w:r></w:p>'
)


def _default_source() -> Path:
    return Path.home() / "Downloads" / "FORMATO EXAMEN DE ORINA (ORIGINAL).docx"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--source",
        type=Path,
        default=_default_source(),
        help="Ruta al FORMATO EXAMEN DE ORINA (ORIGINAL).docx",
    )
    args = ap.parse_args()
    src: Path = args.source

    if not src.is_file():
        print("No existe el ORIGINAL:", src, file=sys.stderr)
        return 1

    DST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, DST)

    with zipfile.ZipFile(DST, "r") as zin:
        xml = zin.read(ENTRY).decode("utf-8")

    n = xml.count(LEUC_RESULT_OLD)
    if n != 2:
        print(
            f"Advertencia: se esperaban 2 bloques '5/C' tras {{eritrocitos}}; encontrados: {n}",
            file=sys.stderr,
        )
    xml2 = xml.replace(LEUC_RESULT_OLD, LEUC_RESULT_NEW)
    if xml2 == xml:
        print("No se aplicó el parche de {leucocitos} (bloque no encontrado).", file=sys.stderr)
        return 1

    tmp = DST.with_suffix(".docx.tmp_sync")
    with zipfile.ZipFile(DST, "r") as zin:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == ENTRY:
                    data = xml2.encode("utf-8")
                zout.writestr(item, data)
    tmp.replace(DST)
    print("OK:", DST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
