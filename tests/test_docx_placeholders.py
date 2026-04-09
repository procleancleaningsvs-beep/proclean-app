"""Reemplazo de placeholders en DOCX sin colapsar runs."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from modules.finiquitos.docx_placeholders import _replace_in_xml_tree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _w(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


class TestDocxPlaceholdersSpan(unittest.TestCase):
    def test_placeholder_en_varios_w_t_conserva_runs(self):
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="{W_NS}">
  <w:p>
    <w:r><w:rPr><w:b/></w:rPr><w:t>{{x</w:t></w:r>
    <w:r><w:t>xx}}</w:t></w:r>
    <w:r><w:t> fijo</w:t></w:r>
  </w:p>
</w:document>"""
        root = ET.fromstring(xml)
        _replace_in_xml_tree(root, {"{xxx}": "VAL"})
        texts = [t.text or "" for t in root.iter(_w("t"))]
        self.assertEqual(texts, ["VAL", "", " fijo"])
        bold_runs = [r for r in root.iter(_w("r")) if r.find(_w("rPr")) is not None and r.find(_w("rPr")).find(_w("b")) is not None]
        self.assertEqual(len(bold_runs), 1)
        self.assertEqual((bold_runs[0].find(_w("t")).text or ""), "VAL")

    def test_mismo_run_reemplazo_parcial(self):
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="{W_NS}">
  <w:p><w:r><w:t>pre{{k}}post</w:t></w:r></w:p>
</w:document>"""
        root = ET.fromstring(xml)
        _replace_in_root = _replace_in_xml_tree
        _replace_in_root(root, {"{k}": "Z"})
        t = next(root.iter(_w("t")))
        self.assertEqual(t.text, "preZpost")


if __name__ == "__main__":
    unittest.main()
