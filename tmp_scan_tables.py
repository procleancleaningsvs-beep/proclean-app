"""Scan document.xml for trHeight and table context (remove after use)."""
from pathlib import Path
from io import BytesIO
import zipfile
import re

from modules.finiquitos.finiquito_template_patch import patch_finiquito_docx_template_bytes

raw = Path("docx_templates/FINIQUITO FORMATO.docx").read_bytes()
d = zipfile.ZipFile(BytesIO(patch_finiquito_docx_template_bytes(raw))).read("word/document.xml").decode("utf-8")
print("2986 count", d.count("2986"), "1780", d.count("1780"), "1520", d.count("1520"))

trs = d.split("</w:tr>")
for i, tr in enumerate(trs):
    if "trHeight" not in tr:
        continue
    tr = tr + "</w:tr>"
    h = re.search(r"<w:trHeight[^>]+/>", tr)
    texts = " ".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", tr)[:12])
    print(i, h.group(0) if h else "?", texts[:140])

print("count trHeight", len(re.findall(r"w:trHeight", d)))
print("Sueldo idx", d.find("Sueldo"))
for label in ("Suma", "Percepciones", "Deducciones", "Neto a Pagar", "Neto"):
    print(label, d.find(label))
print("Neto idx", d.find(">Neto<"))
