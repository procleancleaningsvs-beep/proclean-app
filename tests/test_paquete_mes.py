from datetime import date

from modules.carrier.paquete_mes import (
    paquete_futuro_aun_no_utilizable,
    paquete_vigente_payment_tuple,
    paquete_vigente_ym_str,
    tope_ultimo_mes_pago_utilizable,
)


def test_abril_2026_antes_del_17_vigente_febrero():
    inhabiles: set[date] = set()
    d = date(2026, 4, 10)
    assert paquete_vigente_payment_tuple(d, inhabiles) == (2026, 2)
    assert paquete_vigente_ym_str(d, inhabiles) == "2026-02"


def test_abril_2026_despues_corte_vigente_marzo():
    inhabiles: set[date] = set()
    d = date(2026, 4, 20)
    assert paquete_vigente_payment_tuple(d, inhabiles) == (2026, 3)


def test_tope_mes_pago_abril_es_marzo():
    assert tope_ultimo_mes_pago_utilizable(date(2026, 4, 15)) == (2026, 3)


def test_abril_2026_no_utilizable_como_paquete():
    assert paquete_futuro_aun_no_utilizable("2026-04", date(2026, 4, 15))
