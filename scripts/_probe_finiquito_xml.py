import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
z = zipfile.ZipFile(root / "docx_templates" / "FINIQUITO FORMATO.docx")
x = z.read("word/document.xml").decode("utf-8")
z.close()
needle = '><w:t>1</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:tcW w:w="3042"'
print("needle count", x.count(needle))
i = x.find("{t1}")
print("t1 idx", i)
print(repr(x[i - 400 : i + 30]))

n = '<w:t>1</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:tcW w:w="3042" w:type="dxa"'
print("anchor2 count", x.count(n))
print("r2", x.count('<w:t>3</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:tcW w:w="3042" w:type="dxa"'))
for label, needle in (
    ("r3", '<w:t>19</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:tcW w:w="3042" w:type="dxa"'),
    ("r4", '<w:t>22</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:tcW w:w="3042" w:type="dxa"'),
    ("r5", '<w:t>24</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:tcW w:w="3042" w:type="dxa"'),
):
    print(label, x.count(needle))
