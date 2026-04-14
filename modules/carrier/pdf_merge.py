"""
Ensamble de PDF para expedientes Carrier > Cursos (versión 1 estable).

Rasteriza cada página de entrada a una página carta (612x792 pt), escalando de forma
uniforme y centrada (sin deformar). Acepta PDF e imágenes vía PyMuPDF.

`pdf_pages`: índices 0-based de páginas a incluir, o None = todas.

Versión 2: se puede añadir pipeline vectorial o recortes por región PDF usando los
mismos metadatos en `slots_json`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import fitz  # PyMuPDF


LETTER_W = 612.0
LETTER_H = 792.0


def _insert_pixmap_centered(dst: fitz.Document, pix: fitz.Pixmap) -> None:
    pw, ph = float(pix.width), float(pix.height)
    if pw <= 0 or ph <= 0:
        return
    s = min((LETTER_W * 0.98) / pw, (LETTER_H * 0.98) / ph)
    tw, th = pw * s, ph * s
    x0 = (LETTER_W - tw) / 2.0
    y0 = (LETTER_H - th) / 2.0
    page = dst.new_page(width=LETTER_W, height=LETTER_H)
    page.insert_image(fitz.Rect(x0, y0, x0 + tw, y0 + th), pixmap=pix)


def append_pdf_pages(
    dst: fitz.Document,
    src_path: Path,
    page_indices: Iterable[int] | None,
    render_matrix: fitz.Matrix | None = None,
) -> None:
    mat = render_matrix or fitz.Matrix(2.0, 2.0)
    src = fitz.open(str(src_path))
    try:
        n = src.page_count
        indices = list(page_indices) if page_indices is not None else list(range(n))
        for i in indices:
            if i < 0 or i >= n:
                continue
            page = src.load_page(i)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            _insert_pixmap_centered(dst, pix)
    finally:
        src.close()


def append_two_images_same_page(
    dst: fitz.Document,
    path_left: Path,
    path_right: Path,
    crop_left: tuple[float, float, float, float] | None,
    crop_right: tuple[float, float, float, float] | None,
    render_matrix: fitz.Matrix | None = None,
) -> None:
    """Dos imágenes en una sola página carta (lado a lado), escaladas sin deformar."""
    mat = render_matrix or fitz.Matrix(2.0, 2.0)
    half = LETTER_W / 2.0 - 8.0
    page = dst.new_page(width=LETTER_W, height=LETTER_H)

    def _pix_for(path: Path, crop: tuple[float, float, float, float] | None) -> fitz.Pixmap:
        doc = fitz.open(str(path))
        try:
            pg = doc.load_page(0)
            r = pg.rect
            clip = r
            if crop is not None:
                x0f, y0f, x1f, y1f = crop
                clip = fitz.Rect(
                    r.x0 + x0f * r.width,
                    r.y0 + y0f * r.height,
                    r.x0 + x1f * r.width,
                    r.y0 + y1f * r.height,
                ) & r
            return pg.get_pixmap(matrix=mat, clip=clip, alpha=False)
        finally:
            doc.close()

    pl = _pix_for(path_left, crop_left)
    pr = _pix_for(path_right, crop_right)
    try:
        for pix, x0b in ((pl, 6.0), (pr, LETTER_W / 2 + 2.0)):
            pw, ph = float(pix.width), float(pix.height)
            if pw <= 0 or ph <= 0:
                continue
            s = min(half / pw, (LETTER_H * 0.96) / ph)
            tw, th = pw * s, ph * s
            y0 = (LETTER_H - th) / 2.0
            page.insert_image(fitz.Rect(x0b, y0, x0b + tw, y0 + th), pixmap=pix)
    finally:
        pl = None
        pr = None


def append_image_page(
    dst: fitz.Document,
    image_path: Path,
    crop_rect_norm: tuple[float, float, float, float] | None,
    render_matrix: fitz.Matrix | None = None,
) -> None:
    mat = render_matrix or fitz.Matrix(2.0, 2.0)
    imgdoc = fitz.open(str(image_path))
    try:
        if imgdoc.page_count < 1:
            return
        page = imgdoc.load_page(0)
        r = page.rect
        clip = r
        if crop_rect_norm is not None:
            x0f, y0f, x1f, y1f = crop_rect_norm
            clip = fitz.Rect(
                r.x0 + x0f * r.width,
                r.y0 + y0f * r.height,
                r.x0 + x1f * r.width,
                r.y0 + y1f * r.height,
            )
            clip = clip & r
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        _insert_pixmap_centered(dst, pix)
    finally:
        imgdoc.close()


def build_merged_pdf(
    sources: list[tuple[Any, ...]],
) -> fitz.Document:
    """
    sources: entradas de 4 tuplas (kind, path, pdf_pages|None, crop_norm|None)
      kind: 'pdf' | 'image'
    o una tupla de 5 elementos:
      ('ine_duo', path_izq, path_der, crop_izq, crop_der) — dos imágenes INE en una hoja.
    """
    dst = fitz.open()
    try:
        for item in sources:
            if len(item) == 5 and item[0] == "ine_duo":
                _, pl, pr, crop_l, crop_r = item
                if isinstance(pl, Path) and isinstance(pr, Path) and pl.is_file() and pr.is_file():
                    append_two_images_same_page(dst, pl, pr, crop_l, crop_r)
                continue
            kind, path, pages, crop = item[0], item[1], item[2], item[3]
            if not isinstance(path, Path) or not path.is_file():
                continue
            if kind == "pdf":
                append_pdf_pages(dst, path, pages)
            elif kind == "image":
                append_image_page(dst, path, crop)
        return dst
    except Exception:
        dst.close()
        raise


def write_merged_pdf(
    sources: list[tuple[Any, ...]],
    out_path: Path,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = build_merged_pdf(sources)
    try:
        doc.save(str(out_path))
    finally:
        doc.close()
