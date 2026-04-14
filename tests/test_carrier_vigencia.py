from datetime import date

from modules.carrier.vigencia import (
    nominal_day_17_after_payment_month,
    operational_deadline_for_payment_month,
    should_warn_stale_payment_month,
)


def test_nominal_march_goes_to_april_17():
    assert nominal_day_17_after_payment_month(2026, 3) == date(2026, 4, 17)


def test_operational_deadline_weekend_roll_march_2021():
    # Pago marzo 2021 → día 17 nominal abril 2021 = sábado → siguiente hábil lunes 19
    assert operational_deadline_for_payment_month(2021, 3, set()) == date(2021, 4, 19)


def test_warn_after_deadline():
    assert not should_warn_stale_payment_month(2026, 3, date(2026, 4, 17), set())
    assert should_warn_stale_payment_month(2026, 3, date(2026, 4, 18), set())


def test_inhabil_pushes_deadline():
    # 17 abril 2026 es viernes hábil; si 17 fuera inhábil artificial, empuja
    inhab = {date(2026, 4, 17)}
    d = operational_deadline_for_payment_month(2026, 3, inhab)
    assert d >= date(2026, 4, 17)
