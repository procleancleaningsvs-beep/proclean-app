"""
Regenera los 3 PDFs de prueba y guarda recortes PNG para revisión visual.
Salida: tests/evidence_vitroflex/real_run/screenshots/
"""

from __future__ import annotations

import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "tests" / "evidence_vitroflex" / "real_run"
PDF_DIR = OUT_DIR / "pdfs"
IMG_DIR = OUT_DIR / "screenshots"


def _clip_page(pdf: fitz.Document, pi: int, y0: float, y1: float, dest: Path, zoom: float = 2.75) -> None:
    page = pdf[pi]
    r = page.rect
    clip = fitz.Rect(0, r.height * y0, r.width, r.height * y1)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    dest.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(dest))


def main() -> int:
    sys.path.insert(0, str(ROOT))
    import os

    os.chdir(ROOT)

    from modules.vitroflex_docs.build_document import build_cr_docx_bytes, build_memo_docx_bytes
    from modules.vitroflex_docs.libreoffice_pdf import docx_bytes_to_pdf_bytes
    from modules.vitroflex_docs.template_paths import CR_DOCX, MEMO_DOCX

    workers3 = [
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

    PDF_DIR.mkdir(parents=True, exist_ok=True)

    p05 = PDF_DIR / "05_cr_mercado_moderno.pdf"
    p06 = PDF_DIR / "06_caso_una_hoja.pdf"
    p07 = PDF_DIR / "07_caso_multiples_hojas.pdf"

    p05.write_bytes(
        docx_bytes_to_pdf_bytes(
            build_cr_docx_bytes(
                fecha_texto="Fecha CR Mercado Moderno",
                planta="Mercado Moderno",
                workers=workers3,
                template_path=CR_DOCX,
            ),
            suffix="05_cr",
        )
    )
    p06.write_bytes(
        docx_bytes_to_pdf_bytes(
            build_memo_docx_bytes(
                fecha_texto="Caso pocas filas",
                permiso1_iso="2026-03-01",
                permiso2_iso="2026-04-18",
                workers=workers3,
                template_path=MEMO_DOCX,
            ),
            suffix="06_memo",
        )
    )
    p07.write_bytes(
        docx_bytes_to_pdf_bytes(
            build_memo_docx_bytes(
                fecha_texto="Caso multipágina",
                permiso1_iso="2026-01-01",
                permiso2_iso="2026-02-18",
                workers=big,
                template_path=MEMO_DOCX,
            ),
            suffix="07_memo",
        )
    )

    d07 = fitz.open(str(p07))
    last = len(d07) - 1
    # Última hoja: suele ser solo el cierre; captura completa para evitar recortes vacíos.
    p_last = d07[last]
    pix_last = p_last.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    pix_last.save(str(IMG_DIR / "07_firma_misma_pagina.png"))
    # Filas de datos: en este caso todas caben en la página 1; se recorta zona media-inferior.
    _clip_page(d07, 0, 0.42, 0.88, IMG_DIR / "07_tabla_filas_datos.png")
    d07.close()

    d06 = fitz.open(str(p06))
    _clip_page(d06, 0, 0.20, 0.82, IMG_DIR / "06_tabla_contorno.png")
    d06.close()

    d05 = fitz.open(str(p05))
    _clip_page(d05, 0, 0.36, 0.74, IMG_DIR / "05_tabla_imss_una_linea.png")
    d05.close()

    print("PDFs:", p05, p06, p07, sep="\n")
    print("PNG:", IMG_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
