"""Edición libre v2: solo desglose/placeholders DOCX, ISR automático, deducciones extra empaquetadas."""

from __future__ import annotations

import copy
import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from modules.finiquitos.calc import (
    _ajuste_neto_permitido,
    _mapear_pdf,
    calc_isr_art174_con_detalle,
    calc_isr_mes_semanal_mensualizado,
    format_importe,
    isr_art96,
)

logger = logging.getLogger(__name__)

D0 = Decimal("0")

MAX_PERC_EXPORT = 8
MAX_DED_EXPORT = 6

SLOT_ORDER_P = ("t1", "t2", "t3", "t5", "t6", "n7", "np")
MAIN_SLOTS_P = frozenset(SLOT_ORDER_P)

LAB_BY_SLOT: dict[str, str] = {
    "t1": "sueldo",
    "t2": "septimo_dia",
    "t3": "vacaciones_a_tiempo",
    "t5": "prima_vacacional",
    "t6": "aguinaldo",
}

DEFAULT_P_ROWS: tuple[tuple[str, str, str], ...] = (
    ("t1", "1", "Sueldo"),
    ("t2", "3", "Séptimo día"),
    ("t3", "19", "Vacaciones a tiempo"),
    ("t5", "22", "Prima vacacional"),
    ("t6", "24", "Aguinaldo"),
    ("n7", "29", "Prima de antigüedad"),
    ("np", "", ""),
)


def _q(x: Decimal, q: Decimal = Decimal("0.01")) -> Decimal:
    return x.quantize(q, rounding=ROUND_HALF_UP)


def _dec(x: Any) -> Decimal:
    try:
        return Decimal(str(x))
    except Exception:
        return D0


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("1", "true", "yes", "si", "on")


def _norm_fiscal(s: str) -> str:
    t = (s or "").strip().lower()
    return "exento" if t == "exento" else "gravable"


def _parse_num_sort(s: str) -> tuple[int, str]:
    t = (s or "").strip()
    if not t:
        return (10**9, "")
    try:
        return (int(t, 10), t)
    except ValueError:
        return (10**9, t)


def _row_p_active(row: dict[str, Any]) -> bool:
    if _bool(row.get("eliminado")):
        return False
    return bool(
        str(row.get("num") or "").strip()
        or str(row.get("nom") or "").strip()
        or _dec(row.get("monto")) != D0
    )


def _row_d_active(row: dict[str, Any]) -> bool:
    if _bool(row.get("eliminado")):
        return False
    return bool(
        str(row.get("num") or "").strip()
        or str(row.get("nom") or "").strip()
        or _dec(row.get("monto")) != D0
    )


def _normalize_percepcion_row(row: dict[str, Any]) -> dict[str, Any]:
    slot = str(row.get("slot") or "").strip().lower()
    if slot and slot not in MAIN_SLOTS_P:
        slot = ""
    lab_key = row.get("labKey")
    lk = str(lab_key).strip() if lab_key is not None else ""
    if not lk:
        lk = LAB_BY_SLOT.get(slot, "") or ""
    return {
        "slot": slot,
        "labKey": lk or None,
        "num": str(row.get("num") or "").strip(),
        "nom": str(row.get("nom") or "").strip(),
        "monto": float(row.get("monto") or 0),
        "fiscal": _norm_fiscal(str(row.get("fiscal") or "gravable")),
    }


def _parse_percepciones_v2_list(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        out.append(_normalize_percepcion_row(row))
    return out


def _parse_deducciones_extra_v2(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "id": str(row.get("id") or ""),
                "num": str(row.get("num") or "").strip(),
                "nom": str(row.get("nom") or "").strip(),
                "monto": float(row.get("monto") or 0),
            }
        )
    return out


def _combine_percepciones_v2(dm: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _parse_percepciones_v2_list(dm.get("percepciones"))
    legacy = _parse_percepciones_v2_list(dm.get("percepciones_extra"))
    for r in legacy:
        if r.get("slot") and str(r["slot"]) in MAIN_SLOTS_P:
            continue
        r = dict(r)
        r.setdefault("slot", "")
        rows.append(r)
    return rows


def _recalc_isr_v2(
    *,
    rows_p: list[dict[str, Any]],
    sin_isr: bool,
    ultimo_mensual: Decimal,
    fecha_emision: Any,
) -> tuple[Decimal, Decimal, Decimal]:
    if sin_isr:
        return D0, D0, D0
    base_mes = D0
    base_174 = D0
    pa_grav = D0
    for r in rows_p:
        m = _dec(r.get("monto"))
        if _norm_fiscal(str(r.get("fiscal"))) != "gravable":
            continue
        sl = str(r.get("slot") or "").strip().lower()
        if sl == "n7":
            pa_grav += m
            continue
        if sl in ("t5", "t6"):
            base_mes += m
            base_174 += m
        elif sl in MAIN_SLOTS_P:
            base_mes += m
        else:
            base_mes += m
            base_174 += m
    isr_mes_neto = calc_isr_mes_semanal_mensualizado(base_mes, fecha_emision, ultimo_mensual)["isr_mes_neto"]
    isr_174, _ = calc_isr_art174_con_detalle(base_174, ultimo_mensual)
    if pa_grav > 0:
        isr_ult = isr_art96(ultimo_mensual, "mensual")
        if pa_grav >= ultimo_mensual and ultimo_mensual > 0:
            tasa_sep = _q(isr_ult / ultimo_mensual)
            isr_sep = _q(pa_grav * tasa_sep)
        else:
            isr_sep = isr_art96(pa_grav, "mensual")
    else:
        isr_sep = D0
    return _q(isr_mes_neto), _q(isr_174), _q(isr_sep)


def _default_p_rows_from_calc(calc: dict[str, Any]) -> list[dict[str, Any]]:
    lab = calc.get("laboral") or {}
    pa = _dec(lab.get("prima_antiguedad_monto"))
    rows: list[dict[str, Any]] = []
    for slot, num, nom in DEFAULT_P_ROWS:
        lk = LAB_BY_SLOT.get(slot)
        m = float(lab.get(lk) or 0) if lk else 0.0
        if slot == "n7":
            m = float(pa or 0)
            if m <= 0:
                num, nom = "", ""
        if slot == "np":
            m = float(lab.get("prima_dominical") or 0)
        rows.append(
            {
                "slot": slot,
                "labKey": lk,
                "num": num,
                "nom": nom,
                "monto": m,
                "fiscal": "gravable",
            }
        )
    return rows


def _sort_meta_percepciones(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda r: (_parse_num_sort(str(r.get("num") or ""))[0], str(r.get("slot") or "")),
    )


def validate_desglose_manual_v2_dm(dm: dict[str, Any]) -> str | None:
    """Validación previa al cálculo (solo payload)."""
    if not isinstance(dm, dict) or dm.get("v") != 2:
        return None
    combined = _combine_percepciones_v2(dm)
    active_p = [r for r in combined if _row_p_active(r)]
    if len(active_p) > MAX_PERC_EXPORT:
        return (
            f"Hay {len(active_p)} percepciones activas; la plantilla admite como máximo {MAX_PERC_EXPORT}. "
            "Elimine o combine conceptos antes de continuar."
        )
    ded = _parse_deducciones_extra_v2(dm.get("deducciones_extra"))
    active_d = [r for r in ded if _row_d_active(r)]
    if len(active_d) > MAX_DED_EXPORT:
        return (
            f"Hay {len(active_d)} deducciones adicionales activas; en conjunto con el ISR del formato "
            f"el límite práctico es {MAX_DED_EXPORT} líneas compactas. Reduzca deducciones adicionales."
        )
    return None


def apply_desglose_manual(
    calc: dict[str, Any],
    dm_or_filas: Any,
    *,
    entrada: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    v1: dm_or_filas es list[dict] (filas legacy).
    v2: dm_or_filas es dict con v==2, percepciones, deducciones_extra, sin_isr.
    """
    if isinstance(dm_or_filas, dict) and dm_or_filas.get("v") == 2:
        return _apply_v2(calc, dm_or_filas, entrada=entrada or {})
    if isinstance(dm_or_filas, list):
        return _apply_v1_legacy(calc, dm_or_filas)
    if isinstance(dm_or_filas, dict):
        filas = dm_or_filas.get("filas")
        if isinstance(filas, list) and filas:
            return _apply_v1_legacy(calc, filas)
    return copy.deepcopy(calc)


def _apply_v1_legacy(calc: dict[str, Any], filas: list[Any]) -> dict[str, Any]:
    """Compatibilidad: filas con tipo P/D/ISR y claves base (sin v2)."""
    out = copy.deepcopy(calc)
    if not filas:
        return out

    lab = out["laboral"]
    fis = out["fiscal"]
    extra_p = D0
    extra_d = D0

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
    out.setdefault("edicion_libre_desglose_meta", {})
    out["edicion_libre_desglose_meta"] = {"extra_percepciones": float(extra_p), "extra_deducciones": float(extra_d), "v": 1}
    logger.info("Finiquito: desglose manual v1 (%d filas).", len(filas))
    return out


def _zero_labor_keys_for_v2(lab: dict[str, Any]) -> None:
    for lk in LAB_BY_SLOT.values():
        if lk:
            lab[lk] = 0.0
    lab["prima_antiguedad_monto"] = 0.0
    lab["prima_dominical"] = 0.0


def _apply_lab_from_rows_v2(lab: dict[str, Any], rows_p: list[dict[str, Any]]) -> None:
    _zero_labor_keys_for_v2(lab)
    for row in rows_p:
        m = float(row.get("monto") or 0)
        lk = str(row.get("labKey") or "").strip() or LAB_BY_SLOT.get(str(row.get("slot") or "").strip().lower(), "")
        sl = str(row.get("slot") or "").strip().lower()
        if lk:
            lab[lk] = m
        elif sl == "n7":
            lab["prima_antiguedad_monto"] = m
        elif sl == "np":
            lab["prima_dominical"] = m


def _apply_v2(calc: dict[str, Any], dm: dict[str, Any], *, entrada: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(calc)
    sin_isr = _bool(dm.get("sin_isr"))
    combined = _combine_percepciones_v2(dm)
    active = [r for r in combined if _row_p_active(r)]
    if not active:
        rows_p = _default_p_rows_from_calc(out)
    else:
        rows_p = active

    ded_ex = [r for r in _parse_deducciones_extra_v2(dm.get("deducciones_extra")) if _row_d_active(r)]

    lab = out["laboral"]
    fis = out["fiscal"]
    lab["ptu"] = 0.0
    ent = entrada or {}

    ultimo_mensual = _dec(ent.get("salario_mensual_capturado"))
    if ultimo_mensual <= 0:
        sal_d = _dec(lab.get("salario_diario"))
        ultimo_mensual = _q(sal_d * Decimal("30.4")) if sal_d > 0 else D0

    fe_raw = ent.get("emision") or ent.get("fecha_emision")
    try:
        from datetime import date as _date

        if hasattr(fe_raw, "year"):
            fecha_emision = fe_raw  # type: ignore[assignment]
        else:
            s = str(fe_raw or "")[:10]
            fecha_emision = _date.fromisoformat(s) if len(s) >= 10 else _date.today()
    except Exception:
        from datetime import date as _date

        fecha_emision = _date.today()

    isr_mes, isr_174, isr_sep = _recalc_isr_v2(
        rows_p=rows_p,
        sin_isr=sin_isr,
        ultimo_mensual=ultimo_mensual,
        fecha_emision=fecha_emision,
    )

    _apply_lab_from_rows_v2(lab, rows_p)

    extra_d = sum((_dec(d.get("monto")) for d in ded_ex), D0)
    total_perc = sum((_dec(r.get("monto")) for r in rows_p), D0)
    total_perc = _q(total_perc)

    fis["isr_mes_neto"] = float(isr_mes)
    fis["isr_ordinario_neto"] = float(isr_mes)
    fis["isr_antes_subsidio_periodo"] = float(isr_mes)
    fis["isr_ordinario_antes_subsidio"] = float(isr_mes)
    fis["isr_art174"] = float(isr_174)
    fis["isr_separacion"] = float(isr_sep)

    ded_reales = _q(isr_mes + isr_174 + isr_sep + extra_d)
    neto_prev = _q(total_perc - ded_reales)
    neto_final, ajuste_neto = _ajuste_neto_permitido(neto_prev)
    extra_99 = _q(abs(ajuste_neto)) if ajuste_neto > D0 else D0
    suma_43_45_99 = _q(isr_mes + isr_174 + extra_99)
    suma_d_num = _q(suma_43_45_99 + isr_sep + extra_d)

    tot = out["totales"]
    display_total_perc = _q(total_perc + (abs(ajuste_neto) if ajuste_neto < 0 else D0))
    tot["total_percepciones"] = float(display_total_perc)
    tot["total_deducciones_reales"] = float(ded_reales)
    tot["suma_deducciones_43_45_99"] = float(suma_43_45_99)
    tot["ajuste_neto"] = float(ajuste_neto)
    tot["neto_final"] = float(neto_final)

    pf = _mapear_pdf(
        isr_antes_subsidio=isr_mes,
        isr_mes_neto=isr_mes,
        isr_174=isr_174,
        isr_sep=isr_sep,
        ajuste_neto=ajuste_neto,
    )
    pf["suma_d"] = format_importe(suma_d_num)
    out["pdf_filas"] = pf

    rows_meta = _sort_meta_percepciones([dict(r) for r in rows_p])
    main_slots_list: list[dict[str, Any]] = []
    extra_list: list[dict[str, Any]] = []
    for r in rows_meta:
        sl = str(r.get("slot") or "").strip().lower()
        if sl in MAIN_SLOTS_P:
            main_slots_list.append(dict(r))
        else:
            rr = dict(r)
            rr.setdefault("slot", "")
            extra_list.append(rr)

    out["edicion_libre_desglose_meta"] = {
        "v": 2,
        "sin_isr": sin_isr,
        "extra_deducciones": float(extra_d),
        "extra_percepciones": float(sum(_dec(x.get("monto")) for x in extra_list)),
        "percepciones": main_slots_list,
        "percepciones_extra": extra_list,
        "deducciones_extra": ded_ex,
    }
    logger.info("Finiquito: desglose manual v2 aplicado.")
    return out


def merge_desglose_v2_into_entrada_for_history(entrada: dict[str, Any], desglose_manual: dict[str, Any]) -> None:
    """Mantiene desglose_manual completo en entrada para rehidratar."""
    if not isinstance(entrada, dict) or not isinstance(desglose_manual, dict):
        return
    if desglose_manual.get("v") == 2:
        entrada["desglose_manual"] = desglose_manual


def build_default_percepciones_for_form(calc: dict[str, Any]) -> list[dict[str, Any]]:
    """JSON para el formulario al activar edición libre (7 filas)."""
    return _default_p_rows_from_calc(calc)
