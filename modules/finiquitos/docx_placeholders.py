"""
Reemplazo de placeholders en DOCX sin alterar estructura: fusiona w:t por w:p y reescribe.
Cubre texto en cuerpo, tablas, encabezados, pies y cuadros de texto (w:p bajo word/).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
ET.register_namespace("w", W_NS)


def _local(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


def _escape_xml_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def replace_placeholders_in_docx_bytes(docx_bytes: bytes, mapping: dict[str, str]) -> bytes:
    """
    mapping: claves con llaves, p. ej. '{t1}' -> '1,890.24'
    Valores vacíos conservan la fila (placeholder desaparece).
    """
    buf = BytesIO()
    with ZipFile(BytesIO(docx_bytes), "r") as zin:
        with ZipFile(buf, "w", ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename.startswith("word/") and info.filename.endswith(".xml"):
                    root = ET.fromstring(data)
                    _replace_in_xml_tree(root, mapping)
                    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                zout.writestr(info, data)
    return buf.getvalue()


def _replace_in_xml_tree(root: ET.Element, mapping: dict[str, str]) -> None:
    for p in root.iter(_local("p")):
        texts = list(p.iter(_local("t")))
        if not texts:
            continue
        full = "".join((t.text or "") for t in texts)
        if not full.strip():
            continue
        new = full
        for k, v in mapping.items():
            if k in new:
                new = new.replace(k, _escape_xml_text(v))
        if new != full:
            texts[0].text = new
            for t in texts[1:]:
                t.text = ""

