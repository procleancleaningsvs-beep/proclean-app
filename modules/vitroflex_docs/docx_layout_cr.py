"""CR: tabla de trabajadores con tipografía uniforme en filas de datos y columna IMSS en una línea."""

from __future__ import annotations

from copy import deepcopy

from docx.document import Document as DocumentClass
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from modules.vitroflex_docs.docx_table_workers import find_worker_table, worker_header_row_index

# 16 medios puntos = 8 pt (referencia que mantiene NSS de 11 dígitos en una línea)
_DATA_SZ_HALFPTS = "16"


def _tighten_data_row_paragraph_spacing(table) -> None:
    hi = worker_header_row_index(table)
    for row in table.rows[hi + 1 :]:
        for cell in row.cells:
            for p in cell.paragraphs:
                pf = p.paragraph_format
                pf.space_after = None
                if pf.space_before is not None and pf.space_before.pt > 0:
                    pf.space_before = None


def _nowrap_imss_data_cells(table, imss_col: int = 1) -> None:
    hi = worker_header_row_index(table)
    for row in table.rows[hi + 1 :]:
        if imss_col >= len(row.cells):
            continue
        tc = row.cells[imss_col]._tc
        tc_pr = tc.tcPr
        if tc_pr is None:
            tc_pr = OxmlElement("w:tcPr")
            tc.insert(0, tc_pr)
        if tc_pr.find(qn("w:noWrap")) is None:
            tc_pr.append(OxmlElement("w:noWrap"))


def _data_rows_uniform_cell_margins(table) -> None:
    hi = worker_header_row_index(table)
    for row in table.rows[hi + 1 :]:
        for cell in row.cells:
            tc = cell._tc
            tc_pr = tc.tcPr
            if tc_pr is None:
                tc_pr = OxmlElement("w:tcPr")
                tc.insert(0, tc_pr)
            old = tc_pr.find(qn("w:tcMar"))
            if old is not None:
                tc_pr.remove(old)
            mar = OxmlElement("w:tcMar")
            for side in ("top", "left", "bottom", "right"):
                m = OxmlElement(f"w:{side}")
                m.set(qn("w:w"), "0")
                m.set(qn("w:type"), "dxa")
                mar.append(m)
            tc_pr.append(mar)


def _ppr_set_default_run_font_size(p_pr, sz_half_pts: str) -> None:
    """w:pPr/w:rPr con tamaño uniforme (referencia para clonar en cada w:r)."""
    rpr = p_pr.find(qn("w:rPr"))
    if rpr is None:
        rpr = OxmlElement("w:rPr")
        p_pr.append(rpr)
    sz = rpr.find(qn("w:sz"))
    if sz is None:
        sz = OxmlElement("w:sz")
        rpr.append(sz)
    sz.set(qn("w:val"), sz_half_pts)
    sz_cs = rpr.find(qn("w:szCs"))
    if sz_cs is None:
        sz_cs = OxmlElement("w:szCs")
        rpr.append(sz_cs)
    sz_cs.set(qn("w:val"), sz_half_pts)


def _strip_bold_from_rpr(rpr) -> None:
    for tag in ("w:b", "w:bCs"):
        b = rpr.find(qn(tag))
        if b is not None:
            rpr.remove(b)


def _uniform_data_row_font_size(table, sz_half_pts: str = _DATA_SZ_HALFPTS) -> None:
    """Mismo rPr efectivo en pPr y en cada run (rFonts + sz); todas las columnas de datos."""
    hi = worker_header_row_index(table)
    for row in table.rows[hi + 1 :]:
        for cell in row.cells:
            for p in cell.paragraphs:
                p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p_el = p._p
                p_pr = p_el.pPr
                if p_pr is None:
                    p_pr = OxmlElement("w:pPr")
                    p_el.insert(0, p_pr)
                jc = p_pr.find(qn("w:jc"))
                if jc is None:
                    jc = OxmlElement("w:jc")
                    p_pr.append(jc)
                jc.set(qn("w:val"), "center")
                _ppr_set_default_run_font_size(p_pr, sz_half_pts)
                base_rpr = p_pr.find(qn("w:rPr"))
                if base_rpr is None:
                    continue
                _strip_bold_from_rpr(base_rpr)
                template_rpr = deepcopy(base_rpr)
                for r in p.runs:
                    r_el = r._r
                    old = r_el.rPr
                    if old is not None:
                        r_el.remove(old)
                    rpr_new = deepcopy(template_rpr)
                    r_el.insert(0, rpr_new)


def _widen_imss_column_dxa(
    table,
    imss_col: int = 1,
    nombre_col: int = 0,
    act_col: int = 2,
) -> None:
    tbl = table._tbl
    grid = tbl.find(qn("w:tblGrid"))
    if grid is None:
        return
    gcs = grid.findall(qn("w:gridCol"))
    if len(gcs) <= max(imss_col, nombre_col, act_col):
        return

    def _w(gc):
        w = gc.get(qn("w:w"))
        t = gc.get(qn("w:type"))
        if not w or not w.isdigit():
            return None
        if t not in (None, "dxa"):
            return None
        return int(w)

    in0 = _w(gcs[nombre_col])
    in1 = _w(gcs[imss_col])
    in2 = _w(gcs[act_col])
    if in0 is None or in1 is None or in2 is None:
        return
    take0 = min(1050, in0 - 360)
    take2 = min(250, in2 - 220)
    add = take0 + take2
    if add <= 0:
        return
    n0, n1, n2 = in0 - take0, in1 + add, in2 - take2
    gcs[nombre_col].set(qn("w:w"), str(n0))
    gcs[imss_col].set(qn("w:w"), str(n1))
    gcs[act_col].set(qn("w:w"), str(n2))

    hi = worker_header_row_index(table)
    for row in table.rows[hi:]:
        cells = row.cells
        for idx, new_w in ((nombre_col, n0), (imss_col, n1), (act_col, n2)):
            if idx >= len(cells):
                continue
            tc_pr = cells[idx]._tc.tcPr
            if tc_pr is None:
                continue
            tc_w = tc_pr.find(qn("w:tcW"))
            tw_type = tc_w.get(qn("w:type")) if tc_w is not None else None
            if tc_w is None or (tw_type not in (None, "dxa")):
                continue
            tc_w.set(qn("w:w"), str(new_w))


def _cr_set_fixed_layout_and_total_width(table) -> None:
    tbl_el = table._tbl
    tbl_pr = tbl_el.tblPr
    if tbl_pr is None:
        tbl_pr = OxmlElement("w:tblPr")
        tbl_el.insert(0, tbl_pr)
    layout = tbl_pr.find(qn("w:tblLayout"))
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        tbl_pr.append(layout)
    layout.set(qn("w:type"), "fixed")

    grid = tbl_el.find(qn("w:tblGrid"))
    if grid is None:
        return
    total = 0
    for gc in grid.findall(qn("w:gridCol")):
        w = gc.get(qn("w:w"))
        if w and w.isdigit():
            total += int(w)
    if total <= 0:
        return
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:w"), str(total))
    tbl_w.set(qn("w:type"), "dxa")


def apply_cr_pdf_layout(doc: DocumentClass) -> None:
    """Solo la tabla de trabajadores: fuente uniforme en filas de datos; cuerpo legal intacto."""
    table = find_worker_table(doc)
    if table is None:
        return
    _widen_imss_column_dxa(table)
    _cr_set_fixed_layout_and_total_width(table)
    _nowrap_imss_data_cells(table)
    _data_rows_uniform_cell_margins(table)
    _uniform_data_row_font_size(table)
    _tighten_data_row_paragraph_spacing(table)
