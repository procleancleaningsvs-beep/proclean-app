"""Arma el DOCX final desde plantilla oficial + datos de formulario."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from docx import Document

from modules.vitroflex_docs.dates import mes_nombre, parse_iso_date
from modules.vitroflex_docs.docx_layout_cr import apply_cr_pdf_layout
from modules.vitroflex_docs.docx_layout_memo import (
    memo_link_worker_table_to_signature,
    memo_wrap_signature_block_in_unsplit_table,
)
from modules.vitroflex_docs.docx_replace_body import replace_in_document
from modules.vitroflex_docs.docx_table_workers import fill_worker_table


def _fecha_permiso_es(iso: str | None) -> str:
    d = parse_iso_date(iso) if iso else None
    if not d:
        return ""
    return f"{d.day} de {mes_nombre(d.month)} de {d.year}"


def build_memo_docx_bytes(
    *,
    fecha_texto: str,
    permiso1_iso: str | None,
    permiso2_iso: str | None,
    workers: list[dict],
    template_path: Path,
) -> bytes:
    doc = Document(str(template_path))
    mapping = {
        "{{FECHA}}": fecha_texto or "",
        "{{PERMISO_1}}": _fecha_permiso_es(permiso1_iso),
        "{{PERMISO_2}}": _fecha_permiso_es(permiso2_iso),
        # Etiquetas fijas de la plantilla Word → mismo estilo que el formulario web
        "PERMISO A PARTIR DEL:": "Permiso a partir del:",
        "Y HASTA EL DIA:": "Y hasta el día:",
    }
    replace_in_document(doc, mapping)
    fill_worker_table(doc, workers)
    memo_wrap_signature_block_in_unsplit_table(doc)
    memo_link_worker_table_to_signature(doc)
    out = BytesIO()
    doc.save(out)
    return out.getvalue()


def build_cr_docx_bytes(
    *,
    fecha_texto: str,
    planta: str,
    workers: list[dict],
    template_path: Path,
) -> bytes:
    doc = Document(str(template_path))
    planta_u = (planta or "").strip().upper()
    mapping = {
        "{{FECHA}}": fecha_texto or "",
        "{{PLANTA}}": planta_u,
    }
    replace_in_document(doc, mapping)
    fill_worker_table(doc, workers)
    apply_cr_pdf_layout(doc)
    out = BytesIO()
    doc.save(out)
    return out.getvalue()
