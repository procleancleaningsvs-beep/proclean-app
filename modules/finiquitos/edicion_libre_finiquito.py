"""Aplicación de importes manuales sobre un resultado de finiquito ya calculado (fase 1)."""

from __future__ import annotations

import copy
import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from modules.finiquitos.calc import _ajuste_neto_permitido, _mapear_pdf

logger = logging.getLogger(__name__)

D0 = Decimal("0")


def _q(x: Decimal, q: Decimal = Decimal("0.01")) -> Decimal:
    return x.quantize(q, rounding=ROUND_HALF_UP)


def _dec(x: Any) -> Decimal:
    try:
        return Decimal(str(x))
    except Exception:
        return D0


def merge_finiquito_calc_with_manual(calc: dict[str, Any], manual: dict[str, Any]) -> dict[str, Any]:
    """
    Copia el dict de cálculo y aplica montos manuales conocidos; recalcula totales, pdf_filas y
    fragmentos de auditoría usados en vista/PDF.
    """
    out = copy.deepcopy(calc)
    if not manual:
        return out

    lab = out["laboral"]
    fis = out["fiscal"]

    lab_map = {
        "sueldo": "sueldo",
        "septimo_dia": "septimo_dia",
        "vacaciones": "vacaciones_a_tiempo",
        "prima_vacacional": "prima_vacacional",
        "aguinaldo": "aguinaldo",
        "prima_antiguedad": "prima_antiguedad_monto",
        "prima_dominical": "prima_dominical",
        "ptu": "ptu",
    }
    for src, dst in lab_map.items():
        if src not in manual:
            continue
        try:
            lab[dst] = float(manual[src])
        except (TypeError, ValueError):
            continue

    if "isr_ordinario" in manual:
        try:
            v = float(manual["isr_ordinario"])
        except (TypeError, ValueError):
            v = float(fis.get("isr_mes_neto") or 0)
        for k in (
            "isr_mes_neto",
            "isr_ordinario_neto",
            "isr_antes_subsidio_periodo",
            "isr_ordinario_antes_subsidio",
        ):
            if k in fis:
                fis[k] = v
    if "isr_art174" in manual:
        try:
            fis["isr_art174"] = float(manual["isr_art174"])
        except (TypeError, ValueError):
            pass
    if "isr_separacion" in manual:
        try:
            fis["isr_separacion"] = float(manual["isr_separacion"])
        except (TypeError, ValueError):
            pass

    _recompute_totales_y_pdf(out)
    _patch_auditoria_resumen(out)
    logger.info("Finiquito: aplicados importes_manuales (edición libre), keys=%s", sorted(manual.keys()))
    return out


def _recompute_totales_y_pdf(c: dict[str, Any]) -> None:
    lab = c["laboral"]
    fis = c["fiscal"]
    sueldo = _dec(lab.get("sueldo"))
    sept = _dec(lab.get("septimo_dia"))
    vac = _dec(lab.get("vacaciones_a_tiempo"))
    pv = _dec(lab.get("prima_vacacional"))
    ag = _dec(lab.get("aguinaldo"))
    pa = _dec(lab.get("prima_antiguedad_monto"))
    pdom = _dec(lab.get("prima_dominical"))
    ptu = _dec(lab.get("ptu"))
    total_perc = _q(sueldo + sept + vac + pv + ag + pa + pdom + ptu)

    isr_mes = _dec(fis.get("isr_mes_neto"))
    isr174 = _dec(fis.get("isr_art174"))
    isr_sep = _dec(fis.get("isr_separacion"))
    ded_reales = _q(isr_mes + isr174 + isr_sep)
    neto_prev = _q(total_perc - ded_reales)
    neto_final, ajuste_neto = _ajuste_neto_permitido(neto_prev)
    extra_99 = _q(abs(ajuste_neto)) if ajuste_neto > D0 else D0
    suma_43_45_99 = _q(isr_mes + isr174 + extra_99)

    tot = c["totales"]
    tot["total_percepciones"] = float(total_perc)
    tot["total_deducciones_reales"] = float(ded_reales)
    tot["suma_deducciones_43_45_99"] = float(suma_43_45_99)
    tot["ajuste_neto"] = float(ajuste_neto)
    tot["neto_final"] = float(neto_final)

    c["pdf_filas"] = _mapear_pdf(
        isr_antes_subsidio=isr_mes,
        isr_mes_neto=isr_mes,
        isr_174=isr174,
        isr_sep=isr_sep,
        ajuste_neto=ajuste_neto,
    )


def _patch_auditoria_resumen(c: dict[str, Any]) -> None:
    aud = c.setdefault("auditoria", {})
    lab = c["laboral"]
    fis = c["fiscal"]
    tot = c["totales"]

    aud["percepciones_finiquito"] = [
        {"concepto": "Sueldo", "monto": float(lab.get("sueldo") or 0)},
        {"concepto": "Séptimo día", "monto": float(lab.get("septimo_dia") or 0)},
        {"concepto": "Vacaciones", "monto": float(lab.get("vacaciones_a_tiempo") or 0)},
        {"concepto": "Prima vacacional", "monto": float(lab.get("prima_vacacional") or 0)},
        {"concepto": "Aguinaldo", "monto": float(lab.get("aguinaldo") or 0)},
        {"concepto": "Prima de antigüedad", "monto": float(lab.get("prima_antiguedad_monto") or 0)},
        {"concepto": "Prima dominical", "monto": float(lab.get("prima_dominical") or 0)},
        {"concepto": "PTU", "monto": float(lab.get("ptu") or 0)},
    ]

    sf = aud.setdefault("seccion_fiscal", {})
    A = sf.setdefault("A_resumen_percepciones", {})
    A["sueldo"] = float(lab.get("sueldo") or 0)
    A["septimo_dia"] = float(lab.get("septimo_dia") or 0)
    A["vacaciones"] = float(lab.get("vacaciones_a_tiempo") or 0)
    A["aguinaldo"] = float(lab.get("aguinaldo") or 0)
    A["prima_vacacional"] = float(lab.get("prima_vacacional") or 0)
    A["prima_dominical"] = float(lab.get("prima_dominical") or 0)
    A["ptu"] = float(lab.get("ptu") or 0)
    A["prima_antiguedad"] = float(lab.get("prima_antiguedad_monto") or 0)
    A["total_percepciones"] = float(tot.get("total_percepciones") or 0)

    F = sf.setdefault("F_ajuste_neto", {})
    npv = _q(
        _dec(tot.get("total_percepciones"))
        - _dec(fis.get("isr_mes_neto"))
        - _dec(fis.get("isr_art174"))
        - _dec(fis.get("isr_separacion"))
    )
    F["neto_previo"] = float(npv)
    F["ajuste"] = float(tot.get("ajuste_neto") or 0)
    F["neto_final"] = float(tot.get("neto_final") or 0)
    aud["ajuste_neto"] = dict(F)

    isr_blk = aud.setdefault("isr", {})
    isr_blk["isr_periodo_41_y_45"] = float(fis.get("isr_mes_neto") or 0)
    isr_blk["isr_mes_neto_periodo"] = float(fis.get("isr_mes_neto") or 0)
    isr_blk["isr_art174"] = float(fis.get("isr_art174") or 0)
    isr_blk["isr_separacion"] = float(fis.get("isr_separacion") or 0)
