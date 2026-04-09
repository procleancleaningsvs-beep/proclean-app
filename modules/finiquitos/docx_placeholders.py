"""
Reemplazo de placeholders en DOCX sin recrear documento.

El template manda: solo sustitución de texto. No se reconstruyen tablas ni párrafos.

Importante: repartir el texto nuevo usando las longitudes de los runs originales rompe
espacios y palabras cuando el reemplazo cambia la longitud (p. ej. "cubierta amás",
",teniéndose"). Si un párrafo cambia, se consolida en el primer w:t del flujo del párrafo.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
ET.register_namespace("w", W_NS)


def _local(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


_SKIP_SUBTREES = frozenset(
    {
        _local("drawing"),
        _local("tbl"),
    }
)


def _build_parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    parent: dict[ET.Element, ET.Element] = {}
    for el in root.iter():
        for ch in el:
            parent[ch] = el
    return parent


def _paragraph_inside_txbx_content(p: ET.Element, parent: dict[ET.Element, ET.Element]) -> bool:
    x: ET.Element | None = p
    while x is not None:
        if x.tag == _local("txbxContent"):
            return True
        x = parent.get(x)
    return False


def _collect_w_t_ordered_for_paragraph(p: ET.Element, parent: dict[ET.Element, ET.Element]) -> list[ET.Element]:
    """
    Nodos w:t del párrafo en orden de lectura.

    - En párrafos del cuerpo: no se entra a w:drawing ni w:tbl (el nombre en footer en textbox
      vive en otro w:p dentro del drawing, se procesa aparte).
    - En párrafos dentro de w:txbxContent: se toma todo el texto del párrafo (sin cruzar otro
      w:p anidado).
    """
    out: list[ET.Element] = []

    def walk(el: ET.Element, skip_drawing: bool) -> None:
        if el.tag in _SKIP_SUBTREES and skip_drawing:
            return
        if el.tag == _local("p") and el is not p:
            return
        if el.tag == _local("t"):
            out.append(el)
            return
        for ch in el:
            walk(ch, skip_drawing)

    in_txbx = _paragraph_inside_txbx_content(p, parent)
    walk(p, skip_drawing=not in_txbx)
    return out


def _consolidate_runs_text(texts: list[ET.Element], new_text: str) -> None:
    """Un solo w:t con el texto completo; el resto vacío. Evita cortes y espacios perdidos."""
    if not texts:
        return
    texts[0].set(f"{{{XML_NS}}}space", "preserve")
    texts[0].text = new_text
    for t in texts[1:]:
        t.text = ""


def replace_placeholders_in_docx_bytes(docx_bytes: bytes, mapping: dict[str, str]) -> bytes:
    """
    mapping: claves con llaves, p. ej. '{t1}' -> '1,890.24'
    Valores vacíos dejan el párrafo sin ese fragmento (sustitución literal).
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
    parent = _build_parent_map(root)
    seen: set[int] = set()
    for p in root.iter(_local("p")):
        pid = id(p)
        if pid in seen:
            continue
        seen.add(pid)
        texts = _collect_w_t_ordered_for_paragraph(p, parent)
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
            _consolidate_runs_text(texts, new)
