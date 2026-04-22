"""
Ajuste fino del pie DOCX de Examen de Sangre: bloque derecho (nombre, línea,
responsable / universidad) y VML asociado, para evitar recorte en PDF (LibreOffice).

Ejecutar una vez desde la raíz del repo:
  python tools/patch_sangre_footer_layout.py
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCX = ROOT / "examenes_medicos_templates" / "FORMATO EXAMEN DE SANGRE.docx"

# Desplazamiento hacia arriba del bloque derecho (nombre + regla + firma textual).
DELTA_EMU = 280_000
EMU_PER_PT = 12_700

NAME_V_OLD = 7_793_502
LINE_V_OLD = 8_042_275
SIGN_V_OLD = 8_052_582

NAME_CY_OLD = 139_700
SIGN_CY_OLD = 389_890

NAME_CY_NEW = 170_000
SIGN_CY_NEW = 590_000


def _margin_pt(pos_emu: int) -> str:
    return f"{pos_emu / EMU_PER_PT:.2f}"


def patch_footer_xml(xml: str) -> tuple[str, bool]:
    """Devuelve (xml, True) si hubo reemplazos; si ya estaba parcheado, (xml, False)."""
    if str(LINE_V_OLD) not in xml:
        return xml, False

    name_v = NAME_V_OLD - DELTA_EMU
    line_v = LINE_V_OLD - DELTA_EMU
    sign_v = SIGN_V_OLD - DELTA_EMU

    xml = xml.replace(str(LINE_V_OLD), str(line_v))
    xml = xml.replace(str(NAME_V_OLD), str(name_v))
    xml = xml.replace(str(SIGN_V_OLD), str(sign_v))
    xml = xml.replace(f'cy="{NAME_CY_OLD}"', f'cy="{NAME_CY_NEW}"')
    xml = xml.replace(f'cy="{SIGN_CY_OLD}"', f'cy="{SIGN_CY_NEW}"')

    xml = xml.replace("margin-top:633.25pt", f"margin-top:{_margin_pt(line_v)}pt")
    xml = xml.replace("margin-top:613.65pt", f"margin-top:{_margin_pt(name_v)}pt")
    xml = xml.replace("margin-top:634.05pt", f"margin-top:{_margin_pt(sign_v)}pt")
    xml = xml.replace("height:11pt", f"height:{NAME_CY_NEW / EMU_PER_PT:.2f}pt")
    xml = xml.replace("height:30.7pt", f"height:{SIGN_CY_NEW / EMU_PER_PT:.2f}pt")
    return xml, True


def main() -> None:
    if not DOCX.is_file():
        raise SystemExit(f"No existe plantilla: {DOCX}")

    targets = {"word/footer2.xml", "word/footer4.xml"}
    with zipfile.ZipFile(DOCX, "r") as zin:
        infos = zin.infolist()
        raw_by_name = {zi.filename: zin.read(zi.filename) for zi in infos}

    any_change = False
    patched_footer2: bytes | None = None
    buf = io.BytesIO()
    seen_names: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in infos:
            data = raw_by_name[item.filename]
            seen_names.add(item.filename)
            if item.filename in targets:
                text = data.decode("utf-8")
                text2, changed = patch_footer_xml(text)
                if changed:
                    any_change = True
                    data = text2.encode("utf-8")
                if item.filename == "word/footer2.xml":
                    patched_footer2 = data
            elif item.filename in {"word/footer1.xml", "word/footer3.xml"} and patched_footer2 is not None:
                if data != patched_footer2:
                    data = patched_footer2
                    any_change = True
            elif (
                item.filename in {"word/_rels/footer1.xml.rels", "word/_rels/footer3.xml.rels"}
                and "word/_rels/footer2.xml.rels" in raw_by_name
            ):
                rel2 = raw_by_name["word/_rels/footer2.xml.rels"]
                if data != rel2:
                    data = rel2
                    any_change = True
            zout.writestr(item, data)

        if patched_footer2 is not None:
            for missing in ("word/footer1.xml", "word/footer3.xml"):
                if missing not in seen_names:
                    zout.writestr(missing, patched_footer2)
                    any_change = True
            rel2 = raw_by_name.get("word/_rels/footer2.xml.rels")
            if rel2 is not None:
                for missing_rel in ("word/_rels/footer1.xml.rels", "word/_rels/footer3.xml.rels"):
                    if missing_rel not in seen_names:
                        zout.writestr(missing_rel, rel2)
                        any_change = True

    if not any_change:
        print(f"Sin cambios (ya aplicado o sin coincidencias): {DOCX.name}")
        return

    DOCX.write_bytes(buf.getvalue())
    print(f"OK: parcheado {DOCX.name} ({', '.join(sorted(targets))})")


if __name__ == "__main__":
    main()
