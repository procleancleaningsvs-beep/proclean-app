"""MEMO: cierre con dos líneas independientes (Validación / Autorización) en tabla no partible."""

from __future__ import annotations

import re
import unicodedata
from copy import deepcopy

from docx.document import Document as DocumentClass
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from modules.vitroflex_docs.docx_table_workers import find_worker_table

_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _p_plain_text(p_el) -> str:
    return "".join((t.text or "") for t in p_el.findall(".//w:t", _NS))


def _split_labels(val_p_el) -> tuple[str, str]:
    raw = _p_plain_text(val_p_el)
    if "\t" in raw:
        a, b = raw.split("\t", 1)
        return a.strip(), b.strip()
    m = re.search(
        r"(Validaci[oó]n)\s+(Autorizaci[oó]n)",
        raw,
        flags=re.IGNORECASE,
    )
    if m:
        return m.group(1), m.group(2)
    return "Validación", "Autorización"


def _clone_rpr_from_first_text_run(val_p_el) -> OxmlElement:
    for r in val_p_el.iter():
        if r.tag != qn("w:r"):
            continue
        t = r.find(qn("w:t"))
        if t is None or not (t.text or "").strip():
            continue
        rpr = r.find(qn("w:rPr"))
        if rpr is None:
            return OxmlElement("w:rPr")
        return deepcopy(rpr)
    return OxmlElement("w:rPr")


def _paragraph_label_top_rule(text: str, val_p_el) -> OxmlElement:
    """Un párrafo: línea superior (pBdr) + etiqueta centrada."""
    p = OxmlElement("w:p")
    p_pr = OxmlElement("w:pPr")
    old = val_p_el.find(qn("w:pPr"))
    if old is not None:
        st = old.find(qn("w:pStyle"))
        if st is not None:
            p_pr.append(deepcopy(st))

    p_bdr = OxmlElement("w:pBdr")
    top = OxmlElement("w:top")
    top.set(qn("w:val"), "single")
    top.set(qn("w:sz"), "8")
    top.set(qn("w:space"), "2")
    top.set(qn("w:color"), "000000")
    p_bdr.append(top)
    p_pr.append(p_bdr)

    jc = OxmlElement("w:jc")
    jc.set(qn("w:val"), "center")
    p_pr.append(jc)

    sp = OxmlElement("w:spacing")
    sp.set(qn("w:before"), "160")
    p_pr.append(sp)
    p_pr.append(OxmlElement("w:keepLines"))

    p.insert(0, p_pr)

    r = OxmlElement("w:r")
    r.append(_clone_rpr_from_first_text_run(val_p_el))
    wt = OxmlElement("w:t")
    wt.text = text
    r.append(wt)
    p.append(r)
    return p


def _build_two_cell_signature_table(left_p: OxmlElement, right_p: OxmlElement) -> OxmlElement:
    tbl = OxmlElement("w:tbl")
    tbl_pr = OxmlElement("w:tblPr")
    tw = OxmlElement("w:tblW")
    tw.set(qn("w:w"), "5000")
    tw.set(qn("w:type"), "pct")
    tbl_pr.append(tw)
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tbl_pr.append(layout)
    tbl.append(tbl_pr)

    grid = OxmlElement("w:tblGrid")
    g1 = OxmlElement("w:gridCol")
    g1.set(qn("w:w"), "4680")
    g2 = OxmlElement("w:gridCol")
    g2.set(qn("w:w"), "4680")
    grid.append(g1)
    grid.append(g2)
    tbl.append(grid)

    tr = OxmlElement("w:tr")

    for gc_w, child_p in ((4680, left_p), (4680, right_p)):
        tc = OxmlElement("w:tc")
        tc_pr = OxmlElement("w:tcPr")
        tc_w = OxmlElement("w:tcW")
        tc_w.set(qn("w:w"), str(gc_w))
        tc_w.set(qn("w:type"), "dxa")
        tc_pr.append(tc_w)
        tc.append(tc_pr)
        tc.append(child_p)
        tr.append(tc)

    tbl.append(tr)
    return tbl


def memo_wrap_signature_block_in_unsplit_table(doc: DocumentClass) -> None:
    body = doc._element[0]
    sect = body.find(qn("w:sectPr"))
    if sect is None:
        return
    idx = list(body).index(sect)
    if idx < 2:
        return

    val_p_el = None
    val_j = None
    for j in range(idx - 1, max(-1, idx - 12), -1):
        el = body[j]
        if el.tag != qn("w:p"):
            continue
        t = _strip_accents(_p_plain_text(el).lower())
        if "validacion" in t and "autorizacion" in t:
            val_p_el = el
            val_j = j
            break
    if val_p_el is None or val_j is None or val_j < 1:
        return

    line_p_el = body[val_j - 1]
    if line_p_el.tag != qn("w:p"):
        return

    lab_l, lab_r = _split_labels(val_p_el)
    p_left = _paragraph_label_top_rule(lab_l, val_p_el)
    p_right = _paragraph_label_top_rule(lab_r, val_p_el)
    new_tbl = _build_two_cell_signature_table(p_left, p_right)

    pos = list(body).index(line_p_el)
    body.remove(line_p_el)
    body.remove(val_p_el)
    body.insert(pos, new_tbl)


def memo_link_worker_table_to_signature(doc: DocumentClass) -> None:
    """
    Encadena párrafos entre la tabla de trabajadores y el bloque de firmas para que,
    si aún cabe espacio en la página 1, el cierre no salte solo a la página 2.
    """
    body = doc._element[0]
    children = list(body)
    sect_i = None
    for i, el in enumerate(children):
        if el.tag == qn("w:sectPr"):
            sect_i = i
            break
    if sect_i is None:
        return
    tbl_idxs = [i for i, c in enumerate(children[:sect_i]) if c.tag == qn("w:tbl")]
    if len(tbl_idxs) < 2:
        return
    sig_idx = tbl_idxs[-1]
    worker_idx = tbl_idxs[-2]
    for j in range(worker_idx + 1, sig_idx):
        el = children[j]
        if el.tag != qn("w:p"):
            continue
        Paragraph(el, doc._body).paragraph_format.keep_with_next = True

    worker_table = find_worker_table(doc)
    if worker_table is None or not worker_table.rows:
        return
    last_row = worker_table.rows[-1]
    for cell in last_row.cells:
        for p in cell.paragraphs:
            p.paragraph_format.keep_with_next = True
