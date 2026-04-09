"""Nombre de archivo PDF del finiquito (formato propio) y texto del documento."""

from __future__ import annotations

import re


def normalizar_nombre_empleado_documento(s: str) -> str:
    """
    Valor para {empleado_nombre_completo} en el DOCX: trim, espacios colapsados, TODO en mayúsculas.
    Sin truncar ni alterar el orden ni los caracteres del nombre (salvo colapso de espacios).
    """
    return " ".join((s or "").split()).upper()


def nombre_propio_para_archivo(s: str) -> str:
    """
    Equivalente práctico a NOMPROPIO de Excel: primera letra de cada palabra en mayúscula,
    el resto en minúscula. Trim, espacios internos colapsados a uno.
    Conserva acentos; no reordena ni elimina apellidos.
    """
    s = " ".join((s or "").split())
    if not s:
        return ""
    out: list[str] = []
    for w in s.split():
        if len(w) == 1:
            out.append(w.upper())
        else:
            out.append(w[0].upper() + w[1:].lower())
    return " ".join(out)


def sanitizar_nombre_archivo_windows(s: str) -> str:
    """Quita caracteres no válidos en nombres de archivo en Windows."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", s).strip()


def build_finiquito_pdf_filename(nombre_capturado: str) -> str:
    """
    Devuelve el nombre completo del archivo: 'Finiquito {Nombre}.pdf'
    con formato propio y segmento seguro para sistema de archivos.
    """
    base = nombre_propio_para_archivo((nombre_capturado or "").strip())
    safe = sanitizar_nombre_archivo_windows(base) or "Empleado"
    return f"Finiquito {safe}.pdf"
