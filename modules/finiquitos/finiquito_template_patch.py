"""
Parches idempotentes sobre el DOCX de finiquito (bytes), misma lógica para app y CLI.

No depende de rutas: recibe ZIP en memoria y devuelve ZIP parcheado.
El script `scripts/patch_finiquito_footer_signature.py` solo delega aquí y escribe disco.
"""

from __future__ import annotations

import re
import zipfile
from io import BytesIO


CLOSE_ALT = "</mc:AlternateContent></w:r>"


def _strip_mc_fallbacks(s: str) -> str:
    return re.sub(r"<mc:Fallback>.*?</mc:Fallback>\s*", "", s, flags=re.DOTALL)


def _remove_second_footer_run(s: str) -> str:
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
    s = s.replace('cy="152400"', 'cy="540000"', 1)
    # Punto medio firma: ~20 mm útiles (antes 990000 ≈ 27 mm)
    s = s.replace(
        '<a:ext cx="6872024" cy="540000"/></a:xfrm><a:prstGeom prst="rect">',
        '<a:ext cx="6872024" cy="720000"/></a:xfrm><a:prstGeom prst="rect">',
        1,
    )
    s = s.replace(
        '<a:ext cx="6872024" cy="990000"/></a:xfrm><a:prstGeom prst="rect">',
        '<a:ext cx="6872024" cy="720000"/></a:xfrm><a:prstGeom prst="rect">',
        1,
    )
    return s


def _patch_header_logo_src_rect(s: str) -> str:
    return s.replace(
        '<a:srcRect l="5393" r="4439"/>',
        '<a:srcRect l="0" t="0" r="0" b="0"/>',
        1,
    )


def _patch_table_spacer_row(s: str) -> str:
    """Aire moderado bajo conceptos: entre aplastado (360) y cancha (2986)."""
    new_row = '<w:trPr><w:trHeight w:val="900" w:hRule="atLeast"/></w:trPr>'
    if new_row in s:
        return s
    old_exact = '<w:trPr><w:trHeight w:val="2986"/></w:trPr>'
    if old_exact in s:
        return s.replace(old_exact, new_row, 1)
    old_small = '<w:trPr><w:trHeight w:val="360" w:hRule="atLeast"/></w:trPr>'
    if old_small in s:
        return s.replace(old_small, new_row, 1)
    return s


def _patch_pg_mar_footer_gap(s: str) -> str:
    """Entre 827 original y 1440 agresivo: ~56 pt."""
    for old, new in (
        ('w:footer="827"', 'w:footer="1120"'),
        ('w:footer="1440"', 'w:footer="1120"'),
    ):
        if old in s:
            return s.replace(old, new, 1)
    return s


def _patch_atentamente_spacing(s: str) -> str:
    """Espacio razonable bajo ATENTAMENTE (~72 pt)."""
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
    target_after = "1440"  # 72 pt
    ta = int(target_after)
    m_after = re.search(r'w:after="(\d+)"', inner)
    if m_after and int(m_after.group(1)) == ta:
        return s
    if m_after:
        new_inner = re.sub(r'w:after="\d+"', f'w:after="{target_after}"', inner, count=1)
        return s[: ppr_open + len("<w:pPr>")] + new_inner + s[ppr_close:]
    if "w:spacing" in inner:
        new_inner = inner.replace(
            "<w:spacing ",
            f'<w:spacing w:after="{target_after}" ',
            1,
        )
        return s[: ppr_open + len("<w:pPr>")] + new_inner + s[ppr_close:]
    insert = f'<w:spacing w:before="360" w:after="{target_after}"/>'
    return s[:ppr_close] + insert + s[ppr_close:]


def _patch_neto_cell_nowrap_wider(s: str) -> str:
    """Evita partir 'Neto a Pagar' + monto: celda ancha, tab más a la derecha, sin noWrap (mejor en LO)."""
    needle_tc = (
        '<w:tcW w:w="4862" w:type="dxa"/><w:tcBorders><w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
        '<w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/></w:tcBorders>'
        '<w:vAlign w:val="center"/></w:tcPr><w:p w14:paraId="09F3A52C"'
    )
    repl_tc = (
        '<w:tcW w:w="6600" w:type="dxa"/><w:tcBorders><w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
        '<w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/></w:tcBorders>'
        '<w:vAlign w:val="center"/></w:tcPr><w:p w14:paraId="09F3A52C"'
    )
    if needle_tc in s:
        s = s.replace(needle_tc, repl_tc, 1)
    needle_oldwrap = (
        '<w:tcW w:w="5400" w:type="dxa"/><w:tcBorders><w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
        '<w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/></w:tcBorders>'
        '<w:vAlign w:val="center"/><w:noWrap/></w:tcPr><w:p w14:paraId="09F3A52C"'
    )
    repl_nowrap_off = (
        '<w:tcW w:w="6600" w:type="dxa"/><w:tcBorders><w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
        '<w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/></w:tcBorders>'
        '<w:vAlign w:val="center"/></w:tcPr><w:p w14:paraId="09F3A52C"'
    )
    if needle_oldwrap in s:
        s = s.replace(needle_oldwrap, repl_nowrap_off, 1)
    for pos, right in (("2611", "126"), ("3200", "72")):
        old_tab = (
            f'<w:tabs><w:tab w:val="left" w:pos="{pos}"/></w:tabs><w:spacing w:before="83"/><w:ind w:right="{right}"/>'
            '<w:rPr><w:rFonts w:ascii="Arial"/><w:b/><w:position w:val="2"/><w:sz w:val="18"/></w:rPr></w:pPr>'
            '<w:r><w:rPr><w:rFonts w:ascii="Arial"/><w:b/><w:sz w:val="20"/></w:rPr>'
            '<w:t xml:space="preserve">                      </w:t></w:r>'
            '<w:proofErr w:type="gramStart"/><w:r><w:rPr><w:rFonts w:ascii="Arial"/><w:b/><w:sz w:val="20"/></w:rPr><w:t>Neto</w:t>'
        )
        if old_tab in s:
            s = s.replace(
                old_tab,
                '<w:tabs><w:tab w:val="left" w:pos="4320"/></w:tabs><w:spacing w:before="83"/><w:ind w:right="36"/>'
                '<w:rPr><w:rFonts w:ascii="Arial"/><w:b/><w:position w:val="2"/><w:sz w:val="18"/></w:rPr></w:pPr>'
                '<w:r><w:rPr><w:rFonts w:ascii="Arial"/><w:b/><w:sz w:val="20"/></w:rPr>'
                '<w:t xml:space="preserve">   </w:t></w:r>'
                '<w:proofErr w:type="gramStart"/><w:r><w:rPr><w:rFonts w:ascii="Arial"/><w:b/><w:sz w:val="20"/></w:rPr><w:t>Neto</w:t>',
                1,
            )
            break
    return s


def _patch_signature_cell_remove_body_line(s: str) -> str:
    """Quita línea de firma del cuerpo (celda 11B7B088 antes de totales); la firma va al pie."""
    bad = (
        '<w:p w14:paraId="11B7B088" w14:textId="3AEE4489" w:rsidR="00E607E1" w:rsidRDefault="00E607E1" '
        'w:rsidP="00D50D59"><w:pPr><w:pStyle w:val="TableParagraph"/><w:spacing w:before="120" w:after="60"/>'
        '<w:ind w:left="0"/><w:jc w:val="center"/><w:rPr><w:sz w:val="18"/></w:rPr></w:pPr>'
        '<w:r><w:rPr><w:sz w:val="18"/></w:rPr>'
        '<w:t xml:space="preserve">______________________________________________</w:t></w:r></w:p></w:tc>'
    )
    good = (
        '<w:p w14:paraId="11B7B088" w14:textId="3AEE4489" w:rsidR="00E607E1" w:rsidRDefault="00E607E1" '
        'w:rsidP="00D50D59"><w:pPr><w:pStyle w:val="TableParagraph"/><w:ind w:left="87"/>'
        '<w:rPr><w:sz w:val="18"/></w:rPr></w:pPr></w:p></w:tc>'
    )
    if bad in s:
        return s.replace(bad, good, 1)
    return s


def _patch_footer_line_above_nombre(s: str) -> str:
    """Línea para firmar solo en el textbox del pie, encima del nombre."""
    if "w14:paraId=\"F1RM4SIG\"" in s:
        return s
    old = '<w:txbxContent><w:p w14:paraId="5BB30B96"'
    new = (
        '<w:txbxContent><w:p w14:paraId="F1RM4SIG" w14:textId="77777777" w:rsidR="00CE1084" '
        'w:rsidRDefault="00E607E1" w:rsidP="00E607E1"><w:pPr><w:pStyle w:val="Textoindependiente"/>'
        '<w:spacing w:before="120" w:after="140"/><w:jc w:val="center"/><w:rPr><w:sz w:val="18"/></w:rPr></w:pPr>'
        '<w:r><w:rPr><w:sz w:val="18"/></w:rPr>'
        '<w:t xml:space="preserve">______________________________________________</w:t></w:r></w:p>'
        '<w:p w14:paraId="5BB30B96"'
    )
    if old in s:
        return s.replace(old, new, 1)
    return s


def _dedupe_adjacent_placeholder(s: str) -> str:
    return s.replace(
        "{empleado_nombre_completo}{empleado_nombre_completo}",
        "{empleado_nombre_completo}",
    )


def patch_finiquito_docx_template_bytes(docx_bytes: bytes) -> bytes:
    """
    Aplica todos los parches al ZIP DOCX. Idempotente con los valores objetivo actuales.
    """
    buf = BytesIO()
    with zipfile.ZipFile(BytesIO(docx_bytes), "r") as zin:
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
                    s = _patch_pg_mar_footer_gap(s)
                    s = _patch_atentamente_spacing(s)
                    s = _patch_neto_cell_nowrap_wider(s)
                    s = _patch_signature_cell_remove_body_line(s)
                    data = s.encode("utf-8")
                zout.writestr(info, data)
    return buf.getvalue()
