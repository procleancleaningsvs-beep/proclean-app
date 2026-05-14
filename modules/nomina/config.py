from __future__ import annotations

from datetime import date
from decimal import Decimal


# ---------------------------------------------------------------------------
# UMI (Infonavit) per year
# ---------------------------------------------------------------------------
UMI_BY_YEAR: dict[int, Decimal] = {
    2026: Decimal("100.81"),
}


def get_umi_for_year(year: int | None) -> Decimal | None:
    if year is None:
        return None
    return UMI_BY_YEAR.get(int(year))


# ---------------------------------------------------------------------------
# Salario mínimo y exento de horas extra por año y zona
# (Microfase 4.0 deja preparado, motor de cálculo se construye en 4.1+)
# ---------------------------------------------------------------------------
SMG_BY_YEAR: dict[int, dict[str, Decimal]] = {
    2026: {
        "GENERAL": Decimal("315.04"),
        "FRONTERA": Decimal("440.87"),
    },
}

EXENTO_HE_BY_YEAR: dict[int, dict[str, Decimal]] = {
    2026: {
        "GENERAL": Decimal("236.28"),
        "FRONTERA": Decimal("330.65"),
    },
}


def get_smg_for_year(year: int | None, zona: str) -> Decimal | None:
    if year is None:
        return None
    table = SMG_BY_YEAR.get(int(year))
    if not table:
        return None
    key = "FRONTERA" if str(zona or "").strip().upper() == "FRONTERA" else "GENERAL"
    return table.get(key)


def get_exento_he_for_year(year: int | None, zona: str) -> Decimal | None:
    if year is None:
        return None
    table = EXENTO_HE_BY_YEAR.get(int(year))
    if not table:
        return None
    key = "FRONTERA" if str(zona or "").strip().upper() == "FRONTERA" else "GENERAL"
    return table.get(key)


# ---------------------------------------------------------------------------
# Festivos oficiales por año (descanso obligatorio LFT art. 74)
# El calendario se mantiene configurable por año, sin hardcodear en parser.
# ---------------------------------------------------------------------------
HOLIDAYS_BY_YEAR: dict[int, list[date]] = {
    2026: [
        date(2026, 1, 1),
        date(2026, 2, 2),
        date(2026, 3, 16),
        date(2026, 5, 1),
        date(2026, 9, 16),
        date(2026, 11, 16),
        date(2026, 12, 25),
        # Transmisión Poder Ejecutivo Federal y días electorales se agregan
        # explícitamente al catálogo cuando aplique (configurable, no implícito).
    ],
}


def get_holidays_for_year(year: int | None) -> list[date]:
    if year is None:
        return []
    return list(HOLIDAYS_BY_YEAR.get(int(year), []))


def is_official_holiday(d: date) -> bool:
    holidays = get_holidays_for_year(d.year)
    return d in holidays


# ---------------------------------------------------------------------------
# Catálogo de claves diarias del Master de Asistencia (v4)
# Microfase 4.0 deja documentado el cómputo. El motor de cálculo se construirá
# en una microfase posterior; esta tabla guía consistencia y warnings.
# ---------------------------------------------------------------------------
VALID_DAILY_KEYS = {
    "A", "D", "F", "V", "I", "PSS", "PCS", "NI",
    "B", "R", "S", "FE", "FL", "DL", "OT",
}

# Claves que computan como día pagado / cuentan para séptimo día
PAID_DAILY_KEYS = {"A", "V", "PCS", "FE", "R", "DL", "FL"}

# Claves que NO computan
UNPAID_DAILY_KEYS = {"F", "PSS", "I", "S", "B", "D", "NI"}

DAILY_KEY_LABELS: dict[str, str] = {
    "A": "Asistencia",
    "D": "Descanso programado",
    "F": "Falta",
    "V": "Vacaciones",
    "I": "Incapacidad IMSS",
    "PSS": "Permiso sin goce",
    "PCS": "Permiso con goce",
    "NI": "Nuevo ingreso (no computa)",
    "B": "Baja",
    "R": "Retardo (computa como asistencia, requiere revisión)",
    "S": "Suspensión",
    "FE": "Festivo descansado (computa)",
    "FL": "Festivo laborado (computa; pago adicional en fase posterior)",
    "DL": "Domingo/Descanso laborado (computa)",
    "OT": "Otro / revisar en observaciones",
}
