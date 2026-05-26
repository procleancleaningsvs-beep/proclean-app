from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import NamedTuple


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

# Claves que computan como día pagado / cuentan para séptimo día (motor 4.1+)
PAID_DAILY_KEYS = {"A", "V", "PCS", "FE", "R", "DL", "FL"}

# Claves que NO computan como día pagado / no cuentan para séptimo
UNPAID_DAILY_KEYS = {"F", "PSS", "I", "S", "B", "D", "NI"}

# NI: no paga ni computa; solo aclara nuevo ingreso (no está en PAID_DAILY_KEYS).

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

# ---------------------------------------------------------------------------
# Precheck motor de cálculo (Microfase 4.0): códigos de warning estables en JSON
# ---------------------------------------------------------------------------
WARN_BLOCK_CALC_MISSING_SALARY = "block_calc_missing_salary_operativo"
WARN_BLOCK_CALC_MISSING_VALOR_HE = "block_calc_missing_valor_x_he_when_he"
WARN_REVIEW_NO_CONFIDENT_MATCH = "review_no_confident_headcount_match"
WARN_HEADCOUNT_UNAVAILABLE = "headcount_unavailable_pending_match"
WARN_LOCALIDAD_FRONTERA_DEMOTION_BLOCKED = "localidad_frontera_demotion_blocked"
WARN_LOCALIDAD_FRONTERA_IMPORT_UNKNOWN = "localidad_frontera_import_unknown_defaults_general"
WARN_FRONTERA_EXCEL_VS_LEARNED = "frontera_excel_contradicts_learned_locality"
WARN_SAME_NSS_MULTIPLE_CLIENTS = "same_nss_multiple_clients"

# Conciliación parámetros base ↔ Headcount (Microfase 4.0+)
WARN_NOMINA_SIN_MATCH_HEADCOUNT = "NOMINA_SIN_MATCH_HEADCOUNT"
WARN_CONTPAQ_SIN_MATCH_HEADCOUNT = "CONTPAQ_SIN_MATCH_HEADCOUNT"
WARN_MATCH_DUDOSO_NOMBRE = "MATCH_DUDOSO_NOMBRE"
WARN_NSS_IGUAL_NOMBRE_DISTINTO = "NSS_IGUAL_NOMBRE_DISTINTO"
WARN_NOMBRE_SIMILAR_NSS_DISTINTO = "NOMBRE_SIMILAR_NSS_DISTINTO"
WARN_HEADCOUNT_ACTIVO_SIN_SALARIO = "HEADCOUNT_ACTIVO_SIN_SALARIO_OPERATIVO"
WARN_HEADCOUNT_ACTIVO_SIN_VALOR_HE = "HEADCOUNT_ACTIVO_SIN_VALOR_X_HE"
WARN_EMPLEADO_NOMINA_INACTIVO_HC = "EMPLEADO_EN_NOMINA_INACTIVO_HEADCOUNT"
WARN_EMPLEADO_ACTIVO_HC_BAJA_CONTPAQ = "EMPLEADO_ACTIVO_HEADCOUNT_BAJA_CONTPAQ"
WARN_DUPLICADO_NOMINA = "DUPLICADO_NOMINA"
WARN_DUPLICADO_CONTPAQ = "DUPLICADO_CONTPAQ"

# Mensaje UI cuando Headcount no está disponible (Microfase 4.1)
MSG_HEADCOUNT_UNAVAILABLE_CALCULO = (
    "Headcount no disponible; revisar matches antes de usar cálculo real."
)

# ---------------------------------------------------------------------------
# Motor preliminar 4.1: factor ISR mensual, bono TPT, domingo laborado
# ---------------------------------------------------------------------------
FACTOR_ISR_MENSUAL = Decimal("30.4")
DIAS_TARIFA_ISR_DEFAULT = 7
DIAS_TARIFA_SUBSIDIO_DEFAULT = 7
BONO_TPT_TOPE_2026 = Decimal("140")

# Opciones de pago domingo laborado (factor sobre sueldo diario operativo)
DOMINGO_FACTOR_PROPORCIONAL = Decimal("1.17")
DOMINGO_FACTOR_PRIMA = Decimal("1.25")
DOMINGO_FACTOR_MANUAL = Decimal("0")  # no automático; revisar en visualizador

# ---------------------------------------------------------------------------
# Macro ISR_2026 (replica estricta 4.1.1): subsidio fijo y límite de base mensual
# ---------------------------------------------------------------------------
SUBSIDIO_MACRO_MENSUAL_2026 = Decimal("536.21")
# Subsidio aplica si: 0.01 <= baseSub_Mes < 11492.67 (límite superior exclusivo)
SUBSIDIO_BASE_MENSUAL_MIN_INCL = Decimal("0.01")
SUBSIDIO_BASE_MENSUAL_MAX_EXCL = Decimal("11492.67")

# Redondeo operativo de neto a pagar (.00, .20, .40, .60, .80 por defecto)
NETO_ROUND_STEP = Decimal("0.20")
NETO_INTEGER_THRESHOLD = Decimal("0.85")


class IsrBracket2026(NamedTuple):
    limite_inferior: Decimal
    limite_superior: Decimal | None  # None = sin tope superior
    cuota_fija: Decimal
    tasa_pct: Decimal


# Tarifa mensual art. 96 LISR 2026 (tabla publicada para cálculo mensual)
ISR_MENSUAL_2026_BRACKETS: tuple[IsrBracket2026, ...] = (
    IsrBracket2026(Decimal("0.01"), Decimal("746.04"), Decimal("0"), Decimal("1.92")),
    IsrBracket2026(Decimal("746.05"), Decimal("6332.05"), Decimal("14.32"), Decimal("6.40")),
    IsrBracket2026(Decimal("6332.06"), Decimal("11128.01"), Decimal("371.83"), Decimal("10.88")),
    IsrBracket2026(Decimal("11128.02"), Decimal("12935.82"), Decimal("893.63"), Decimal("16.00")),
    IsrBracket2026(Decimal("12935.83"), Decimal("15487.71"), Decimal("1182.88"), Decimal("17.92")),
    IsrBracket2026(Decimal("15487.72"), Decimal("31236.49"), Decimal("1639.32"), Decimal("21.36")),
    IsrBracket2026(Decimal("31236.50"), Decimal("49233.00"), Decimal("4005.46"), Decimal("23.52")),
    IsrBracket2026(Decimal("49233.01"), Decimal("93993.90"), Decimal("8237.45"), Decimal("30.00")),
    IsrBracket2026(Decimal("93993.91"), Decimal("125325.20"), Decimal("21665.72"), Decimal("32.00")),
    IsrBracket2026(Decimal("125325.21"), Decimal("375975.61"), Decimal("31691.85"), Decimal("34.00")),
    IsrBracket2026(Decimal("375975.62"), None, Decimal("116912.87"), Decimal("35.00")),
)


def salario_minimo_semanal_2026(*, es_frontera: bool) -> Decimal:
    smg = SMG_BY_YEAR[2026]["FRONTERA" if es_frontera else "GENERAL"]
    return smg * Decimal("7")
