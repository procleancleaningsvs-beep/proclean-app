"""Nombre de archivo PDF del finiquito (español con partículas) y texto del documento."""

from __future__ import annotations

import re


def normalizar_nombre_empleado_documento(s: str) -> str:
    """
    Valor para {empleado_nombre_completo} en el DOCX: trim, espacios colapsados, TODO en mayúsculas.
    Sin truncar ni alterar el orden ni los caracteres del nombre (salvo colapso de espacios).
    Independiente del formato del nombre de archivo.
    """
    return " ".join((s or "").split()).upper()


def _cap_palabra_significativa(w: str) -> str:
    if not w:
        return w
    if len(w) == 1:
        return w.upper()
    return w[0].upper() + w[1:].lower()


def nombre_propio_pdf_espanol(s: str) -> str:
    """
    Formato para nombre de archivo: tipo nombre propio con partículas en minúsculas (de, del, y, de la/los/las).
    Trim, espacios colapsados. Conserva acentos; no reordena ni trunca.
    """
    words = " ".join((s or "").split()).split()
    if not words:
        return ""
    out: list[str] = []
    i = 0
    n = len(words)
    while i < n:
        w = words[i]
        wl = w.lower()
        if i == 0:
            out.append(_cap_palabra_significativa(w))
            i += 1
            continue
        if wl == "y":
            out.append("y")
            i += 1
            continue
        if wl == "del":
            out.append("del")
            i += 1
            continue
        if wl == "de":
            out.append("de")
            i += 1
            if i < n and words[i].lower() in ("la", "los", "las"):
                out.append(words[i].lower())
                i += 1
            continue
        out.append(_cap_palabra_significativa(w))
        i += 1
    return " ".join(out)


def nombre_propio_para_archivo(s: str) -> str:
    """Compatibilidad: mismo criterio que el PDF en español."""
    return nombre_propio_pdf_espanol(s)


def sanitizar_nombre_archivo_windows(s: str) -> str:
    """Quita caracteres no válidos en nombres de archivo en Windows."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", s).strip()


def build_finiquito_pdf_filename(nombre_capturado: str) -> str:
    """
    Devuelve el nombre completo del archivo: 'Finiquito {Nombre}.pdf'
    con partículas en minúsculas y segmento seguro para sistema de archivos.
    """
    base = nombre_propio_pdf_espanol((nombre_capturado or "").strip())
    safe = sanitizar_nombre_archivo_windows(base) or "Empleado"
    return f"Finiquito {safe}.pdf"
