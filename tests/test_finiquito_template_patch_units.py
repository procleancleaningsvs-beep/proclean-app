"""Parches de plantilla FINIQUITO (strings XML) — regresiones mínimas."""

from __future__ import annotations

import unittest

from modules.finiquitos.finiquito_template_patch import (
    _patch_footer_drawing_not_behind_document,
    _patch_header_logo_src_rect,
    _patch_neto_totals_row_and_grid,
    _patch_pg_mar_footer_gap,
    _patch_table_spacer_row,
)


class TestTemplatePatchUnits(unittest.TestCase):
    def test_src_rect_se_anula(self):
        s = '<a:blip r:embed="rId1"/><a:srcRect l="5393" r="4439"/><a:stretch>'
        o = _patch_header_logo_src_rect(s)
        self.assertIn('l="0" t="0" r="0" b="0"', o)
        self.assertNotIn('l="5393"', o)

    def test_spacer_row_2986(self):
        s = '<w:tr><w:trPr><w:trHeight w:val="2986"/></w:trPr></w:tr>'
        o = _patch_table_spacer_row(s)
        self.assertIn('w:hRule="atLeast"', o)
        self.assertIn('w:val="1780"', o)
        self.assertNotIn("2986", o)

    def test_spacer_row_upgrades_1520(self):
        s = '<w:tr><w:trPr><w:trHeight w:val="1520" w:hRule="atLeast"/></w:trPr></w:tr>'
        o = _patch_table_spacer_row(s)
        self.assertIn('w:val="1780"', o)

    def test_footer_margin(self):
        s = '<w:pgMar w:top="1" w:footer="827" w:left="0"/>'
        o = _patch_pg_mar_footer_gap(s)
        self.assertIn('w:footer="1120"', o)

    def test_neto_row_widen_two_column(self):
        s = (
            "<w:tbl><w:tblGrid><w:gridCol w:w=\"5778\"/><w:gridCol w:w=\"4819\"/></w:tblGrid>"
            "<w:tr><w:tc><w:tcPr><w:tcW w:w=\"5778\" w:type=\"dxa\"/></w:tcPr></w:tc>"
            "<w:tc><w:tcPr><w:tcW w:w=\"4819\" w:type=\"dxa\"/></w:tcPr>"
            "<w:p w14:paraId=\"09F3A52C\"><w:pPr><w:pStyle w:val=\"TableParagraph\"/></w:pPr></w:p>"
            "</w:tc></w:tr></w:tbl>"
        )
        o = _patch_neto_totals_row_and_grid(s)
        self.assertIn('w:w="4097"', o)
        self.assertIn('w:w="6500"', o)
        self.assertNotIn('w:w="4819"', o)

    def test_footer_anchor_behind_doc_off(self):
        s = (
            '<wp:anchor distT="0" behindDoc="1" locked="0">'
            '<wp:anchor distT="0" behindDoc="1" locked="0">'
        )
        o = _patch_footer_drawing_not_behind_document(s)
        self.assertEqual(o.count('behindDoc="0"'), 2)
        self.assertNotIn('behindDoc="1"', o)


if __name__ == "__main__":
    unittest.main()
