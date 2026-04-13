"""One-off: print exact XML substrings to embed in finiquito_template_patch.py."""
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
x = zipfile.ZipFile(root / "docx_templates" / "FINIQUITO FORMATO.docx").read("word/document.xml").decode("utf-8")

# Row2: from <w:t>3</w:t> through end of día runs (before t2 cell opens)
a = x.find("<w:t>3</w:t>")
b = x.find("{t2}")
chunk = x[a : b]
print("=== LEN", len(chunk))
print(repr(chunk[:200]))
print("...")
print(repr(chunk[-200:]))

# p2_nom replacement target: multi-run concept cell only (between tc open and </w:tc> before amount)
# Find start of concept cell for row2: after a, find second <w:tc>
i = chunk.find("<w:tc><w:tcPr><w:tcW w:w=\"3042\"")
j = chunk.rfind("</w:tc>")
cell2 = chunk[i:j]
print("=== CELL2 LEN", len(cell2))
print(repr(cell2[:300]))
