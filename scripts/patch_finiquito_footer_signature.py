"""Ajustes visuales del template FINIQUITO: footer, firma y párrafo ATENTAMENTE.

Ejecutar desde la raíz del repo:
  python scripts/patch_finiquito_footer_signature.py

Solo manipula XML como texto para no romper prefijos w:/mc: (ElementTree reescribe ns0:).
"""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCX = ROOT / "docx_templates" / "FINIQUITO FORMATO.docx"

CLOSE_ALT = "</mc:AlternateContent></w:r>"


def _strip_mc_fallbacks(s: str) -> str:
    return re.sub(r"<mc:Fallback>.*?</mc:Fallback>\s*", "", s, flags=re.DOTALL)


def _remove_second_footer_run(s: str) -> str:
    """Quita el segundo <w:r> con otro ancla duplicado tras el primero."""
    a = s.find(CLOSE_ALT)
    if a < 0:
        return s
    start = a + len(CLOSE_ALT)
    chunk = s[start : start + 40].lstrip()
    if not chunk.startswith("<w:r"):
        return s
    b = s.find(CLOSE_ALT, start)
    if b < 0:
        return s
    end = b + len(CLOSE_ALT)
    return s[:start] + s[end:]


def _boost_spacing_and_extent(s: str) -> str:
    s = s.replace(
        '<w:pStyle w:val="Textoindependiente"/><w:spacing w:before="12"/>',
        '<w:pStyle w:val="Textoindependiente"/><w:spacing w:before="360" w:after="360"/>',
        1,
    )
    # Solo el cy pequeño de la caja del nombre; el primer cy mayor suele ser el contenedor.
    s = s.replace('cy="152400"', 'cy="540000"', 1)
    return s


def _patch_atentamente_spacing(s: str) -> str:
    """Espacio real entre ATENTAMENTE y el pie (nombre). Inserta w:spacing en el w:pPr de ese párrafo."""
    key = "<w:t>ATENTAMENTE</w:t>"
    idx = s.find(key)
    if idx < 0:
        return s
    ppr_close = s.rfind("</w:pPr>", 0, idx)
    if ppr_close < 0:
        return s
    ppr_open = s.rfind("<w:pPr>", 0, ppr_close)
    if ppr_open < 0:
        return s
    block = s[ppr_open:ppr_close]
    if 'w:after="2160"' in block and "w:spacing" in block:
        return s
    insert = '<w:spacing w:before="360" w:after="2160"/>'
    return s[:ppr_close] + insert + s[ppr_close:]


def _dedupe_adjacent_placeholder(s: str) -> str:
    return s.replace(
        "{empleado_nombre_completo}{empleado_nombre_completo}",
        "{empleado_nombre_completo}",
    )


def main() -> None:
    if not DOCX.is_file():
        raise SystemExit(f"No existe {DOCX}")
    buf = BytesIO()
    with zipfile.ZipFile(DOCX, "r") as zin:
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == "word/footer1.xml":
                    s = data.decode("utf-8")
                    s = _strip_mc_fallbacks(s)
                    s = _remove_second_footer_run(s)
                    s = _dedupe_adjacent_placeholder(s)
                    s = _boost_spacing_and_extent(s)
                    data = s.encode("utf-8")
                elif info.filename == "word/document.xml":
                    s = data.decode("utf-8")
                    s = _patch_atentamente_spacing(s)
                    data = s.encode("utf-8")
                zout.writestr(info, data)
    DOCX.write_bytes(buf.getvalue())
    print("OK:", DOCX)


if __name__ == "__main__":
    main()
