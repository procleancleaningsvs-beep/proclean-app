"""Conversión DOCX → PDF mediante LibreOffice (misma línea que start_local.bat)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def resolve_soffice_path() -> str | None:
    env = (os.environ.get("PROCLEAN_LIBREOFFICE") or "").strip()
    if env and Path(env).is_file():
        return env
    for candidate in (
        Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
        Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("soffice") or shutil.which("soffice.exe")


def docx_to_pdf(docx_path: Path, pdf_path: Path, *, timeout_sec: int = 180) -> None:
    """
    Convierte un .docx a .pdf usando LibreOffice en modo headless.
    """
    soffice = resolve_soffice_path()
    if not soffice:
        raise RuntimeError(
            "No se encontró LibreOffice (soffice). Instálalo o define PROCLEAN_LIBREOFFICE "
            "con la ruta a soffice.exe (ver start_local.bat)."
        )

    docx_path = docx_path.resolve()
    outdir = pdf_path.parent.resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    # LibreOffice nombra el PDF igual que el docx de entrada
    expected = outdir / f"{docx_path.stem}.pdf"

    cmd = [
        soffice,
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--convert-to",
        "pdf",
        "--outdir",
        str(outdir),
        str(docx_path),
    ]
    try:
        subprocess.run(
            cmd,
            check=True,
            timeout=timeout_sec,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or "").strip() or str(exc)
        raise RuntimeError(f"LibreOffice falló al convertir a PDF: {err}") from exc

    if not expected.is_file():
        raise RuntimeError(f"LibreOffice no generó el PDF esperado en {expected}")

    if expected.resolve() != pdf_path.resolve():
        pdf_path.unlink(missing_ok=True)
        expected.rename(pdf_path)


def _safe_temp_docx_stem(stem: str, *, fallback: str = "vitroflex", max_len: int = 120) -> str:
    """Nombre base seguro para archivos temporales DOCX/PDF (LibreOffice usa el mismo stem)."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (stem or "").strip())
    s = s.strip(" ._") or fallback
    return s[:max_len]


def docx_bytes_to_pdf_bytes(
    docx_bytes: bytes,
    *,
    suffix: str = "vitroflex",
    pdf_stem: str | None = None,
) -> bytes:
    """Escribe temporal DOCX, convierte, lee PDF."""
    stem = (
        _safe_temp_docx_stem(pdf_stem, fallback="finiquito")
        if pdf_stem
        else _safe_temp_docx_stem(suffix, fallback=suffix)
    )
    with tempfile.TemporaryDirectory(prefix="proclean_pdf_") as tmp:
        tdir = Path(tmp)
        docx = tdir / f"{stem}.docx"
        pdf = tdir / f"{stem}.pdf"
        docx.write_bytes(docx_bytes)
        docx_to_pdf(docx, pdf)
        return pdf.read_bytes()
