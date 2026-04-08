"""
Partición de bases entre ISR (mes) y Art. 174 LISR (finiquito).

Regla central (modos total_gravable y aguinaldo_todo_gravable):
  base_art174 = excedentes sobre exentos UMA (aguinaldo 30, prima vac. 15) + prima dominical + PTU
  base_isr_mes = total_percepciones - base_art174

Modo correcto_fiscal: lo exento no se grava; Art. 174 solo sobre gravados de ag/prima vac;
  base ISR (mes) = sueldo + séptimo + vacaciones (sin mezclar Art. 174).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from modules.finiquitos.uma_exentos import limite_exento_aguinaldo_uma, limite_exento_prima_vacacional_uma

D0 = Decimal("0")


def _q(x: Decimal, q: Decimal = Decimal("0.01")) -> Decimal:
    from decimal import ROUND_HALF_UP

    return x.quantize(q, rounding=ROUND_HALF_UP)


ModoParticion = Literal["total_gravable", "aguinaldo_todo_gravable", "correcto_fiscal"]


@dataclass(frozen=True)
class ParticionISR:
    """Resultado de partición para auditoría y cálculo."""

    total_percepciones: Decimal
    uma_diaria: Decimal
    limite_exento_aguinaldo: Decimal
    limite_exento_prima_vacacional: Decimal
    aguinaldo: Decimal
    excedente_aguinaldo: Decimal
    prima_vacacional: Decimal
    excedente_prima_vacacional: Decimal
    excedente_prima_dominical: Decimal
    excedente_ptu: Decimal
    base_art174: Decimal
    base_isr_mes: Decimal
    modo: ModoParticion
    aguinaldo_exento_aplicado: Decimal
    prima_vac_exenta_aplicada: Decimal


def particionar_bases_total_gravable(
    *,
    total_percepciones: Decimal,
    aguinaldo: Decimal,
    prima_vacacional: Decimal,
    prima_dominical: Decimal,
    ptu: Decimal,
    fecha_referencia: date,
    modo: Literal["total_gravable", "aguinaldo_todo_gravable"],
) -> ParticionISR:
    uma_d = limite_exento_aguinaldo_uma(fecha_referencia) / Decimal("30")
    lim_ag = limite_exento_aguinaldo_uma(fecha_referencia)
    lim_pv = limite_exento_prima_vacacional_uma(fecha_referencia)

    exc_ag = _q(max(D0, aguinaldo - lim_ag))
    exc_pv = _q(max(D0, prima_vacacional - lim_pv))
    exc_pd = _q(max(D0, prima_dominical))
    exc_ptu = _q(max(D0, ptu))

    ag_ex_apl = _q(min(aguinaldo, lim_ag))
    pv_ex_apl = _q(min(prima_vacacional, lim_pv))

    base_174 = _q(exc_ag + exc_pv + exc_pd + exc_ptu)
    base_mes = _q(total_percepciones - base_174)

    return ParticionISR(
        total_percepciones=_q(total_percepciones),
        uma_diaria=uma_d,
        limite_exento_aguinaldo=lim_ag,
        limite_exento_prima_vacacional=lim_pv,
        aguinaldo=_q(aguinaldo),
        excedente_aguinaldo=exc_ag,
        prima_vacacional=_q(prima_vacacional),
        excedente_prima_vacacional=exc_pv,
        excedente_prima_dominical=exc_pd,
        excedente_ptu=exc_ptu,
        base_art174=base_174,
        base_isr_mes=base_mes,
        modo=modo,
        aguinaldo_exento_aplicado=ag_ex_apl,
        prima_vac_exenta_aplicada=pv_ex_apl,
    )


def particionar_bases_correcto_fiscal(
    *,
    sueldo: Decimal,
    septimo: Decimal,
    vacaciones: Decimal,
    aguinaldo: Decimal,
    prima_vacacional: Decimal,
    prima_dominical: Decimal,
    ptu: Decimal,
    fecha_referencia: date,
) -> ParticionISR:
    """Exentos UMA: lo gravado de ag/prima vac va a Art. 174; ISR (mes) solo ordinario."""
    uma_d = limite_exento_aguinaldo_uma(fecha_referencia) / Decimal("30")
    lim_ag = limite_exento_aguinaldo_uma(fecha_referencia)
    lim_pv = limite_exento_prima_vacacional_uma(fecha_referencia)

    ag_ex = _q(min(aguinaldo, lim_ag))
    ag_gr = _q(max(D0, aguinaldo - ag_ex))
    pv_ex = _q(min(prima_vacacional, lim_pv))
    pv_gr = _q(max(D0, prima_vacacional - pv_ex))

    exc_pd = _q(max(D0, prima_dominical))
    exc_ptu = _q(max(D0, ptu))

    base_174 = _q(ag_gr + pv_gr + exc_pd + exc_ptu)
    base_mes = _q(sueldo + septimo + vacaciones)
    total_p = _q(sueldo + septimo + vacaciones + prima_vacacional + aguinaldo + prima_dominical + ptu)

    return ParticionISR(
        total_percepciones=total_p,
        uma_diaria=uma_d,
        limite_exento_aguinaldo=lim_ag,
        limite_exento_prima_vacacional=lim_pv,
        aguinaldo=_q(aguinaldo),
        excedente_aguinaldo=ag_gr,
        prima_vacacional=_q(prima_vacacional),
        excedente_prima_vacacional=pv_gr,
        excedente_prima_dominical=exc_pd,
        excedente_ptu=exc_ptu,
        base_art174=base_174,
        base_isr_mes=base_mes,
        modo="correcto_fiscal",
        aguinaldo_exento_aplicado=ag_ex,
        prima_vac_exenta_aplicada=pv_ex,
    )
