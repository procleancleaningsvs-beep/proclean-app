"""
Cálculo de finiquito (LFT / LISR 2026).

Política: prima de antigüedad con fracción de año proporcional exacta (importe laboral);
documentado como criterio por defecto en `prima_antiguedad_monto`.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any, Literal

from modules.finiquitos.config import (
    ISR_TABLA_MENSUAL_2026,
    ISR_TABLA_QUINCENAL_2026,
    SUBSIDIO_LIMITE_MENSUAL_2026,
    SUBSIDIO_PCT_2026,
    SUBSIDIO_PCT_ENERO_2026,
    SMG_FRONTERA_2026,
    SMG_GENERAL_2026,
    UMA_DIARIA_2026,
    UMA_MENSUAL_2025,
    UMA_MENSUAL_2026,
)

D2 = Decimal("0.01")
D0 = Decimal("0")


def _q(x: Decimal) -> Decimal:
    return x.quantize(D2, rounding=ROUND_HALF_UP)


def add_years_safe(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, month=2, day=28)


def full_years_between(start: date, end: date) -> int:
    """Años completos de calendario entre start y end (end >= start)."""
    if end < start:
        return 0
    y = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        y -= 1
    return y


def ultimo_aniversario(ingreso: date, baja: date) -> date:
    """Última fecha de aniversario de ingreso que sea <= baja."""
    if baja < ingreso:
        return ingreso
    y = full_years_between(ingreso, baja)
    return add_years_safe(ingreso, y)


def dias_servicio(ingreso: date, baja: date) -> int:
    return (baja - ingreso).days + 1


def anios_servicio_exactos(ingreso: date, baja: date) -> Decimal:
    """Años de servicio como fracción exacta (días / 365.25)."""
    d = (baja - ingreso).days
    return _q(Decimal(d) / Decimal("365.25"))


def dias_vacaciones_ley_por_anio_servicio(anio_servicio: int) -> int:
    """
    Días de vacaciones anuales según art. 76 LFT (tabla 2026 del requerimiento).
    anio_servicio: 1 = primer año, 2 = segundo, etc.
    """
    if anio_servicio <= 0:
        return 12
    if anio_servicio <= 5:
        return 10 + 2 * anio_servicio  # 12,14,16,18,20
    if anio_servicio <= 10:
        return 22
    if anio_servicio <= 15:
        return 24
    if anio_servicio <= 20:
        return 26
    if anio_servicio <= 25:
        return 28
    if anio_servicio <= 30:
        return 30
    return 30


def isr_art96(base_gravada: Decimal, periodicidad: Literal["quincenal", "mensual", "15_dias"]) -> Decimal:
    if base_gravada <= 0:
        return D0
    tab = ISR_TABLA_QUINCENAL_2026 if periodicidad in ("quincenal", "15_dias") else ISR_TABLA_MENSUAL_2026
    bg = _q(base_gravada)
    for lim_inf, lim_sup, cuota, pct in tab:
        if bg < lim_inf:
            continue
        if lim_sup is not None and bg > lim_sup:
            continue
        excedente = bg - lim_inf
        isr = cuota + excedente * (pct / Decimal("100"))
        return _q(isr)
    return D0


def subsidio_mensual_para_fecha(fecha_pago: date) -> Decimal:
    if fecha_pago.year == 2026 and fecha_pago.month == 1:
        return _q(UMA_MENSUAL_2025 * SUBSIDIO_PCT_ENERO_2026)
    return _q(UMA_MENSUAL_2026 * SUBSIDIO_PCT_2026)


def subsidio_periodo(
    fecha_pago: date,
    dias_periodo: Decimal,
    *,
    ingreso_mensual_equiv: Decimal,
) -> Decimal:
    """
    Subsidio al empleo para el periodo; no mezclar con pagos por separación en la base elegible.
    Elegibilidad: ingreso mensual equivalente (salario_diario * 30.4) vs límite mensual.
    Monto: UMA diaria (enero: UMA mensual 2025 / 30.4) × pct × días del periodo (coherente con ejemplo 264.30).
    """
    if ingreso_mensual_equiv <= 0 or ingreso_mensual_equiv > SUBSIDIO_LIMITE_MENSUAL_2026:
        return D0
    if fecha_pago.year == 2026 and fecha_pago.month == 1:
        uma_d = UMA_MENSUAL_2025 / Decimal("30.4")
        pct = SUBSIDIO_PCT_ENERO_2026
    else:
        uma_d = UMA_DIARIA_2026
        pct = SUBSIDIO_PCT_2026
    return _q(uma_d * pct * dias_periodo)


def anios_exentos_separacion(anios_exactos: Decimal) -> int:
    base = int(anios_exactos.to_integral_value(rounding=ROUND_DOWN))
    frac = anios_exactos - Decimal(base)
    if frac > Decimal("0.5"):
        base += 1
    return max(1, base)


def prima_antiguedad_aplica_separacion_voluntaria(ingreso: date, baja: date) -> bool:
    """Para este flujo: separación voluntaria solo aplica con 15 años o más."""
    return anios_servicio_exactos(ingreso, baja) >= Decimal("15")


def calcular_finiquito(
    *,
    ingreso: date,
    baja: date,
    fecha_emision: date,
    salario_diario: Decimal,
    zona: Literal["general", "frontera"],
    periodicidad_isr: Literal["quincenal", "mensual", "15_dias", "semanal_mensualizada"],
    modo: Literal["correcto_fiscal", "aguinaldo_todo_gravable"],
    dias_sueldo_pendientes: Decimal,
    septimos_pendientes: Decimal,
    dias_aguinaldo_politica: Decimal,
    prima_vacacional_pct: Decimal,
    vacaciones_ya_usadas: Decimal,
    aguinaldo_ya_pagado: Decimal,
    prima_vac_ya_pagada: Decimal,
    incluir_prima_antiguedad: bool,
    motivo_baja: str,
    salario_mensual_capturado: Decimal | None = None,
) -> dict[str, Any]:
    smg = SMG_GENERAL_2026 if zona == "general" else SMG_FRONTERA_2026

    ult_ann = ultimo_aniversario(ingreso, baja)
    aniversario_siguiente = add_years_safe(ult_ann, 1)
    dias_anio_ciclo = (aniversario_siguiente - ult_ann).days
    if dias_anio_ciclo <= 0:
        dias_anio_ciclo = 365
    # Días del ciclo vigente (incluye día de baja; alinea con ejemplo 163 días).
    dias_transcurridos_ciclo = max(0, (baja - ult_ann).days + 1)

    anios_completos_hasta_aniversario = full_years_between(ingreso, ult_ann)
    anio_servicio_vac = anios_completos_hasta_aniversario + 1
    dias_vac_anuales = dias_vacaciones_ley_por_anio_servicio(anio_servicio_vac)

    vac_devengadas = Decimal(dias_vac_anuales) * Decimal(dias_transcurridos_ciclo) / Decimal(dias_anio_ciclo)
    vac_pend = vac_devengadas - vacaciones_ya_usadas
    if vac_pend < 0:
        vac_pend = D0

    vacaciones_a_tiempo = _q(salario_diario * vac_pend)
    prima_vacacional = _q(vacaciones_a_tiempo * (prima_vacacional_pct / Decimal("100")))

    # Aguinaldo proporcional (año calendario de baja)
    inicio_anio = date(baja.year, 1, 1)
    dias_trabajados_anio = (baja - inicio_anio).days + 1
    dias_anio_cal = 366 if baja.year % 4 == 0 and (baja.year % 100 != 0 or baja.year % 400 == 0) else 365
    ag_prop = dias_aguinaldo_politica * Decimal(dias_trabajados_anio) / Decimal(dias_anio_cal)
    aguinaldo_bruto = _q(salario_diario * ag_prop)
    aguinaldo = aguinaldo_bruto - aguinaldo_ya_pagado
    if aguinaldo < 0:
        aguinaldo = D0
    aguinaldo = _q(aguinaldo)

    prima_vac_neta = prima_vacacional - prima_vac_ya_pagada
    if prima_vac_neta < 0:
        prima_vac_neta = D0
    prima_vac_neta = _q(prima_vac_neta)

    sueldo = _q(salario_diario * dias_sueldo_pendientes)
    septimo = _q(salario_diario * septimos_pendientes)

    anios_exact = anios_servicio_exactos(ingreso, baja)
    salario_tope = salario_diario if salario_diario < 2 * smg else 2 * smg
    prima_antig_monto = D0
    if incluir_prima_antiguedad and _prima_antiguedad_procede(motivo_baja, anios_exact):
        prima_antig_monto = _q(salario_tope * Decimal("12") * anios_exact)

    # Exento / gravado
    if modo == "aguinaldo_todo_gravable":
        ag_ex = D0
        ag_gr = aguinaldo
    else:
        ag_ex = _q(min(aguinaldo, 30 * smg))
        ag_gr = _q(max(D0, aguinaldo - ag_ex))

    pv_ex = _q(min(prima_vac_neta, 15 * smg))
    pv_gr = _q(max(D0, prima_vac_neta - pv_ex))

    anios_ex = anios_exentos_separacion(anios_exact)
    lim_sep = 90 * smg * Decimal(anios_ex)
    pa_ex = _q(min(prima_antig_monto, lim_sep))
    pa_gr = _q(max(D0, prima_antig_monto - pa_ex))

    bucket_ord_grav = _q(sueldo + septimo + vacaciones_a_tiempo)
    extra_art174 = _q(ag_gr + pv_gr)

    ingreso_mensual_equiv = salario_mensual_capturado if salario_mensual_capturado is not None else _q(salario_diario * Decimal("30.4"))
    ultimo_mensual = ingreso_mensual_equiv

    # Política operativa tipo CONTPAQ (intencional):
    # El trabajador cobra semanal, pero el ISR ordinario se determina
    # mensualizando la base gravable semanal y aplicando la tabla mensual art. 96.
    dias_periodo = Decimal("7")
    base_ordinaria_mensualizada = _q(bucket_ord_grav / dias_periodo * Decimal("30.4")) if bucket_ord_grav > 0 else D0
    isr_ordinario_mensualizado = isr_art96(base_ordinaria_mensualizada, "mensual")
    sub_mensualizado = subsidio_periodo(
        fecha_emision,
        Decimal("30.4"),
        ingreso_mensual_equiv=base_ordinaria_mensualizada,
    )
    sub_mensual_ap = _q(min(sub_mensualizado, isr_ordinario_mensualizado))

    # Retención efectiva del periodo semanal, prorrateada del cálculo mensualizado.
    isr_ord_antes = _q(isr_ordinario_mensualizado / Decimal("30.4") * dias_periodo)
    sub_ap = _q(sub_mensual_ap / Decimal("30.4") * dias_periodo)
    sub_ap = _q(min(sub_ap, isr_ord_antes))
    isr_ord_neto = _q(max(D0, isr_ord_antes - sub_ap))

    # Art 174: mensualización a 2 decimales para tasa efectiva (ejemplo oficial 91.66).
    if extra_art174 > 0:
        rem_m = _q(extra_art174 / Decimal("365") * Decimal("30.4"))
        iso = isr_art96(ultimo_mensual, "mensual")
        icon = isr_art96(ultimo_mensual + rem_m, "mensual")
        diff = max(D0, icon - iso)
        if rem_m == 0:
            tasa_174 = D0
        else:
            tasa_174 = (diff / rem_m).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        isr_174 = _q(extra_art174 * tasa_174)
    else:
        isr_174 = D0

    # Separación art 95/96
    if pa_gr > 0:
        isr_ult = isr_art96(ultimo_mensual, "mensual")
        if pa_gr >= ultimo_mensual:
            tasa_sep = D0 if ultimo_mensual == 0 else _q(isr_ult / ultimo_mensual)
            isr_sep = _q(pa_gr * tasa_sep)
        else:
            isr_sep = isr_art96(pa_gr, "mensual")
    else:
        isr_sep = D0

    total_perc = _q(sueldo + septimo + vacaciones_a_tiempo + prima_vac_neta + aguinaldo + prima_antig_monto)
    ded_reales = _q(isr_ord_neto + isr_174 + isr_sep)
    neto_prev = _q(total_perc - ded_reales)
    neto_final, ajuste_neto = _ajuste_neto_permitido(neto_prev)

    pdf_map = _mapear_pdf(
        isr_ord_antes=isr_ord_antes,
        isr_ord_neto=isr_ord_neto,
        isr_174=isr_174,
        isr_sep=isr_sep,
        sub_ap=sub_ap,
        tiene_sep=pa_gr > 0,
        tiene_sub=sub_ap > 0,
        total_percepciones=total_perc,
        neto_final=neto_final,
    )

    return {
        "laboral": {
            "dias_servicio": dias_servicio(ingreso, baja),
            "anios_servicio_exactos": float(anios_exact),
            "anios_servicio_completos": full_years_between(ingreso, baja),
            "ultimo_aniversario": ult_ann.isoformat(),
            "dias_vacaciones_anuales_ley": dias_vac_anuales,
            "dias_trabajados_anio": dias_trabajados_anio,
            "dias_aguinaldo_aplicables": float(dias_aguinaldo_politica),
            "factor_vacaciones_ciclo": float((Decimal(dias_transcurridos_ciclo) / Decimal(dias_anio_ciclo)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) if dias_anio_ciclo else D0),
            "factor_aguinaldo": float((Decimal(dias_trabajados_anio) / Decimal(dias_anio_cal)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
            "vacaciones_devengadas": float(vac_devengadas),
            "vacaciones_pendientes": float(vac_pend),
            "sueldo": float(sueldo),
            "septimo_dia": float(septimo),
            "vacaciones_a_tiempo": float(vacaciones_a_tiempo),
            "prima_vacacional": float(prima_vac_neta),
            "aguinaldo": float(aguinaldo),
            "prima_antiguedad_monto": float(prima_antig_monto),
        },
        "fiscal": {
            "ingreso_mensual_equiv": float(ingreso_mensual_equiv),
            "criterio_isr_ordinario": "semanal_mensualizada_tipo_contpaq",
            "base_ordinaria_mensualizada": float(base_ordinaria_mensualizada),
            "isr_ordinario_mensualizado": float(isr_ordinario_mensualizado),
            "subsidio_mensualizado_aplicado": float(sub_mensual_ap),
            "salario_minimo_zona": float(smg),
            "aguinaldo_exento": float(ag_ex),
            "aguinaldo_gravado": float(ag_gr),
            "prima_vac_exenta": float(pv_ex),
            "prima_vac_gravada": float(pv_gr),
            "prima_antig_exenta": float(pa_ex),
            "prima_antig_gravada": float(pa_gr),
            "bucket_ordinario_gravado": float(bucket_ord_grav),
            "bucket_art174_gravado": float(extra_art174),
            "bucket_separacion_gravado": float(pa_gr),
            "isr_ordinario_antes_subsidio": float(isr_ord_antes),
            "subsidio_aplicado": float(sub_ap),
            "isr_ordinario_neto": float(isr_ord_neto),
            "isr_art174": float(isr_174),
            "isr_separacion": float(isr_sep),
        },
        "auditoria": {
            "base_sueldo": {
                "sueldo_semanal_estimado": float(_q(salario_diario * Decimal("7"))),
                "salario_diario_calculado": float(salario_diario),
                "formula": "salario_diario = sueldo_semanal / 7",
            },
            "vacaciones": {
                "antiguedad_anios": float(anios_exact),
                "dias_vacaciones_corresponden": dias_vac_anuales,
                "dias_laborados_ciclo": dias_transcurridos_ciclo,
                "factor": float((Decimal(dias_transcurridos_ciclo) / Decimal(dias_anio_ciclo)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) if dias_anio_ciclo else D0),
                "formula": "vacaciones_devengadas = dias_vacaciones_anuales * dias_laborados_ciclo / dias_anio_ciclo",
                "resultado_dias": float(vac_pend),
                "monto": float(vacaciones_a_tiempo),
            },
            "aguinaldo": {
                "dias_aguinaldo": float(dias_aguinaldo_politica),
                "dias_laborados": dias_trabajados_anio,
                "factor": float((Decimal(dias_trabajados_anio) / Decimal(dias_anio_cal)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
                "formula": "aguinaldo = salario_diario * dias_aguinaldo * dias_laborados / dias_anio",
                "resultado": float(aguinaldo),
            },
            "prima_vacacional": {
                "base": float(vacaciones_a_tiempo),
                "porcentaje": float(prima_vacacional_pct),
                "formula": "prima_vacacional = vacaciones_a_tiempo * (pct/100)",
                "resultado": float(prima_vac_neta),
            },
            "isr": {
                "base_ordinaria_periodo": float(bucket_ord_grav),
                "base_ordinaria_mensualizada": float(base_ordinaria_mensualizada),
                "mensualizacion_formula": "base_mensualizada = base_periodo / 7 * 30.4",
                "isr_mensualizado": float(isr_ordinario_mensualizado),
                "subsidio_mensualizado": float(sub_mensual_ap),
                "isr_periodo_final": float(isr_ord_neto),
                "isr_art174": float(isr_174),
                "isr_separacion": float(isr_sep),
            },
            "ajuste_neto": {
                "neto_previo": float(neto_prev),
                "ajuste": float(ajuste_neto),
                "neto_final": float(neto_final),
            },
        },
        "totales": {
            "total_percepciones": float(total_perc),
            "total_deducciones_reales": float(ded_reales),
            "ajuste_neto": float(ajuste_neto),
            "neto_final": float(neto_final),
        },
        "pdf_filas": pdf_map,
    }


def _prima_antiguedad_procede(motivo: str, anios_exact: Decimal) -> bool:
    m = (motivo or "").strip().lower()
    if m in ("retiro_voluntario", "renuncia", "voluntario"):
        return anios_exact >= Decimal("15")
    return True


def _ajuste_neto_permitido(neto_prev: Decimal) -> tuple[Decimal, Decimal]:
    """Menor |ajuste| para que centavos finales ∈ {00,20,40,60,80}; empate → preferir .00."""
    neto_prev = _q(neto_prev)
    centavos_objetivo = {0, 20, 40, 60, 80}
    best_neto: Decimal | None = None
    best_adj: Decimal | None = None
    best_key: tuple | None = None

    base_cents = int(neto_prev * 100)
    for delta in range(-200, 201):
        cand = _q((Decimal(base_cents + delta)) / Decimal("100"))
        c = int((cand * 100) % 100)
        if c not in centavos_objetivo:
            continue
        ajuste = _q(neto_prev - cand)
        key = (abs(ajuste), 0 if c == 0 else 1, ajuste)
        if best_key is None or key < best_key:
            best_key = key
            best_neto = cand
            best_adj = ajuste
    if best_neto is None or best_adj is None:
        return neto_prev, D0
    return best_neto, best_adj


def _mapear_pdf(
    *,
    isr_ord_antes: Decimal,
    isr_ord_neto: Decimal,
    isr_174: Decimal,
    isr_sep: Decimal,
    sub_ap: Decimal,
    tiene_sep: bool,
    tiene_sub: bool,
    total_percepciones: Decimal,
    neto_final: Decimal,
) -> dict[str, Any]:
    """Filas fijas del DOCX según reglas 17.3."""
    sub_neg = _q(-sub_ap)
    n8, c8, t8 = "41", "I.S.R. antes de Subs al empleo", format_importe(isr_ord_antes)
    n9, c9, t9 = "43", "I.S.R. Art174", format_importe(isr_174)
    n10, c10, t10 = "45", "Subsidio al empleo aplicado", format_importe(sub_neg)

    if tiene_sep and not tiene_sub:
        n10, c10, t10 = "45", "ISR pagos por separación", format_importe(isr_sep)
    elif tiene_sep and tiene_sub:
        n8, c8, t8 = "41", "ISR ordinario neto", format_importe(isr_ord_neto)
        n9, c9, t9 = "43", "I.S.R. Art174", format_importe(isr_174)
        n10, c10, t10 = "45", "ISR pagos por separación", format_importe(isr_sep)

    suma_d_num = _q(total_percepciones - neto_final)

    return {
        "n8": n8,
        "c_isa": c8,
        "t8": t8,
        "n9": n9,
        "c_i174": c9,
        "t9": t9,
        "n10": n10,
        "c_imes": c10,
        "t10": t10,
        "suma_d": format_importe(suma_d_num),
    }


def format_importe(x: Decimal) -> str:
    """Formato #,##0.00 (coma miles, punto decimal)."""
    return f"{float(_q(x)):,.2f}"
