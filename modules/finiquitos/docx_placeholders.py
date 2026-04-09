"""
Reemplazo de placeholders en DOCX sin recrear documento.

Sustituye solo el tramo de texto de cada placeholder en los w:t que lo intersectan,
conservando runs, w:tab (en sus propios w:r) y formato fuera del placeholder.

Evita repartir el párrafo entero en un solo w:t (eso destruía negritas/tabs en el template).
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


def _maybe_preserve_space(t: ET.Element) -> None:
    tx = t.text or ""
    if tx and (tx[0].isspace() or tx[-1].isspace()):
        t.set(f"{{{XML_NS}}}space", "preserve")


def _apply_placeholder_span(
    texts: list[ET.Element],
    start: int,
    end: int,
    value: str,
) -> None:
    """Sustituye full[start:end] por value; solo modifica w:t que intersectan [start,end)."""
    if start >= end:
        return
    cum = 0
    overlaps: list[tuple[int, str, int, int]] = []
    for idx, t in enumerate(texts):
        s = t.text or ""
        L = len(s)
        rs, re = cum, cum + L
        if re > start and rs < end:
            o_start = max(0, start - rs)
            o_end_excl = min(L, end - rs)
            overlaps.append((idx, s, o_start, o_end_excl))
        cum += L
    if not overlaps:
        return
    i, s_i, oi0, _ = overlaps[0]
    j, s_j, _, oj_excl = overlaps[-1]
    if i == j:
        texts[i].text = s_i[:oi0] + value + s_j[oj_excl:]
        _maybe_preserve_space(texts[i])
        return
    texts[i].text = s_i[:oi0] + value
    for k in range(i + 1, j):
        texts[k].text = ""
    texts[j].text = s_j[oj_excl:]
    _maybe_preserve_space(texts[i])
    _maybe_preserve_space(texts[j])


def _replace_placeholders_in_text_nodes(texts: list[ET.Element], mapping: dict[str, str]) -> None:
    """Aplica todas las sustituciones; en cada paso, la coincidencia más a la izquierda y la clave más larga."""
    keys = [k for k in mapping if k]
    max_rounds = 3000
    for _ in range(max_rounds):
        full = "".join(t.text or "" for t in texts)
        best: tuple[int, int, str] | None = None
        for k in keys:
            pos = full.find(k)
            if pos < 0:
                continue
            cand = (pos, -len(k), k)
            if best is None or cand < best:
                best = cand
        if best is None:
            break
        pos, _, k = best
        _apply_placeholder_span(texts, pos, pos + len(k), mapping[k])


def replace_placeholders_in_docx_bytes(docx_bytes: bytes, mapping: dict[str, str]) -> bytes:
    """
    mapping: claves con llaves, p. ej. '{t1}' -> '1,890.24'
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
        needs = any(k in full for k in mapping)
        if needs:
            _replace_placeholders_in_text_nodes(texts, mapping)
