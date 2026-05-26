#!/usr/bin/env python3
"""
Diagnóstico del motor DOCX→PDF (LibreOffice + fontconfig).

Uso (desde la raíz del repo):
  python scripts/check_pdf_engine.py
  python scripts/check_pdf_engine.py --convert docx_templates/FINIQUITO\\ FORMATO.docx

No afecta producción ni UI; solo imprime información en consola.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FONT_FAMILIES = (
    "Calibri",
    "Arial",
    "Times New Roman",
    "Cambria",
    "Aptos",
    "Carlito",
    "Caladea",
    "Liberation Sans",
    "Liberation Serif",
)


def _run(cmd: list[str], *, timeout: int = 30) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", f"comando no encontrado: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout ({timeout}s): {' '.join(cmd)}"


def _print_section(title: str) -> None:
    print()
    print(f"=== {title} ===")


def _print_version(label: str, cmd: list[str]) -> None:
    code, out, err = _run(cmd)
    text = out or err or "(sin salida)"
    status = "OK" if code == 0 else f"exit {code}"
    print(f"{label}: {status}")
    print(text)


def _print_fc_match(family: str) -> None:
    code, out, err = _run(["fc-match", family])
    line = out or err or "(sin salida)"
    status = "OK" if code == 0 else f"exit {code}"
    print(f"fc-match {family!r}: {status} -> {line}")


def _print_font_packages() -> None:
    if not shutil.which("dpkg-query"):
        print("dpkg-query no disponible (omitido; normal fuera de Debian/Docker)")
        return
    code, out, _ = _run(
        [
            "dpkg-query",
            "-W",
            "-f=${Package}\t${Version}\n",
            "libreoffice-core",
            "libreoffice-writer",
            "fontconfig",
            "fonts-crosextra-carlito",
            "fonts-crosextra-caladea",
            "fonts-liberation2",
            "fonts-dejavu",
            "fonts-noto-core",
        ]
    )
    if code != 0:
        print("No se pudieron consultar paquetes dpkg")
        return
    for line in out.splitlines():
        print(line)


def _default_fixture() -> Path | None:
    candidates = (
        ROOT / "docx_templates" / "FINIQUITO FORMATO.docx",
        ROOT / "vitroflex_templates" / "MEMO MENSUAL FORMATO.docx",
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def _try_conversion(docx_path: Path) -> None:
    from modules.vitroflex_docs.libreoffice_pdf import docx_to_pdf, resolve_soffice_path

    if not resolve_soffice_path():
        print("Conversión omitida: LibreOffice no detectado")
        return

    with tempfile.TemporaryDirectory(prefix="proclean_pdf_diag_") as tmp:
        tdir = Path(tmp)
        pdf_path = tdir / f"{docx_path.stem}.pdf"
        try:
            docx_to_pdf(docx_path, pdf_path)
        except Exception as exc:
            print(f"Conversión FALLÓ: {exc}")
            return
        size = pdf_path.stat().st_size
        print(f"Conversión OK: {docx_path.name} -> {pdf_path.name} ({size} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnóstico LibreOffice/fontconfig para PDFs")
    parser.add_argument(
        "--convert",
        nargs="?",
        const="__default__",
        metavar="DOCX",
        help="Prueba conversión DOCX→PDF (opcional: ruta; sin valor usa plantilla finiquito)",
    )
    args = parser.parse_args()

    from modules.vitroflex_docs.libreoffice_pdf import resolve_soffice_path

    _print_section("LibreOffice")
    soffice = resolve_soffice_path()
    print(f"resolve_soffice_path(): {soffice or '(no detectado)'}")
    if soffice:
        _print_version("soffice --version", [soffice, "--version"])
    _print_version("libreoffice --version", ["libreoffice", "--version"])

    _print_section("Paquetes (Debian/Docker)")
    _print_font_packages()

    _print_section("fc-match (sustitución de fuentes)")
    if not shutil.which("fc-match"):
        print("fc-match no disponible (normal en Windows sin fontconfig)")
    else:
        code, out, _ = _run(["fc-list"], timeout=60)
        if code == 0 and out:
            print("fc-list (primeras 8 líneas):")
            for line in out.splitlines()[:8]:
                print(f"  {line}")
        for family in FONT_FAMILIES:
            _print_fc_match(family)

    if args.convert is not None:
        _print_section("Prueba de conversion DOCX->PDF")
        if args.convert == "__default__":
            fixture = _default_fixture()
            if not fixture:
                print("No hay fixture DOCX disponible en el repo")
                return 1
            docx_path = fixture
        else:
            docx_path = Path(args.convert)
            if not docx_path.is_file():
                print(f"No existe: {docx_path}")
                return 1
        print(f"Entrada: {docx_path.resolve()}")
        _try_conversion(docx_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
