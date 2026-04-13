"""
Parches idempotentes sobre el DOCX de finiquito (bytes), misma lógica para app y CLI.

No depende de rutas: recibe ZIP en memoria y devuelve ZIP parcheado.
El script `scripts/patch_finiquito_footer_signature.py` solo delega aquí y escribe disco.
"""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from pathlib import Path


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
    # Plantilla recompilada con scripts/patch_finiquito_docx_template.py (rsid 003D30AC): misma celda,
    # guiones + nombre en cuerpo; debe vaciarse para no duplicar la firma respecto al pie.
    bad_build = (
        '<w:p w14:paraId="11B7B088" w14:textId="3AEE4489" w:rsidR="003D30AC" w:rsidRDefault="003D30AC">'
        '<w:pPr><w:pStyle w:val="TableParagraph"/><w:spacing w:before="240"/><w:ind w:left="0"/>'
        '<w:jc w:val="center"/><w:rPr><w:sz w:val="18"/></w:rPr></w:pPr>'
        '<w:r><w:rPr><w:sz w:val="18"/></w:rPr>'
        '<w:t xml:space="preserve">________________________________________</w:t></w:r></w:p>'
        '<w:p w14:paraId="11B7B089" w14:textId="3AEE4490" w:rsidR="003D30AC" w:rsidRDefault="003D30AC">'
        '<w:pPr><w:pStyle w:val="TableParagraph"/><w:spacing w:before="200" w:after="0" w:line="276" '
        'w:lineRule="atLeast"/><w:ind w:left="0"/><w:jc w:val="center"/>'
        '<w:rPr><w:sz w:val="17"/></w:rPr></w:pPr>'
        '<w:r><w:rPr><w:sz w:val="17"/></w:rPr><w:t>{empleado_nombre_completo}</w:t></w:r></w:p>'
    )
    good_build = (
        '<w:p w14:paraId="11B7B088" w14:textId="3AEE4489" w:rsidR="003D30AC" w:rsidRDefault="003D30AC">'
        '<w:pPr><w:pStyle w:val="TableParagraph"/><w:ind w:left="87"/>'
        '<w:rPr><w:sz w:val="18"/></w:rPr></w:pPr></w:p>'
    )
    if bad_build in s:
        return s.replace(bad_build, good_build, 1)
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


def _patch_desglose_concept_placeholders_finiquito(s: str) -> str:
    """
    Sustituye textos fijos de concepto/número en la tabla de desglose por placeholders
    {p1_num}…{p5_nom} para que la edición libre v2 pueda reexportar número y nombre.
    Idempotente si ya se aplicó.
    """
    if "{p1_nom}" in s:
        return s
    base = Path(__file__).resolve().parent

    def _read(name: str) -> str | None:
        p = base / name
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8")

    n1 = '<w:t>1</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:tcW w:w="3042" w:type="dxa"'
    n1b = '<w:t>{p1_num}</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:tcW w:w="3042" w:type="dxa"'
    if n1 in s:
        s = s.replace(n1, n1b, 1)
    if "<w:t>Sueldo</w:t>" in s:
        s = s.replace("<w:t>Sueldo</w:t>", "<w:t>{p1_nom}</w:t>", 1)

    n2 = '<w:t>3</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:tcW w:w="3042" w:type="dxa"'
    n2b = '<w:t>{p2_num}</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:tcW w:w="3042" w:type="dxa"'
    if n2 in s:
        s = s.replace(n2, n2b, 1)
    old2 = _read("_snippet_row2_p.xml")
    new2 = (
        '<w:p w14:paraId="7BBE51AF" w14:textId="77777777" w:rsidR="00CE1084" w:rsidRDefault="00000000">'
        '<w:pPr><w:pStyle w:val="TableParagraph"/><w:ind w:left="88"/><w:rPr><w:sz w:val="18"/></w:rPr></w:pPr>'
        '<w:r><w:rPr><w:color w:val="808080"/><w:sz w:val="18"/></w:rPr><w:t>{p2_nom}</w:t></w:r></w:p></w:tc>'
    )
    if old2 and old2 in s:
        s = s.replace(old2, new2, 1)

    n3 = '<w:t>19</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:tcW w:w="3042" w:type="dxa"'
    n3b = '<w:t>{p3_num}</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:tcW w:w="3042" w:type="dxa"'
    if n3 in s:
        s = s.replace(n3, n3b, 1)
    old3 = _read("_snippet_row3_p.xml")
    new3 = (
        '<w:p w14:paraId="4B78EA14" w14:textId="77777777" w:rsidR="00CE1084" w:rsidRDefault="00000000">'
        '<w:pPr><w:pStyle w:val="TableParagraph"/><w:ind w:left="88"/><w:rPr><w:sz w:val="18"/></w:rPr></w:pPr>'
        '<w:r><w:rPr><w:color w:val="808080"/><w:sz w:val="18"/></w:rPr><w:t>{p3_nom}</w:t></w:r></w:p></w:tc>'
    )
    if old3 and old3 in s:
        s = s.replace(old3, new3, 1)

    n4 = '<w:t>22</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:tcW w:w="3042" w:type="dxa"'
    n4b = '<w:t>{p4_num}</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:tcW w:w="3042" w:type="dxa"'
    if n4 in s:
        s = s.replace(n4, n4b, 1)
    old4 = _read("_snippet_row4_concept.xml")
    new4 = (
        '<w:p w14:paraId="18B298EA" w14:textId="660A8FBB" w:rsidR="003D30AC" w:rsidRDefault="003D30AC">'
        '<w:pPr><w:pStyle w:val="TableParagraph"/><w:ind w:left="88"/><w:rPr><w:sz w:val="18"/></w:rPr></w:pPr>'
        '<w:r><w:rPr><w:color w:val="808080"/><w:sz w:val="18"/></w:rPr><w:t>{p4_nom}</w:t></w:r></w:p></w:tc>'
    )
    if old4 and old4 in s:
        s = s.replace(old4, new4, 1)

    n5 = '<w:t>24</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:tcW w:w="3042" w:type="dxa"'
    n5b = '<w:t>{p5_num}</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:tcW w:w="3042" w:type="dxa"'
    if n5 in s:
        s = s.replace(n5, n5b, 1)
    if "<w:t>Aguinaldo</w:t>" in s:
        s = s.replace("<w:t>Aguinaldo</w:t>", "<w:t>{p5_nom}</w:t>", 1)

    return s


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
                    s = _patch_pg_mar_footer_gap(s)
                    s = _patch_atentamente_spacing(s)
                    s = _patch_signature_cell_remove_body_line(s)
                    s = _patch_desglose_concept_placeholders_finiquito(s)
                    data = s.encode("utf-8")
                zout.writestr(info, data)
    return buf.getvalue()
