"""
E2E Vitroflex (PDF real vía API, sin html2pdf).

Requisitos:
  - Plantillas en vitroflex_templates/ (MEMO y CR .docx oficiales)
  - LibreOffice (soffice) — ver PROCLEAN_LIBREOFFICE
  - Credenciales: instance/admin_credentials.txt o variables de entorno

Uso:
  python -m pip install -r requirements.txt
  python scripts/e2e_vitroflex_pdf.py

Salida: tests/evidence_vitroflex/
"""

from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "tests" / "evidence_vitroflex"
PDF_DIR = EVIDENCE / "pdfs"
RENDER_DIR = EVIDENCE / "pdf_renders"
BASE_URL = "http://127.0.0.1:18766"


def _parse_credentials() -> tuple[str, str]:
    import os

    u = (os.environ.get("PROCLEAN_E2E_USER") or "").strip()
    p = (os.environ.get("PROCLEAN_E2E_PASSWORD") or "").strip()
    if u and p:
        return u, p
    cred = ROOT / "instance" / "admin_credentials.txt"
    text = cred.read_text(encoding="utf-8", errors="replace")
    mu = re.search(r"Usuario admin:\s*(\S+)", text)
    mp = re.search(r"Contraseña admin:\s*(\S+)", text)
    if not mu or not mp:
        raise SystemExit("Credenciales no encontradas.")
    return mu.group(1), mp.group(1)


def _start_flask():
    from app import app

    app.config["TESTING"] = True

    def run():
        app.run(host="127.0.0.1", port=18766, debug=False, use_reloader=False, threaded=True)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    import urllib.request

    for _ in range(40):
        try:
            urllib.request.urlopen(BASE_URL + "/login", timeout=1)
            return
        except OSError:
            time.sleep(0.25)


def _login_session():
    import requests

    s = requests.Session()
    u, p = _parse_credentials()
    r = s.post(
        f"{BASE_URL}/login",
        data={"username": u, "password": p},
        allow_redirects=False,
        timeout=30,
    )
    if r.status_code not in (302, 200):
        raise SystemExit(f"Login falló: {r.status_code}")
    return s


def _pdf_ok(data: bytes) -> bool:
    return len(data) > 500 and data[:4] == b"%PDF"


def _save_render(pdf_path: Path, stem: str, pages: int = 2) -> list[Path]:
    import fitz

    out: list[Path] = []
    doc = fitz.open(str(pdf_path))
    try:
        for i in range(min(pages, len(doc))):
            pix = doc.load_page(i).get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            p = RENDER_DIR / f"{stem}_p{i+1}.png"
            pix.save(str(p))
            out.append(p)
    finally:
        doc.close()
    return out


def _post_pdf(s, payload: dict, name: str) -> tuple[Path, bytes]:
    import requests

    r = s.post(f"{BASE_URL}/vitroflex/api/generate-pdf", json=payload, timeout=300)
    if r.status_code != 200:
        try:
            j = r.json()
            err = j.get("error", r.text)
        except Exception:
            err = r.text
        raise RuntimeError(f"{name}: HTTP {r.status_code} {err}")
    data = r.content
    if not _pdf_ok(data):
        raise RuntimeError(f"{name}: PDF inválido o vacío ({len(data)} bytes)")
    path = PDF_DIR / f"{name}.pdf"
    path.write_bytes(data)
    return path, data


def main() -> int:
    import os

    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))

    try:
        import requests
    except ImportError:
        print("pip install requests")
        return 1

    from modules.vitroflex_docs.template_paths import CR_DOCX, MEMO_DOCX

    if not MEMO_DOCX.is_file() or not CR_DOCX.is_file():
        print("OMITIDO: coloca MEMO MENSUAL FORMATO.docx y CR MENSUAL FORMATO.docx en vitroflex_templates/")
        print(f"  MEMO: {MEMO_DOCX} exists={MEMO_DOCX.is_file()}")
        print(f"  CR:   {CR_DOCX} exists={CR_DOCX.is_file()}")
        return 2

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    RENDER_DIR.mkdir(parents=True, exist_ok=True)

    _start_flask()
    s = _login_session()

    lines = ["# E2E Vitroflex (API DOCX→PDF)", ""]
    workers3 = [
        {"nombre": "Ana López", "imss": "12345678901", "actividad": "Aux.", "tel": "81 2183 9413"},
        {"nombre": "Luis Ruiz", "imss": "10987654321", "actividad": "Aux.", "tel": "81 2183 9413"},
        {"nombre": "Rosa Fuentes", "imss": "55555555555", "actividad": "Apoyo", "tel": "81 2000 0000"},
    ]
    big = [
        {
            "nombre": f"Trabajador {i+1}",
            "imss": f"{10000000000+i:011d}",
            "actividad": "Aux. de limpieza",
            "tel": "81 2183 9413",
        }
        for i in range(29)
    ]

    cases = [
        (
            "api_memo_manual_3",
            {
                "kind": "memo",
                "fecha_iso": "2026-03-15",
                "municipio_modo": "garcia",
                "municipio_otro": "",
                "permiso1": "2026-03-10",
                "permiso2": "2026-04-27",
                "workers": workers3,
                "filename": "memo_manual",
                "disposition": "attachment",
            },
        ),
        (
            "api_cr_vitroflex",
            {
                "kind": "cr",
                "fecha_iso": "2026-03-15",
                "municipio_modo": "garcia",
                "municipio_otro": "",
                "planta": "Vitroflex",
                "workers": workers3[:2],
                "filename": "cr_vitroflex",
                "disposition": "attachment",
            },
        ),
        (
            "api_memo_multiples_filas",
            {
                "kind": "memo",
                "fecha_iso": "2026-01-01",
                "municipio_modo": "garcia",
                "municipio_otro": "",
                "permiso1": "2026-01-01",
                "permiso2": "2026-02-18",
                "workers": big,
                "filename": "memo_big",
                "disposition": "attachment",
            },
        ),
    ]

    for stem, payload in cases:
        path, _ = _post_pdf(s, payload, stem)
        renders = _save_render(path, stem, pages=3 if "multiples" in stem else 2)
        lines.append(f"## {stem}")
        lines.append(f"- PDF: `{path}`")
        lines.append(f"- Renders: {', '.join(str(x) for x in renders)}")
        lines.append("")

    rep = EVIDENCE / "REPORTE_API_E2E.md"
    rep.write_text("\n".join(lines), encoding="utf-8")
    print("OK:", rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
