"""Fecha larga en español para placeholders del finiquito."""

from __future__ import annotations

from datetime import date

_MESES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

_DIAS = (
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
)


def fecha_emision_larga(d: date, *, con_dia_semana: bool = True) -> str:
    if con_dia_semana:
        dia = _DIAS[d.weekday()]
        return f"{dia} {d.day} de {_MESES[d.month - 1]} de {d.year}"
    return f"{d.day} de {_MESES[d.month - 1]} de {d.year}"
