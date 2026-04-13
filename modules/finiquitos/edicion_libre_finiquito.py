"""Edición libre limitada al desglose: filas editables + extras y recálculo de totales/PDF."""

from __future__ import annotations

import copy
import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from modules.finiquitos.calc import _ajuste_neto_permitido, _mapear_pdf, format_importe

logger = logging.getLogger(__name__)

D0 = Decimal("0")

# Claves enviadas desde el desglose (frontend) → campo en laboral
LAB_POR_CLAVE: dict[str, str] = {
    "sueldo": "sueldo",
    "septimo_dia": "septimo_dia",
    "vacaciones_a_tiempo": "vacaciones_a_tiempo",
    "prima_vacacional": "prima_vacacional",
    "aguinaldo": "aguinaldo",
    "prima_antiguedad_monto": "prima_antiguedad_monto",
    "prima_dominical": "prima_dominical",
    "ptu": "ptu",
}

_LAB_SUM_KEYS = (
    "sueldo",
    "septimo_dia",
    "vacaciones_a_tiempo",
    "prima_vacacional",
    "aguinaldo",
    "prima_antiguedad_monto",
    "prima_dominical",
    "ptu",
)


def _q(x: Decimal, q: Decimal = Decimal("0.01")) -> Decimal:
    return x.quantize(q, rounding=ROUND_HALF_UP)


def _dec(x: Any) -> Decimal:
    try:
        return Decimal(str(x))
    except Exception:
        return D0


def apply_desglose_manual(calc: dict[str, Any], filas: list[Any]) -> dict[str, Any]:
    """
    Aplica filas del desglose manual (solo montos y extras).
    Cada fila: {id, tipo: P|D|ISR, concepto, monto, clave?}
    - tipo P con id extra-p:* → suma a percepciones extra
    - tipo D con id extra-d:* → suma a deducciones extra
    - tipo ISR con clave isr_ordinario | isr_art174 | isr_separacion
    """
    out = copy.deepcopy(calc)
    if not filas:
        return out

    lab = out["laboral"]
    fis = out["fiscal"]
    extra_p = D0
    extra_d = D0

    for row in filas:
        if not isinstance(row, dict):
            continue
        tipo = str(row.get("tipo") or "").strip().upper()
        rid = str(row.get("id") or "")
        monto = _dec(row.get("monto"))

        if tipo == "P" and rid.startswith("extra-p:"):
            extra_p += monto
            continue
        if tipo == "D" and rid.startswith("extra-d:"):
            extra_d += monto
            continue

        if tipo == "P" and rid.startswith("base:P:"):
            ck = str(row.get("clave") or "").strip()
            lk = LAB_POR_CLAVE.get(ck)
            if lk:
                lab[lk] = float(monto)
            continue

        if tipo == "ISR" and rid.startswith("base:ISR:"):
            ck = str(row.get("clave") or "").strip()
            v = float(monto)
            if ck == "isr_ordinario":
                for k in (
                    "isr_mes_neto",
                    "isr_ordinario_neto",
                    "isr_antes_subsidio_periodo",
                    "isr_ordinario_antes_subsidio",
                ):
                    if k in fis:
                        fis[k] = v
            elif ck == "isr_art174":
                fis["isr_art174"] = v
            elif ck == "isr_separacion":
                fis["isr_separacion"] = v

    total_perc = D0
    for k in _LAB_SUM_KEYS:
        total_perc += _dec(lab.get(k))
    total_perc = _q(total_perc + extra_p)

    isr_mes = _dec(fis.get("isr_mes_neto"))
    isr174 = _dec(fis.get("isr_art174"))
    isr_sep = _dec(fis.get("isr_separacion"))
    ded_reales = _q(isr_mes + isr174 + isr_sep + extra_d)
    neto_prev = _q(total_perc - ded_reales)
    neto_final, ajuste_neto = _ajuste_neto_permitido(neto_prev)
    extra_99 = _q(abs(ajuste_neto)) if ajuste_neto > D0 else D0
    suma_43_45_99 = _q(isr_mes + isr174 + extra_99)
    suma_d_num = _q(suma_43_45_99 + isr_sep + extra_d)

    tot = out["totales"]
    tot["total_percepciones"] = float(total_perc)
    tot["total_deducciones_reales"] = float(ded_reales)
    tot["suma_deducciones_43_45_99"] = float(suma_43_45_99)
    tot["ajuste_neto"] = float(ajuste_neto)
    tot["neto_final"] = float(neto_final)

    pf = _mapear_pdf(
        isr_antes_subsidio=isr_mes,
        isr_mes_neto=isr_mes,
        isr_174=isr174,
        isr_sep=isr_sep,
        ajuste_neto=ajuste_neto,
    )
    pf["suma_d"] = format_importe(suma_d_num)
    out["pdf_filas"] = pf

    _patch_auditoria_desglose(out, neto_prev_sin_ajuste=float(neto_prev), extra_p=float(extra_p), extra_d=float(extra_d))
    logger.info("Finiquito: desglose manual aplicado (%d filas).", len(filas))
    return out


def _patch_auditoria_desglose(c: dict[str, Any], *, neto_prev_sin_ajuste: float, extra_p: float, extra_d: float) -> None:
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
    if extra_p > 0:
        aud["percepciones_finiquito"].append({"concepto": "Otros (desglose manual)", "monto": extra_p})

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
    F["neto_previo"] = float(neto_prev_sin_ajuste)
    F["ajuste"] = float(tot.get("ajuste_neto") or 0)
    F["neto_final"] = float(tot.get("neto_final") or 0)
    aud["ajuste_neto"] = dict(F)

    isr_blk = aud.setdefault("isr", {})
    isr_blk["isr_periodo_41_y_45"] = float(fis.get("isr_mes_neto") or 0)
    isr_blk["isr_mes_neto_periodo"] = float(fis.get("isr_mes_neto") or 0)
    isr_blk["isr_art174"] = float(fis.get("isr_art174") or 0)
    isr_blk["isr_separacion"] = float(fis.get("isr_separacion") or 0)

    c.setdefault("edicion_libre_desglose_meta", {})
    c["edicion_libre_desglose_meta"] = {"extra_percepciones": extra_p, "extra_deducciones": extra_d}
