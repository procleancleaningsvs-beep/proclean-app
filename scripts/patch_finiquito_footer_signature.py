"""Ajustes visuales del template FINIQUITO: logo, tabla, footer, firma y ATENTAMENTE.

Ejecutar desde la raíz del repo:
  python scripts/patch_finiquito_footer_signature.py

Solo manipula XML como texto para no romper prefijos w:/mc: (ElementTree reescribe ns0:).

Diagnóstico (evidencia en la plantilla actual):
- Logo: word/header1.xml, imagen inline en tabla del encabezado (no es anchor flotante).
  pic:blipFill incluye a:srcRect l/r que recorta el bitmap; LibreOffice puede recortar distinto que Word.
  wp:extent cx=1374405 cy=416560 EMU (~3.82x1.16 cm); image1.png 184x59 px.
- “Cancha” bajo conceptos: word/document.xml fila w:tr w14:paraId="11094AA5" con
  w:trHeight w:val="2986" (149.3 pt / ~52.7 mm fijos) y celdas casi vacías → hueco enorme si hay pocas filas llenas.
- Firma: word/footer1.xml, bloque en wps:wsp + wps:txbx (textbox), no párrafo suelto.
  a:ext cy limita altura del área del nombre; w:pgMar w:footer separa cuerpo del pie.
- PDF: modules/vitroflex_docs/libreoffice_pdf.py → soffice --headless --convert-to pdf.
  Si el DOCX ya está mal, el PDF hereda el problema; srcRect/trHeight son del DOCX.
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
    # Si ya estaba en 540000, subir altura útil del textbox del nombre (mismo cx que en plantilla).
    s = s.replace(
        '<a:ext cx="6872024" cy="540000"/></a:xfrm><a:prstGeom prst="rect">',
        '<a:ext cx="6872024" cy="990000"/></a:xfrm><a:prstGeom prst="rect">',
        1,
    )
    return s


def _patch_header_logo_src_rect(s: str) -> str:
    """Quita recorte horizontal del bitmap (srcRect) para que LO/Word muestren el logo completo."""
    return s.replace(
        '<a:srcRect l="5393" r="4439"/>',
        '<a:srcRect l="0" t="0" r="0" b="0"/>',
        1,
    )


def _patch_table_spacer_row(s: str) -> str:
    """
    Reduce la fila reservada enorme (2986 twips ≈ 149 pt) que deja “cancha” bajo los conceptos.
    atLeast + valor modesto: crece si el contenido lo pide, sin forzar ~53 mm vacíos.
    """
    old = '<w:trPr><w:trHeight w:val="2986"/></w:trPr>'
    new = '<w:trPr><w:trHeight w:val="360" w:hRule="atLeast"/></w:trPr>'
    if old not in s:
        return s
    return s.replace(old, new, 1)


def _patch_pg_mar_more_footer_gap(s: str) -> str:
    """Más distancia entre último párrafo del cuerpo y el pie (w:footer en pgMar)."""
    old = 'w:footer="827"'
    new = 'w:footer="1440"'
    if old not in s:
        return s
    return s.replace(old, new, 1)


def _patch_atentamente_spacing(s: str) -> str:
    """Espacio vertical bajo ATENTAMENTE (w:spacing w:after en el w:pPr de ese párrafo)."""
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
    inner = s[ppr_open + len("<w:pPr>") : ppr_close]
    target_after = "4320"  # twips; 216 pt (~76 mm) antes del pie
    ta = int(target_after)
    m_after = re.search(r'w:after="(\d+)"', inner)
    if m_after and int(m_after.group(1)) >= ta:
        return s
    if m_after:
        new_inner = re.sub(r'w:after="\d+"', f'w:after="{target_after}"', inner, count=1)
        return s[: ppr_open + len("<w:pPr>")] + new_inner + s[ppr_close:]
    if "w:spacing" in inner:
        # ya hay spacing pero sin after: ampliar el primer w:spacing
        new_inner = inner.replace(
            "<w:spacing ",
            f'<w:spacing w:after="{target_after}" ',
            1,
        )
        return s[: ppr_open + len("<w:pPr>")] + new_inner + s[ppr_close:]
    insert = f'<w:spacing w:before="360" w:after="{target_after}"/>'
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
                elif info.filename == "word/header1.xml":
                    s = data.decode("utf-8")
                    s = _patch_header_logo_src_rect(s)
                    data = s.encode("utf-8")
                elif info.filename == "word/document.xml":
                    s = data.decode("utf-8")
                    s = _patch_table_spacer_row(s)
                    s = _patch_pg_mar_more_footer_gap(s)
                    s = _patch_atentamente_spacing(s)
                    data = s.encode("utf-8")
                zout.writestr(info, data)
    DOCX.write_bytes(buf.getvalue())
    print("OK:", DOCX)


if __name__ == "__main__":
    main()
