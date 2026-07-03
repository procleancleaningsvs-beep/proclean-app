from __future__ import annotations

import re
from datetime import date, time
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


def parse_time_hhmmss(value: Any) -> tuple[time | None, str | None]:
    s = _norm(value)
    if not s:
        return None, "Hora inválida."
    parts = s.split(":")
    if len(parts) not in (2, 3):
        return None, "Hora inválida."
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
        return time(hour, minute, second), None
    except (TypeError, ValueError):
        return None, "Hora inválida."


def format_registration_datetime(fecha_registro: Any, hora_registro: Any) -> str:
    d, derr = parse_date_iso(fecha_registro)
    if derr or d is None:
        raise ValueError("La Fecha de Registro no es válida.")
    t, terr = parse_time_hhmmss(hora_registro)
    if terr or t is None:
        raise ValueError("La Hora de Registro no es válida.")
    suffix = "a. m." if t.hour < 12 else "p. m."
    hour12 = t.hour % 12 or 12
    return f"{d.day:02d}/{d.month:02d}/{d.year}  {hour12:02d}:{t.minute:02d}:{t.second:02d}{suffix}"


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
