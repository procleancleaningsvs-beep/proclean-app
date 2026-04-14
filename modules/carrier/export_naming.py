"""
Nombre del PDF de expediente Cursos: literal del trabajador en el Alta (movimiento IMSS).

Se toma el campo `nombre` del primer movimiento en `format_history.payload_json`.
Si no hay Alta vinculada o no hay nombre usable, se usa el nombre del expediente.
Solo se eliminan caracteres inválidos para archivo (misma regla que `generator`).
"""

from __future__ import annotations

import json
import re

from generator import INVALID_FILENAME_CHARS


def first_worker_name_from_payload_json(payload_json: str | None) -> str | None:
    if not payload_json or not str(payload_json).strip():
        return None
    try:
        data = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, list) or not data:
        return None
    first = data[0]
    if not isinstance(first, dict):
        return None
    n = first.get("nombre")
    if n is None:
        return None
    s = str(n).strip()
    return s or None


def curso_export_pdf_display_name(*, worker_name_from_alta: str | None, expediente_nombre: str) -> str:
    """
    Nombre de archivo final: «NOMBRE TRABAJADOR.pdf» sin prefijos.
    Sanitiza solo caracteres inválidos para sistema de archivos.
    """
    raw = (worker_name_from_alta or "").strip() or (expediente_nombre or "").strip()
    if raw.lower().endswith(".pdf"):
        raw = raw[:-4].rstrip()
    base = re.sub(INVALID_FILENAME_CHARS, "", raw)
    base = re.sub(r"\s+", " ", base).strip()
    if not base:
        base = "expediente"
    if not base.lower().endswith(".pdf"):
        base = f"{base}.pdf"
    return base
