"""Rutas a las plantillas DOCX oficiales (fuente de verdad)."""

from __future__ import annotations

import os
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent.parent

# Sobrescribir con PROCLEAN_VITROFLEX_TEMPLATES_DIR si hace falta
_TEMPLATES_ROOT = Path(os.environ.get("PROCLEAN_VITROFLEX_TEMPLATES_DIR", str(_BASE / "vitroflex_templates")))

MEMO_DOCX = _TEMPLATES_ROOT / "MEMO MENSUAL FORMATO.docx"
CR_DOCX = _TEMPLATES_ROOT / "CR MENSUAL FORMATO.docx"
