import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
x = zipfile.ZipFile(root / "docx_templates" / "FINIQUITO FORMATO.docx").read("word/document.xml").decode("utf-8")
start = x.find('<w:p w14:paraId="18B298EA"')
t5 = x.find("{t5}")
end_close = "</w:p></w:tc>"
end_i = x.rfind(end_close, 0, t5) + len(end_close)
chunk = x[start:end_i]
Path(root / "modules/finiquitos/_snippet_row4_concept.xml").write_text(chunk, encoding="utf-8")
print("len", len(chunk))
