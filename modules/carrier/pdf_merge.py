"""
Ensamble de PDF para expedientes Carrier > Cursos.

Objetivo principal: buena legibilidad administrativa con peso razonable.
- Páginas PDF se insertan en vector (sin rasterizar) cuando es posible.
- Imágenes se rasterizan a resolución moderada y se embeben en JPEG.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import fitz  # PyMuPDF


LETTER_W = 612.0
LETTER_H = 792.0
PDF_INSET = 0.0
IMG_INSET = 8.0
IMAGE_BASE_MATRIX = fitz.Matrix(1.35, 1.35)
INE_BASE_MATRIX = fitz.Matrix(1.45, 1.45)
JPEG_QUALITY = 72


def _fit_rect(
    src_w: float,
    src_h: float,
    *,
    inset: float,
    scale_mult: float,
    x_bias: float = 0.0,
) -> fitz.Rect:
    usable_w = max(10.0, LETTER_W - inset * 2.0)
    usable_h = max(10.0, LETTER_H - inset * 2.0)
    sm = max(0.2, min(3.0, float(scale_mult)))
    s = min(usable_w / max(1.0, src_w), usable_h / max(1.0, src_h)) * sm
    tw = min(LETTER_W - inset * 2.0, src_w * s)
    th = min(LETTER_H - inset * 2.0, src_h * s)
    x0 = (LETTER_W - tw) / 2.0 + x_bias
    y0 = (LETTER_H - th) / 2.0
    x0 = max(inset, min(LETTER_W - inset - tw, x0))
    y0 = max(inset, min(LETTER_H - inset - th, y0))
    return fitz.Rect(x0, y0, x0 + tw, y0 + th)


def _jpeg_stream_from_pixmap(pix: fitz.Pixmap) -> bytes:
    try:
        return pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY)
    except TypeError:
        try:
            return pix.tobytes("jpeg")
        except Exception:
            return pix.tobytes("jpg")


def append_pdf_pages(
    dst: fitz.Document,
    src_path: Path,
    page_indices: Iterable[int] | None,
    *,
    scale_mult: float = 1.0,
) -> None:
    """Inserta páginas PDF manteniéndolas en vector (muy liviano vs raster)."""
    src = fitz.open(str(src_path))
    try:
        n = src.page_count
        indices = list(page_indices) if page_indices is not None else list(range(n))
        for i in indices:
            if i < 0 or i >= n:
                continue
            sp = src.load_page(i)
            r = sp.rect
            page = dst.new_page(width=LETTER_W, height=LETTER_H)
            to_rect = _fit_rect(r.width, r.height, inset=PDF_INSET, scale_mult=scale_mult)
            page.show_pdf_page(to_rect, src, i, keep_proportion=True, overlay=True)
    finally:
        src.close()


def append_two_images_same_page(
    dst: fitz.Document,
    path_left: Path,
    path_right: Path,
    crop_left: tuple[float, float, float, float] | None,
    crop_right: tuple[float, float, float, float] | None,
) -> None:
    """Dos imágenes en una hoja carta (lado a lado), embebidas como JPEG."""
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
            return pg.get_pixmap(matrix=INE_BASE_MATRIX, clip=clip, alpha=False)
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
            rect = fitz.Rect(x0b, y0, x0b + tw, y0 + th)
            page.insert_image(rect, stream=_jpeg_stream_from_pixmap(pix))
    finally:
        pl = None
        pr = None


def append_image_page(
    dst: fitz.Document,
    image_path: Path,
    crop_rect_norm: tuple[float, float, float, float] | None,
    *,
    scale_mult: float = 1.0,
) -> None:
    imgdoc = fitz.open(str(image_path))
    try:
        if imgdoc.page_count < 1:
            return
        sp = imgdoc.load_page(0)
        r = sp.rect
        clip = r
        if crop_rect_norm is not None:
            x0f, y0f, x1f, y1f = crop_rect_norm
            clip = fitz.Rect(
                r.x0 + x0f * r.width,
                r.y0 + y0f * r.height,
                r.x0 + x1f * r.width,
                r.y0 + y1f * r.height,
            ) & r
        pix = sp.get_pixmap(matrix=IMAGE_BASE_MATRIX, clip=clip, alpha=False)
        pw, ph = float(pix.width), float(pix.height)
        if pw <= 0 or ph <= 0:
            return
        page = dst.new_page(width=LETTER_W, height=LETTER_H)
        to_rect = _fit_rect(pw, ph, inset=IMG_INSET, scale_mult=scale_mult)
        page.insert_image(to_rect, stream=_jpeg_stream_from_pixmap(pix))
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
            scale = 1.0
            if len(item) >= 5 and item[4] is not None:
                try:
                    scale = max(0.2, min(3.0, float(item[4])))
                except (TypeError, ValueError):
                    scale = 1.0
            if not isinstance(path, Path) or not path.is_file():
                continue
            if kind == "pdf":
                append_pdf_pages(dst, path, pages, scale_mult=scale)
            elif kind == "image":
                append_image_page(dst, path, crop, scale_mult=scale)
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
        doc.save(
            str(out_path),
            garbage=3,
            deflate=True,
            deflate_images=True,
            deflate_fonts=True,
            use_objstms=1,
            pretty=False,
        )
    finally:
        doc.close()
