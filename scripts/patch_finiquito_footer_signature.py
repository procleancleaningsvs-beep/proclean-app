"""CLI: escribe en disco la plantilla parcheada (delega en módulo Python).

La app y los scripts de render usan `patch_finiquito_docx_template_bytes` en memoria
vía `render_finiquito_docx` / `render_finiquito_final`; este archivo solo actualiza el .docx del repo.

  python scripts/patch_finiquito_footer_signature.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.finiquitos.finiquito_template_patch import patch_finiquito_docx_template_bytes
DOCX = ROOT / "docx_templates" / "FINIQUITO FORMATO.docx"


def main() -> None:
    if not DOCX.is_file():
        raise SystemExit(f"No existe {DOCX}")
    raw = DOCX.read_bytes()
    DOCX.write_bytes(patch_finiquito_docx_template_bytes(raw))
    print("OK:", DOCX)


if __name__ == "__main__":
    main()
