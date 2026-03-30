"""
Capturas PNG de la UI Vitroflex (MEMO) con Playwright.
Credenciales: lee instance/admin_credentials.txt o variables
PROCLEAN_UI_USER / PROCLEAN_UI_PASSWORD.

Uso (desde la raíz del repo):
  python scripts/capture_vitroflex_ui_evidence.py
Salida: tests/evidence_vitroflex/real_run/ui/
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "evidence_vitroflex" / "real_run" / "ui"
CREDS = ROOT / "instance" / "admin_credentials.txt"
PORT = int(os.environ.get("PROCLEAN_VITROFLEX_UI_PORT", "58912"))


def _parse_admin_file(path: Path) -> tuple[str, str] | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="ignore")
    u = re.search(r"Usuario\s+admin:\s*(.+)", text, re.IGNORECASE)
    p = re.search(r"Contraseña\s+admin:\s*(.+)", text, re.IGNORECASE)
    if not u or not p:
        return None
    return u.group(1).strip(), p.group(1).strip()


def _serve_flask() -> None:
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app import app  # noqa: PLC0415

    app.run(host="127.0.0.1", port=PORT, use_reloader=False, threaded=True, debug=False)


def main() -> int:
    user = os.environ.get("PROCLEAN_UI_USER")
    password = os.environ.get("PROCLEAN_UI_PASSWORD")
    if not user or not password:
        parsed = _parse_admin_file(CREDS)
        if not parsed:
            print("Faltan credenciales: define PROCLEAN_UI_USER / PROCLEAN_UI_PASSWORD o crea instance/admin_credentials.txt")
            return 1
        user, password = parsed

    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError:
        print("pip install playwright && playwright install chromium")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    th = threading.Thread(target=_serve_flask, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{PORT}"
    for _ in range(40):
        try:
            import urllib.request

            urllib.request.urlopen(base + "/login", timeout=0.5)
            break
        except OSError:
            time.sleep(0.25)
    else:
        print("No respondió el servidor Flask en", base)
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"{base}/login", wait_until="networkidle")
        page.fill('input[name="username"]', user)
        page.fill('input[name="password"]', password)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        page.goto(f"{base}/vitroflex/memo", wait_until="networkidle")
        page.wait_for_selector("#vf-fecha-iso")
        assert page.locator("#vf-municipio-otro-wrap").is_hidden()
        assert page.locator("#vf-filename-otro-wrap").is_hidden()
        page.locator(".vf-fecha-block").screenshot(path=str(OUT / "memo_sin_campo_libre_default.png"))
        page.locator("#vf-permisos-block").screenshot(path=str(OUT / "memo_labels_permiso_formato.png"))
        page.select_option("#vf-municipio-mod", "otro")
        page.locator("#vf-municipio-otro-wrap").wait_for(state="visible")
        page.locator(".vf-fecha-block").screenshot(path=str(OUT / "memo_municipio_otro_visible.png"))
        page.select_option("#vf-municipio-mod", "garcia")
        page.locator("#vf-municipio-otro-wrap").wait_for(state="hidden")

        page.locator("#vf-workers-toolbar").screenshot(path=str(OUT / "memo_quitar_todos_toolbar.png"))
        page.select_option("#vf-pdf-name-mode", "a")
        page.locator(".vf-export-row").screenshot(path=str(OUT / "memo_export_sin_nombre_otro.png"))
        page.select_option("#vf-pdf-name-mode", "otro")
        page.locator("#vf-filename-otro-wrap").wait_for(state="visible")
        page.fill("#vf-filename-otro", "mi archivo prueba")
        page.locator(".vf-export-row").screenshot(path=str(OUT / "memo_export_otro_nombre.png"))

        page.goto(f"{base}/vitroflex/cr", wait_until="networkidle")
        page.wait_for_selector("#vf-fecha-iso")
        page.locator(".vf-fecha-block").screenshot(path=str(OUT / "cr_fecha_municipio.png"))
        page.locator(".vf-table-wrap").screenshot(path=str(OUT / "cr_tabla_captura_web.png"))

        browser.close()

    cr_pdf = ROOT / "tests" / "evidence_vitroflex" / "real_run" / "pdfs" / "04_cr_vitroflex.pdf"
    if cr_pdf.is_file():
        try:
            import fitz  # noqa: PLC0415

            doc = fitz.open(str(cr_pdf))
            page0 = doc[0]
            r = page0.rect
            clip = fitz.Rect(0, r.height * 0.32, r.width, r.height * 0.58)
            pix = page0.get_pixmap(matrix=fitz.Matrix(2.25, 2.25), clip=clip, alpha=False)
            pix.save(str(OUT / "cr_tabla_pdf_proporcional_memo.png"))
            doc.close()
        except Exception as exc:
            print("[aviso] Sin recorte PDF CR:", exc)

    print("OK:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
