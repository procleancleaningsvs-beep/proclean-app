"""
Paquete mensual IMSS (Carrier > Cursos): vigente, siguiente y tope operativo.

Reglas (negocio acordado):
  - Se toma el **mes calendario actual** (Y, M) de la fecha de hoy.
  - `corte_mes_actual` = día 17 del mes calendario actual; si no es hábil, el siguiente hábil.
  - Mientras **hoy <= corte_mes_actual**, el **paquete vigente** es el del mes de pago **hace dos meses** (M-2).
  - Cuando **hoy > corte_mes_actual**, el **paquete vigente** pasa al mes de pago **hace un mes** (M-1).
  - El **paquete siguiente** es el mes inmediatamente después del vigente, pero **nunca** por encima del
    tope `mes_calendario_actual - 1` como mes de pago (ej. en abril 2026 el tope es marzo: no se ofrece
    abril como paquete utilizable todavía).
  - Los inhábiles extra se leen igual que en `vigencia` / `carrier_inhabiles.json`.
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, timedelta

from modules.carrier.vigencia import is_working_day, next_business_on_or_after


def _parse_ym(ym: str) -> tuple[int, int] | None:
    s = (ym or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", s):
        return None
    y, m = map(int, s.split("-", 1))
    if y < 2000 or y > 2100 or m < 1 or m > 12:
        return None
    return y, m


def _add_months(y: int, m: int, delta: int) -> tuple[int, int]:
    idx = y * 12 + (m - 1) + delta
    ny, nm0 = divmod(idx, 12)
    return ny, nm0 + 1


def _ym_key(t: tuple[int, int]) -> int:
    return t[0] * 12 + t[1] - 1


def cutoff_dia17_habil_mes_calendario(cal_year: int, cal_month: int, inhabiles: set[date]) -> date:
    """Día 17 del mes calendario (cal_year, cal_month), recorriendo al siguiente hábil si aplica."""
    last = monthrange(cal_year, cal_month)[1]
    d = min(17, last)
    nominal = date(cal_year, cal_month, d)
    if is_working_day(nominal, inhabiles):
        return nominal
    return next_business_on_or_after(nominal + timedelta(days=1), inhabiles)


def paquete_vigente_payment_tuple(today: date, inhabiles: set[date]) -> tuple[int, int]:
    """Mes de pago (año, mes) cuyo paquete es el vigente hoy."""
    y, m = today.year, today.month
    corte = cutoff_dia17_habil_mes_calendario(y, m, inhabiles)
    if today <= corte:
        return _add_months(y, m, -2)
    return _add_months(y, m, -1)


def tope_ultimo_mes_pago_utilizable(today: date) -> tuple[int, int]:
    """Último mes de pago que puede considerarse «utilizable» (mes calendario anterior)."""
    return _add_months(today.year, today.month, -1)


def paquete_siguiente_payment_tuple(
    vigente: tuple[int, int], today: date, inhabiles: set[date]
) -> tuple[int, int] | None:
    """
    Mes de pago siguiente al vigente, si no rebasa el tope (mes calendario actual - 1).
    Si no aplica, None.
    """
    sig = _add_months(vigente[0], vigente[1], 1)
    cap = tope_ultimo_mes_pago_utilizable(today)
    if _ym_key(sig) > _ym_key(cap):
        return None
    return sig


def ym_to_str(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}"


def paquete_vigente_ym_str(today: date, inhabiles: set[date]) -> str:
    v = paquete_vigente_payment_tuple(today, inhabiles)
    return ym_to_str(v[0], v[1])


def paquetes_utilizables_normal(today: date, inhabiles: set[date]) -> list[str]:
    """Lista de YYYY-MM que el usuario normal puede usar (vigente + siguiente si existe)."""
    v = paquete_vigente_payment_tuple(today, inhabiles)
    out = [ym_to_str(v[0], v[1])]
    s = paquete_siguiente_payment_tuple(v, today, inhabiles)
    if s is not None:
        out.append(ym_to_str(s[0], s[1]))
    return out


def usuario_normal_puede_subir_paquete(ym: str, today: date, inhabiles: set[date]) -> bool:
    """Solo el paquete vigente es cargable por usuario normal."""
    return ym == paquete_vigente_ym_str(today, inhabiles)


def usuario_normal_puede_descargar_paquete(ym: str, today: date, inhabiles: set[date]) -> bool:
    return ym in set(paquetes_utilizables_normal(today, inhabiles))


def paquete_futuro_aun_no_utilizable(ym: str, today: date) -> bool:
    """True si ym (AAAA-MM) es estrictamente posterior al tope mes_calendario-1."""
    p = _parse_ym(ym)
    if not p:
        return True
    cap = tope_ultimo_mes_pago_utilizable(today)
    return _ym_key(p) > _ym_key(cap)
