"""Genera 05_cr_mercado_moderno.pdf y 07_caso_multiples_hojas.pdf + capturas."""
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules.vitroflex_docs.build_document import build_cr_docx_bytes, build_memo_docx_bytes
from modules.vitroflex_docs.libreoffice_pdf import docx_bytes_to_pdf_bytes
from modules.vitroflex_docs.template_paths import CR_DOCX, MEMO_DOCX

OUT = ROOT / "tests" / "evidence_vitroflex" / "real_run" / "pdfs"
IMG = ROOT / "tests" / "evidence_vitroflex" / "real_run" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)
IMG.mkdir(parents=True, exist_ok=True)

workers = [
    {"nombre": "Ana López Martínez", "imss": "12345678901", "actividad": "Aux.", "tel": "81 2183 9413"},
    {"nombre": "Luis Hernández", "imss": "10987654321", "actividad": "Aux.", "tel": "81 2183 9413"},
]
big = [
    {
        "nombre": f"Trabajador {i+1}",
        "imss": f"{10000000000 + i:011d}",
        "actividad": "Aux. de limpieza",
        "tel": "81 2183 9413",
    }
    for i in range(29)
]

b05 = build_cr_docx_bytes(
    fecha_texto="Fecha CR Mercado Moderno",
    planta="Mercado Moderno",
    workers=workers,
    template_path=CR_DOCX,
)
b07 = build_memo_docx_bytes(
    fecha_texto="Caso multipágina",
    permiso1_iso="2026-01-01",
    permiso2_iso="2026-02-18",
    workers=big,
    template_path=MEMO_DOCX,
)

pdf05 = docx_bytes_to_pdf_bytes(b05, suffix="05")
pdf07 = docx_bytes_to_pdf_bytes(b07, suffix="07")
OUT.joinpath("05_cr_mercado_moderno.pdf").write_bytes(pdf05)
OUT.joinpath("07_caso_multiples_hojas.pdf").write_bytes(pdf07)

d = fitz.open(stream=pdf05, filetype="pdf")
p = d[0]
r = p.rect
clip = fitz.Rect(0, r.height * 0.30, r.width, r.height * 0.66)
pix = p.get_pixmap(matrix=fitz.Matrix(2.75, 2.75), clip=clip, alpha=False)
pix.save(str(IMG / "05_tabla_uniforme_completa.png"))
d.close()

d2 = fitz.open(stream=pdf07, filetype="pdf")
pl = d2[-1]
pix2 = pl.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
pix2.save(str(IMG / "07_cierre_dos_lineas.png"))
d2.close()

print("OK", OUT / "05_cr_mercado_moderno.pdf", OUT / "07_caso_multiples_hojas.pdf")
