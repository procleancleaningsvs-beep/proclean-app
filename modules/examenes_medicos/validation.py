from __future__ import annotations

import re
from datetime import date
from typing import Any


def _norm(s: Any) -> str:
    return str(s or "").strip()


def validate_required_non_empty(value: Any, label: str) -> str | None:
    if not _norm(value):
        return f"{label} es obligatorio."
    return None


def validate_folio_orina(value: Any) -> str | None:
    s = _norm(value)
    if not s:
        return "Folio es obligatorio."
    if not re.fullmatch(r"\d{6}", s):
        return "El folio de orina debe tener exactamente 6 dígitos."
    return None


def validate_folio_sangre(value: Any) -> str | None:
    s = _norm(value)
    if not s:
        return "Folio es obligatorio."
    if not re.fullmatch(r"\d{12}", s):
        return "El folio de sangre debe tener exactamente 12 dígitos."
    return None


def validate_cliente_numero(value: Any) -> str | None:
    s = _norm(value)
    if not s:
        return "Número de cliente es obligatorio."
    if not re.fullmatch(r"\d{8}", s):
        return "El número de cliente debe tener exactamente 8 dígitos."
    return None


def validate_codigo_barra(value: Any) -> str | None:
    s = _norm(value).upper()
    if not s:
        return "Código de barras es obligatorio."
    if not re.fullmatch(r"[A-Z]{3}\d{10}", s):
        return "El código de barras debe ser 3 letras seguidas de 10 dígitos."
    return None


def validate_sexo(value: Any) -> str | None:
    s = _norm(value)
    if not s:
        return "Sexo es obligatorio."
    if s not in ("Mujer", "Hombre", "Otro"):
        return "Sexo no válido."
    return None


def parse_date_iso(value: Any) -> tuple[date | None, str | None]:
    s = _norm(value)[:10]
    if not s:
        return None, "Fecha inválida."
    try:
        return date.fromisoformat(s), None
    except ValueError:
        return None, "Fecha inválida (use AAAA-MM-DD)."


def edad_desde_fecha_nacimiento(fnac: date, ref: date) -> int:
    y = ref.year - fnac.year
    if (ref.month, ref.day) < (fnac.month, fnac.day):
        y -= 1
    return max(0, y)


def validate_positive_float(value: Any, label: str) -> tuple[float | None, str | None]:
    s = _norm(value).replace(",", ".")
    if not s:
        return None, f"{label} es obligatorio."
    try:
        v = float(s)
    except ValueError:
        return None, f"{label} debe ser numérico."
    if v <= 0:
        return None, f"{label} debe ser mayor a cero."
    return v, None


def classify_imc(imc: float) -> str:
    if imc < 18.5:
        return "Bajo peso"
    if imc < 25:
        return "Normal"
    if imc < 30:
        return "Sobrepeso"
    return "Obesidad"
