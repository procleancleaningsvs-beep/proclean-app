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


# Ancla única: fila espaciadora del DESGLOSE (después de </w:trPr> de la altura, sigue la 1.ª celda 981 dxa).
_DESGLOSE_SPACER_ROW_SUFFIX = '<w:tc><w:tcPr><w:tcW w:w="981"'
_DESGLOSE_SPACER_TARGET_TWIPS = "2080"


def _patch_desglose_spacer_row_height(s: str) -> str:
    """
    Solo la fila espaciadora bajo el desglose (percepciones/deducciones), no el bloque de totales.
    Sube un poco el aire visual sin volver al hueco enorme (2986 twips).
    """
    suf = _DESGLOSE_SPACER_ROW_SUFFIX
    new_row = (
        f'<w:trPr><w:trHeight w:val="{_DESGLOSE_SPACER_TARGET_TWIPS}" '
        f'w:hRule="atLeast"/></w:trPr>{suf}'
    )
    if new_row in s:
        return s
    for needle in (
        f'<w:trPr><w:trHeight w:val="1950" w:hRule="atLeast"/></w:trPr>{suf}',
        f'<w:trPr><w:trHeight w:val="1780" w:hRule="atLeast"/></w:trPr>{suf}',
        f'<w:trPr><w:trHeight w:val="1520" w:hRule="atLeast"/></w:trPr>{suf}',
        f'<w:trPr><w:trHeight w:val="1280" w:hRule="atLeast"/></w:trPr>{suf}',
        f'<w:trPr><w:trHeight w:val="900" w:hRule="atLeast"/></w:trPr>{suf}',
        f'<w:trPr><w:trHeight w:val="360" w:hRule="atLeast"/></w:trPr>{suf}',
        f'<w:trPr><w:trHeight w:val="2986"/></w:trPr>{suf}',
    ):
        if needle in s:
            return s.replace(needle, new_row, 1)
    return s


_NETO_PARA_MARKER = 'w14:paraId="09F3A52C"'
_NETO_INNER_GRID_OLD = '<w:tblGrid><w:gridCol w:w="5778"/><w:gridCol w:w="4819"/></w:tblGrid>'
_NETO_INNER_GRID_NEW = '<w:tblGrid><w:gridCol w:w="3400"/><w:gridCol w:w="7197"/></w:tblGrid>'
_NETO_CELL_LABEL_W = "3400"
_NETO_CELL_AMOUNT_W = "7197"


def _patch_neto_a_pagar_inner_table_prevent_wrap(s: str) -> str:
    """
    Tabla interna de totales (Suma + Neto a Pagar): más ancho a la celda del importe y tab
    más a la izquierda para que montos largos no pasen a segunda línea. No toca el desglose central.
    """
    if _NETO_PARA_MARKER not in s:
        return s
    i = s.find(_NETO_PARA_MARKER)
    tr0 = s.rfind("<w:tr", 0, i)
    tr1 = s.find("</w:tr>", i)
    if tr0 < 0 or tr1 < 0:
        return s
    tr1 += len("</w:tr>")
    win = max(0, tr0 - 12000)
    gi = s[win:tr0].rfind(_NETO_INNER_GRID_OLD)
    if gi < 0:
        return s
    tbl0 = win + gi
    tbl1 = s.find("</w:tbl>", tr1)
    if tbl1 < 0:
        return s
    chunk = s[tbl0:tbl1]
    if _NETO_INNER_GRID_OLD in chunk:
        chunk = chunk.replace(_NETO_INNER_GRID_OLD, _NETO_INNER_GRID_NEW, 1)
    chunk = chunk.replace('<w:tcW w:w="5778"', f'<w:tcW w:w="{_NETO_CELL_LABEL_W}"')
    chunk = chunk.replace('<w:tcW w:w="4819"', f'<w:tcW w:w="{_NETO_CELL_AMOUNT_W}"')
    mi = chunk.find(_NETO_PARA_MARKER)
    if mi >= 0:
        t0 = chunk.rfind("<w:tr", 0, mi)
        t1 = chunk.find("</w:tr>", mi) + len("</w:tr>")
        net = chunk[t0:t1]
        if 'w:pos="4320"' in net:
            net = net.replace('w:pos="4320"', 'w:pos="2600"', 1)
        if '<w:ind w:right="36"/>' in net:
            net = net.replace('<w:ind w:right="36"/>', '<w:ind w:right="0"/>', 1)
        chunk = chunk[:t0] + net + chunk[t1:]
    return s[:tbl0] + chunk + s[tbl1:]


def _patch_footer_drawing_not_behind_document(s: str) -> str:
    """
    LibreOffice a veces pinta anclas del pie con behindDoc=1 como si estuvieran al inicio.
    La firma debe leerse solo al final del documento.
    """
    if 'behindDoc="1"' not in s:
        return s
    return s.replace('behindDoc="1"', 'behindDoc="0"')


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
                    s = _patch_footer_drawing_not_behind_document(s)
                    data = s.encode("utf-8")
                elif info.filename == "word/header1.xml":
                    s = data.decode("utf-8")
                    s = _patch_header_logo_src_rect(s)
                    data = s.encode("utf-8")
                elif info.filename == "word/document.xml":
                    s = data.decode("utf-8")
                    s = _patch_desglose_spacer_row_height(s)
                    s = _patch_neto_a_pagar_inner_table_prevent_wrap(s)
                    s = _patch_pg_mar_footer_gap(s)
                    s = _patch_atentamente_spacing(s)
                    s = _patch_signature_cell_remove_body_line(s)
                    data = s.encode("utf-8")
                zout.writestr(info, data)
    return buf.getvalue()
