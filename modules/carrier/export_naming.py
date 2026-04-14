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


def worker_name_from_payload_json(payload_json: str | None, movimiento_idx: int = 0) -> str | None:
    """Nombre del movimiento en posición `movimiento_idx` (0-based) dentro del payload del formato."""
    if not payload_json or not str(payload_json).strip():
        return None
    try:
        data = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, list) or not data:
        return None
    if movimiento_idx < 0 or movimiento_idx >= len(data):
        return None
    row = data[movimiento_idx]
    if not isinstance(row, dict):
        return None
    n = row.get("nombre")
    if n is None:
        return None
    s = str(n).strip()
    return s or None


def first_worker_name_from_payload_json(payload_json: str | None) -> str | None:
    return worker_name_from_payload_json(payload_json, 0)


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
