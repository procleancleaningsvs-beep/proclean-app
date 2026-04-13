"""Parches de plantilla FINIQUITO (strings XML) — regresiones mínimas."""

from __future__ import annotations

import unittest

from modules.finiquitos.finiquito_template_patch import (
    _patch_desglose_spacer_row_height,
    _patch_footer_drawing_not_behind_document,
    _patch_header_logo_src_rect,
    _patch_neto_a_pagar_inner_table_prevent_wrap,
    _patch_pg_mar_footer_gap,
)


class TestTemplatePatchUnits(unittest.TestCase):
    def test_src_rect_se_anula(self):
        s = '<a:blip r:embed="rId1"/><a:srcRect l="5393" r="4439"/><a:stretch>'
        o = _patch_header_logo_src_rect(s)
        self.assertIn('l="0" t="0" r="0" b="0"', o)
        self.assertNotIn('l="5393"', o)

    def test_desglose_spacer_from_2986(self):
        s = (
            '<w:tr><w:trPr><w:trHeight w:val="2986"/></w:trPr>'
            '<w:tc><w:tcPr><w:tcW w:w="981" w:type="dxa"/></w:tcPr></w:tc></w:tr>'
        )
        o = _patch_desglose_spacer_row_height(s)
        self.assertIn('w:hRule="atLeast"', o)
        self.assertIn('w:val="2080"', o)
        self.assertNotIn("2986", o)

    def test_desglose_spacer_upgrades_1780(self):
        s = (
            '<w:tr><w:trPr><w:trHeight w:val="1780" w:hRule="atLeast"/></w:trPr>'
            '<w:tc><w:tcPr><w:tcW w:w="981" w:type="dxa"/></w:tcPr></w:tc></w:tr>'
        )
        o = _patch_desglose_spacer_row_height(s)
        self.assertIn('w:val="2080"', o)

    def test_neto_inner_table_widen_and_tab(self):
        s = (
            '<w:tbl>'
            '<w:tblGrid><w:gridCol w:w="5778"/><w:gridCol w:w="4819"/></w:tblGrid>'
            '<w:tr><w:tc><w:tcPr><w:tcW w:w="5778"/></w:tcPr><w:p><w:t>{suma_p}</w:t></w:p></w:tc>'
            '<w:tc><w:tcPr><w:tcW w:w="4819"/></w:tcPr></w:tc></w:tr>'
            '<w:tr><w:tc><w:tcPr><w:tcW w:w="5778"/></w:tcPr></w:tc>'
            '<w:tc><w:tcPr><w:tcW w:w="4819"/></w:tcPr>'
            '<w:p w14:paraId="09F3A52C"><w:pPr><w:pStyle w:val="TableParagraph"/>'
            '<w:tabs><w:tab w:val="left" w:pos="4320"/></w:tabs><w:ind w:right="36"/>'
            '</w:pPr></w:p></w:tc></w:tr>'
            "</w:tbl>"
        )
        o = _patch_neto_a_pagar_inner_table_prevent_wrap(s)
        self.assertIn('w:w="7197"', o)
        self.assertIn('w:pos="2600"', o)
        self.assertNotIn('w:pos="4320"', o)
        self.assertNotIn('w:w="4819"', o)

    def test_footer_margin(self):
        s = '<w:pgMar w:top="1" w:footer="827" w:left="0"/>'
        o = _patch_pg_mar_footer_gap(s)
        self.assertIn('w:footer="1120"', o)

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
