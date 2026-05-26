"""Conversión DOCX → PDF mediante LibreOffice (misma línea que start_local.bat)."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_PDF_EXPORT_FILTER = "pdf:writer_pdf_Export"


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


def _build_conversion_command(
    soffice: str,
    *,
    profile_uri: str,
    outdir: Path,
    docx_path: Path,
) -> list[str]:
    return [
        soffice,
        "--headless",
        "--nologo",
        "--nofirststartwizard",
        "--norestore",
        "--nodefault",
        "--nolockcheck",
        f"-env:UserInstallation={profile_uri}",
        "--convert-to",
        _PDF_EXPORT_FILTER,
        "--outdir",
        str(outdir),
        str(docx_path),
    ]


def docx_to_pdf(docx_path: Path, pdf_path: Path, *, timeout_sec: int = 180) -> None:
    """
    Convierte un .docx a .pdf usando LibreOffice en modo headless con perfil aislado.
    """
    soffice = resolve_soffice_path()
    if not soffice:
        raise RuntimeError(
            "No se encontró LibreOffice (soffice). Instálalo o define PROCLEAN_LIBREOFFICE "
            "con la ruta a soffice.exe (ver start_local.bat)."
        )

    docx_path = docx_path.resolve()
    if not docx_path.is_file():
        raise FileNotFoundError(f"No existe el DOCX de entrada: {docx_path}")

    outdir = pdf_path.parent.resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    expected = outdir / f"{docx_path.stem}.pdf"
    profile_dir = Path(tempfile.mkdtemp(prefix=f"lo-profile-{uuid.uuid4().hex}-"))
    profile_uri = profile_dir.as_uri()

    cmd = _build_conversion_command(
        soffice,
        profile_uri=profile_uri,
        outdir=outdir,
        docx_path=docx_path,
    )

    try:
        logger.debug("LibreOffice DOCX→PDF: %s", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                check=True,
                timeout=timeout_sec,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or "").strip() if exc.stdout else ""
            stderr = (exc.stderr or "").strip() if exc.stderr else ""
            logger.error(
                "LibreOffice timeout (%ss) docx=%s stdout=%r stderr=%r",
                timeout_sec,
                docx_path,
                stdout,
                stderr,
            )
            raise RuntimeError(
                f"LibreOffice excedió el tiempo límite ({timeout_sec}s) al convertir a PDF"
            ) from exc
        except subprocess.CalledProcessError as exc:
            err = (exc.stderr or exc.stdout or "").strip() or str(exc)
            logger.error(
                "LibreOffice falló docx=%s returncode=%s stdout=%r stderr=%r",
                docx_path,
                exc.returncode,
                (exc.stdout or "").strip(),
                (exc.stderr or "").strip(),
            )
            raise RuntimeError(f"LibreOffice falló al convertir a PDF: {err}") from exc
        else:
            if (result.stdout or "").strip():
                logger.debug("LibreOffice stdout: %s", result.stdout.strip())
            if (result.stderr or "").strip():
                logger.debug("LibreOffice stderr: %s", result.stderr.strip())

        if not expected.is_file():
            raise RuntimeError(f"LibreOffice no generó el PDF esperado en {expected}")

        pdf_size = expected.stat().st_size
        if pdf_size <= 0:
            raise RuntimeError(f"LibreOffice generó un PDF vacío en {expected}")

        if expected.resolve() != pdf_path.resolve():
            pdf_path.unlink(missing_ok=True)
            expected.rename(pdf_path)

        final_size = pdf_path.stat().st_size
        if final_size <= 0:
            raise RuntimeError(f"El PDF resultante está vacío: {pdf_path}")

        logger.info(
            "LibreOffice DOCX→PDF OK docx=%s pdf=%s bytes=%s",
            docx_path.name,
            pdf_path.name,
            final_size,
        )
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)


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
