"""
Evidencia comparativa (A plantilla original → PDF, B/C generado → PDF + renders).

Requiere:
  - vitroflex_templates/*.docx oficiales
  - LibreOffice

Salida: tests/evidence_vitroflex/compare/
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "evidence_vitroflex" / "compare"


def main() -> int:
    sys.path.insert(0, str(ROOT))
    import fitz

    from modules.vitroflex_docs.build_document import build_memo_docx_bytes
    from modules.vitroflex_docs.libreoffice_pdf import docx_to_pdf, resolve_soffice_path
    from modules.vitroflex_docs.template_paths import MEMO_DOCX

    if not resolve_soffice_path():
        print("Sin LibreOffice.")
        return 2
    if not MEMO_DOCX.is_file():
        print("Sin plantilla MEMO:", MEMO_DOCX)
        return 2

    OUT.mkdir(parents=True, exist_ok=True)

    # A) Plantilla original → PDF (sin tocar contenido)
    ref_pdf = OUT / "A_plantilla_memo_original.pdf"
    docx_to_pdf(MEMO_DOCX, ref_pdf)

    # B) Generado con datos de prueba
    docx_bytes = build_memo_docx_bytes(
        fecha_texto="[E2E compare]",
        permiso1_iso="2026-03-10",
        permiso2_iso="2026-04-27",
        workers=[
            {"nombre": "Nombre Uno", "imss": "11111111111", "actividad": "Act.", "tel": "8111111111"},
        ],
        template_path=MEMO_DOCX,
    )
    tmp_docx = OUT / "_tmp_gen.docx"
    tmp_docx.write_bytes(docx_bytes)
    gen_pdf = OUT / "B_memo_generado.pdf"
    docx_to_pdf(tmp_docx, gen_pdf)
    tmp_docx.unlink(missing_ok=True)

    def render_pdf(path: Path, prefix: str) -> None:
        doc = fitz.open(str(path))
        try:
            pix = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            pix.save(str(OUT / f"{prefix}_pagina1.png"))
        finally:
            doc.close()

    render_pdf(ref_pdf, "A_plantilla")
    render_pdf(gen_pdf, "B_generado")

    print("OK:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
