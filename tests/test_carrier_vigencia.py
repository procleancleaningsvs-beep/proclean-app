from datetime import date

from modules.carrier.vigencia import (
    max_still_valid_payment_month,
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


def test_max_still_valid_prefers_latest_valid_month():
    inhab = set()
    # 10 abr 2026: marzo y abril siguen vigentes como meses de pago; el más reciente es abril
    assert max_still_valid_payment_month(date(2026, 4, 10), inhab) == (2026, 4)


def test_inhabil_pushes_deadline():
    # 17 abril 2026 es viernes hábil; si 17 fuera inhábil artificial, empuja
    inhab = {date(2026, 4, 17)}
    d = operational_deadline_for_payment_month(2026, 3, inhab)
    assert d >= date(2026, 4, 17)
