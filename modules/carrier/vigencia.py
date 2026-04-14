"""
Vigencia operativa del «mes de pago» para documentos base (SIPARE / Pago IMSS).

Regla de negocio:
  - El pago del mes M es vigente hasta el día 17 del mes calendario siguiente (M+1).
  - Si ese día 17 cae en sábado, domingo o fecha marcada como inhábil en
    `carrier_inhabiles.json`, el vencimiento operativo es el siguiente día hábil
    (se avanza día a día hasta encontrar un hábil).
  - La alerta informativa (no bloqueante) aplica cuando la fecha actual es
    estrictamente posterior al vencimiento operativo y el usuario sigue
    seleccionando el mes M como «mes base».
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def _add_months(y: int, m: int, delta: int) -> tuple[int, int]:
    """Añade delta meses a (y, m), ambos 1..12."""
    idx = y * 12 + (m - 1) + delta
    ny, nm0 = divmod(idx, 12)
    return ny, nm0 + 1


def nominal_day_17_after_payment_month(payment_year: int, payment_month: int) -> date:
    """Día 17 del mes calendario siguiente al mes de pago."""
    ny, nm = _add_months(payment_year, payment_month, 1)
    _last = monthrange(ny, nm)[1]
    day = min(17, _last)
    return date(ny, nm, day)


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def is_working_day(d: date, inhabiles: set[date]) -> bool:
    return not is_weekend(d) and d not in inhabiles


def next_business_on_or_after(d: date, inhabiles: set[date]) -> date:
    """Primer día hábil en la fecha d o después."""
    cur = d
    for _ in range(366):
        if is_working_day(cur, inhabiles):
            return cur
        cur += timedelta(days=1)
    return d


def operational_deadline_for_payment_month(
    payment_year: int, payment_month: int, inhabiles: set[date]
) -> date:
    """
    Vencimiento operativo del pago del mes (payment_year, payment_month).

    Parte del día 17 del mes siguiente; si no es hábil, se recorre al siguiente hábil.
    """
    nominal = nominal_day_17_after_payment_month(payment_year, payment_month)
    if is_working_day(nominal, inhabiles):
        return nominal
    return next_business_on_or_after(nominal + timedelta(days=1), inhabiles)


def payment_month_still_valid_today(
    payment_year: int, payment_month: int, today: date, inhabiles: set[date]
) -> bool:
    deadline = operational_deadline_for_payment_month(payment_year, payment_month, inhabiles)
    return today <= deadline


def should_warn_stale_payment_month(
    payment_year: int, payment_month: int, today: date, inhabiles: set[date]
) -> bool:
    """
    True si hoy ya pasó el vencimiento operativo del mes de pago indicado.

    En ese caso se muestra un aviso no bloqueante si el usuario sigue usando ese mes.
    """
    deadline = operational_deadline_for_payment_month(payment_year, payment_month, inhabiles)
    return today > deadline
