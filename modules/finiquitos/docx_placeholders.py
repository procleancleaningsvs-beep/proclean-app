"""
Reemplazo de placeholders en DOCX sin recrear documento.

Objetivo: preservar layout/tipografía/alineación del DOCX base.
No reconstruye tablas/párrafos: solo sustituye texto en nodos existentes.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
ET.register_namespace("w", W_NS)


def _local(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


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
        texts = [t for t in p.iter(_local("t"))]
        if not texts:
            continue
        full = "".join(t.text or "" for t in texts)
        if not full.strip():
            continue
        new = full
        for k, v in mapping.items():
            if k in new:
                new = new.replace(k, v)
        if new != full:
            _distribute_text_over_runs(texts, new)


def _distribute_text_over_runs(texts: list[ET.Element], new_text: str) -> None:
    """
    Redistribuye texto sobre los mismos w:t para preservar al máximo
    el estilo/tabulaciones/alineación existentes en la plantilla.
    """
    original_lengths = [len(t.text or "") for t in texts]
    # Si algún run venía vacío, mantenemos al menos uno para no perder texto.
    if not any(original_lengths):
        texts[0].text = new_text
        for t in texts[1:]:
            t.text = ""
        return

    idx = 0
    for i, t in enumerate(texts):
        if i == len(texts) - 1:
            t.text = new_text[idx:]
            break
        n = original_lengths[i]
        if n <= 0:
            t.text = ""
            continue
        t.text = new_text[idx : idx + n]
        idx += n

