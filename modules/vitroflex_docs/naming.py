"""Nombres de archivo PDF Vitroflex."""

from __future__ import annotations

import re

from modules.vitroflex_docs.dates import extraer_mes_anio_desde_texto_fecha, mes_nombre, parse_iso_date


_INVALID_FS = re.compile(r'[<>:"/\\\\|?*\x00-\x1f]+')


def sanitize_filename_base(name: str, max_len: int = 160) -> str:
    s = (name or "").strip()
    s = _INVALID_FS.sub("_", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    s = (s or "documento").upper()
    if len(s) > max_len:
        s = s[: max_len - 3].rstrip() + "..."
    return s


def resumir_nombres(nombres: list[str], *, max_nombres: int = 2) -> str:
    limpios = [n.strip() for n in nombres if n and str(n).strip()]
    if not limpios:
        return "sin_nombres"
    if len(limpios) <= max_nombres:
        return " ".join(limpios)
    resto = len(limpios) - max_nombres
    return " ".join(limpios[:max_nombres]) + f" y {resto} más"


def memo_filename_opcion1(nombres: list[str]) -> str:
    base = "MEMO " + resumir_nombres(nombres)
    return sanitize_filename_base(base) + ".pdf"


def memo_filename_opcion2(permiso1_iso: str | None) -> str:
    d = parse_iso_date(permiso1_iso)
    if d:
        mes = mes_nombre(d.month)
        base = f"MEMO MENSUAL {mes} {d.year}"
    else:
        base = "MEMO MENSUAL"
    return sanitize_filename_base(base) + ".pdf"


def planta_display_para_archivo(planta: str) -> str:
    """Texto legible; no forzar mayúsculas si estorba."""
    return (planta or "").strip() or "planta"


def cr_filename_opcion1(planta: str, nombres: list[str]) -> str:
    p = planta_display_para_archivo(planta)
    base = f"CR {p} {resumir_nombres(nombres)}"
    return sanitize_filename_base(base) + ".pdf"


def cr_filename_opcion2(planta: str, permiso1_iso: str | None, fecha_texto: str | None) -> str:
    p = planta_display_para_archivo(planta)
    mes_anio: str | None = None
    d = parse_iso_date(permiso1_iso)
    if d:
        mes_anio = f"{mes_nombre(d.month)} {d.year}"
    else:
        ext = extraer_mes_anio_desde_texto_fecha(fecha_texto)
        if ext:
            mes_anio = f"{ext[0]} {ext[1]}"
    if mes_anio:
        base = f"CR {p} {mes_anio}"
    else:
        base = f"CR {p}"
    return sanitize_filename_base(base) + ".pdf"
