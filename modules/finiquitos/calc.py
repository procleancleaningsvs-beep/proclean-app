"""
Cálculo de finiquito (LFT / LISR 2026).

Política: prima de antigüedad con fracción de año proporcional exacta (importe laboral);
documentado como criterio por defecto en `prima_antiguedad_monto`.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any, Literal

from modules.finiquitos.config import SMG_FRONTERA_2026, SMG_GENERAL_2026
from modules.finiquitos.isr_partition import ParticionISR, particionar_bases_correcto_fiscal, particionar_bases_total_gravable
from modules.finiquitos.isr_tarifa_art96 import isr_art96_con_detalle, isr_art96_importe

D2 = Decimal("0.01")
D0 = Decimal("0")


def _q(x: Decimal) -> Decimal:
    return x.quantize(D2, rounding=ROUND_HALF_UP)


def calc_isr_art174_con_detalle(
    extra_art174: Decimal, ultimo_mensual: Decimal
) -> tuple[Decimal, dict[str, Any]]:
    """ISR Art. 174: tasa efectiva por diferencia de tablas; retorna ISR y trazabilidad."""
    if extra_art174 <= 0:
        return D0, {
            "base_art174": 0.0,
            "isr_art174": 0.0,
            "formula_ley": "Sin base gravable Art. 174: ISR = 0.",
            "remuneracion_mensualizada_174": 0.0,
            "tasa_efectiva": 0.0,
            "tarifa_sobre_ultimo_sueldo": None,
            "tarifa_sobre_sueldo_mas_remuneracion": None,
        }

    rem_m = _q(extra_art174 / Decimal("365") * Decimal("30.4"))
    d_iso = isr_art96_con_detalle(ultimo_mensual, "mensual")
    d_icon = isr_art96_con_detalle(ultimo_mensual + rem_m, "mensual")
    iso = d_iso.isr
    icon = d_icon.isr
    diff = max(D0, icon - iso)
    if rem_m == 0:
        tasa_174 = D0
    else:
        tasa_174 = (diff / rem_m).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    isr_174 = _q(extra_art174 * tasa_174)
    formula = (
        f"Remuneración mensualizada (Art. 174) = base Art. 174 × 30.4 / 365 = {rem_m}; "
        f"ISR sobre último sueldo (tabla mensual) = {iso}; "
        f"ISR sobre último sueldo + remuneración = {icon}; "
        f"diferencia = {diff}; tasa efectiva = {diff} / {rem_m} = {tasa_174}; "
        f"ISR Art. 174 = base {extra_art174} × tasa = {isr_174}."
    )
    return isr_174, {
        "base_art174": float(extra_art174),
        "isr_art174": float(isr_174),
        "formula_ley": formula,
        "remuneracion_mensualizada_174": float(rem_m),
        "tasa_efectiva": float(tasa_174),
        "tarifa_sobre_ultimo_sueldo": d_iso.to_auditoria(),
        "tarifa_sobre_sueldo_mas_remuneracion": d_icon.to_auditoria(),
    }


def calc_isr_mes_semanal_mensualizado(
    bucket_isr_mes_grav: Decimal,
    fecha_emision: date,
    ingreso_mensual_equiv: Decimal,
) -> dict[str, Any]:
    """
    ISR (mes): periodo 7 días, mensualización ÷7×30.4, tarifa mensual art. 96.
    En este módulo no se aplica subsidio al empleo; el ISR del periodo es el que alimenta 41 y 45 (mismo importe).
    """
    del fecha_emision, ingreso_mensual_equiv  # API estable; sin subsidio no intervienen.
    dias_periodo = Decimal("7")
    factor_mes = Decimal("30.4")
    base_periodo = _q(bucket_isr_mes_grav)
    base_mensualizada = _q(base_periodo / dias_periodo * factor_mes) if base_periodo > 0 else D0
    tarifa_m = isr_art96_con_detalle(base_mensualizada, "mensual")
    isr_men = tarifa_m.isr
    isr_periodo = _q(isr_men / factor_mes * dias_periodo)
    return {
        "base_isr_mes_periodo": base_periodo,
        "dias_periodo": dias_periodo,
        "tipo_periodo": "Semanal equivalente (7 días de pago); tarifa mensual art. 96 LISR (mensualización tipo nómina).",
        "factor_mensual_dias": factor_mes,
        "formula_mensualizacion": f"base mensualizada = (base periodo {base_periodo} ÷ {dias_periodo}) × {factor_mes} = {base_mensualizada}",
        "base_isr_mes_mensualizada": base_mensualizada,
        "tarifa_mensual_art96": tarifa_m.to_auditoria(),
        "isr_mensual_tabla_art96": isr_men,
        "isr_periodo_prorrateado": isr_periodo,
        "isr_mes_neto": isr_periodo,
    }


def _auditoria_particion(
    part: ParticionISR,
    total_percepciones_finiquito: Decimal,
    prima_dom: Decimal,
    ptu: Decimal,
) -> dict[str, Any]:
    """total_percepciones_finiquito incluye todas las líneas del recibo (p. ej. prima de antigüedad)."""
    return {
        "modo": part.modo,
        "uma_diaria": float(part.uma_diaria),
        "total_percepciones": float(total_percepciones_finiquito),
        "limite_exento_aguinaldo": float(part.limite_exento_aguinaldo),
        "limite_exento_prima_vacacional": float(part.limite_exento_prima_vacacional),
        "aguinaldo_monto": float(part.aguinaldo),
        "aguinaldo_exento_aplicado": float(part.aguinaldo_exento_aplicado),
        "excedente_aguinaldo": float(part.excedente_aguinaldo),
        "prima_vacacional_monto": float(part.prima_vacacional),
        "prima_vac_exenta_aplicada": float(part.prima_vac_exenta_aplicada),
        "excedente_prima_vacacional": float(part.excedente_prima_vacacional),
        "prima_dominical_total": float(prima_dom),
        "prima_dominical_exento": 0.0,
        "excedente_prima_dominical": float(part.excedente_prima_dominical),
        "ptu_total": float(ptu),
        "ptu_exento": 0.0,
        "excedente_ptu": float(part.excedente_ptu),
        "base_art174": float(part.base_art174),
        "base_isr_mes": float(part.base_isr_mes),
    }


def _mes_pkg_para_auditoria(m: dict[str, Any]) -> dict[str, Any]:
    """Serializa el paquete de ISR (mes) para JSON / UI."""
    isr_p = float(m["isr_mes_neto"])
    return {
        "titulo": "4) ISR del mes (base legal: art. 96 LISR / tarifa oficial vigente)",
        "base_isr_mes_periodo": float(m["base_isr_mes_periodo"]),
        "dias_periodo": float(m["dias_periodo"]),
        "tipo_periodo": m["tipo_periodo"],
        "factor_mensual_dias": float(m["factor_mensual_dias"]),
        "formula_mensualizacion": m["formula_mensualizacion"],
        "base_isr_mes_mensualizada": float(m["base_isr_mes_mensualizada"]),
        "tarifa_mensual_art96": m["tarifa_mensual_art96"],
        "isr_mensual_tabla_art96": float(m["isr_mensual_tabla_art96"]),
        "isr_periodo_prorrateado": float(m["isr_periodo_prorrateado"]),
        "isr_mes_neto": isr_p,
        "nota_41_45": (
            "41 I.S.R. antes de Subs al empleo y 45 I.S.R. (mes) muestran el mismo importe (ISR del periodo). "
            "Solo 45 suma en deducciones; 41 es referencia visual. No se calcula subsidio al empleo en este módulo."
        ),
    }


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
    """ISR art. 96; la trazabilidad de fila está en `isr_art96_con_detalle`."""
    return isr_art96_importe(base_gravada, periodicidad)


def anios_exentos_separacion(anios_exactos: Decimal) -> int:
    base = int(anios_exactos.to_integral_value(rounding=ROUND_DOWN))
    frac = anios_exactos - Decimal(base)
    if frac > Decimal("0.5"):
        base += 1
    return max(1, base)


def prima_antiguedad_aplica_separacion_voluntaria(ingreso: date, baja: date) -> bool:
    """Para este flujo: separación voluntaria solo aplica con 15 años o más."""
    return anios_servicio_exactos(ingreso, baja) >= Decimal("15")


def calcular_dias_vacaciones_devengados(ingreso: date, baja: date) -> dict[str, Any]:
    """
    Misma lógica que el finiquito: días de vacaciones devengados hasta la fecha de baja
    (ciclos completos + proporcional del ciclo en curso).
    """
    anios_completos = full_years_between(ingreso, baja)
    dias_vac_completos = D0
    for y in range(1, anios_completos + 1):
        dias_vac_completos += Decimal(dias_vacaciones_ley_por_anio_servicio(y))

    ult_ann = add_years_safe(ingreso, anios_completos) if anios_completos > 0 else ingreso
    aniversario_siguiente = add_years_safe(ult_ann, 1)
    dias_anio_ciclo = max(1, (aniversario_siguiente - ult_ann).days)
    dias_transcurridos_ciclo = max(0, (baja - ult_ann).days + 1)
    dias_vac_anuales_actual = Decimal(dias_vacaciones_ley_por_anio_servicio(anios_completos + 1))
    factor_vac = Decimal(dias_transcurridos_ciclo) / Decimal(dias_anio_ciclo)
    dias_vac_prop_actual = dias_vac_anuales_actual * factor_vac
    dias_vac_total_dev = dias_vac_completos + dias_vac_prop_actual
    return {
        "anios_completos": anios_completos,
        "dias_vac_completos": dias_vac_completos,
        "ult_ann": ult_ann,
        "aniversario_siguiente": aniversario_siguiente,
        "dias_anio_ciclo": dias_anio_ciclo,
        "dias_transcurridos_ciclo": dias_transcurridos_ciclo,
        "dias_vac_anuales_actual": dias_vac_anuales_actual,
        "factor_vac": factor_vac,
        "dias_vac_prop_actual": dias_vac_prop_actual,
        "dias_vac_total_dev": dias_vac_total_dev,
    }


def calcular_finiquito(
    *,
    ingreso: date,
    baja: date,
    fecha_emision: date,
    salario_diario: Decimal,
    zona: Literal["general", "frontera"],
    periodicidad_isr: Literal["quincenal", "mensual", "15_dias", "semanal_mensualizada"],
    modo: Literal["correcto_fiscal", "total_gravable"],
    dias_sueldo_pendientes: Decimal,
    septimos_pendientes: Decimal,
    dias_aguinaldo_politica: Decimal,
    prima_vacacional_pct: Decimal,
    vacaciones_ya_usadas: Decimal,
    aguinaldo_ya_pagado: Decimal,
    prima_vac_ya_pagada: Decimal,
    aguinaldo_pagado_previamente: bool = False,
    prima_dias_cubiertos: Decimal = D0,
    incluir_prima_antiguedad: bool,
    motivo_baja: str,
    salario_mensual_capturado: Decimal | None = None,
    prima_dominical_monto: Decimal = D0,
    ptu_monto: Decimal = D0,
    año_calendario_actual: int | None = None,
) -> dict[str, Any]:
    smg = SMG_GENERAL_2026 if zona == "general" else SMG_FRONTERA_2026
    año_ref = año_calendario_actual if año_calendario_actual is not None else date.today().year

    vac_ctx = calcular_dias_vacaciones_devengados(ingreso, baja)
    anios_completos = vac_ctx["anios_completos"]
    dias_vac_completos = vac_ctx["dias_vac_completos"]
    ult_ann = vac_ctx["ult_ann"]
    aniversario_siguiente = vac_ctx["aniversario_siguiente"]
    dias_anio_ciclo = vac_ctx["dias_anio_ciclo"]
    dias_transcurridos_ciclo = vac_ctx["dias_transcurridos_ciclo"]
    dias_vac_anuales_actual = vac_ctx["dias_vac_anuales_actual"]
    factor_vac = vac_ctx["factor_vac"]
    dias_vac_prop_actual = vac_ctx["dias_vac_prop_actual"]
    dias_vac_total_dev = vac_ctx["dias_vac_total_dev"]

    vac_ya_in = vacaciones_ya_usadas if vacaciones_ya_usadas > D0 else D0
    tope_vac_ya_activo = ingreso.year == año_ref
    vac_ya_eff = vac_ya_in
    vac_ya_recortado = False
    if tope_vac_ya_activo and vac_ya_eff > dias_vac_total_dev:
        vac_ya_eff = dias_vac_total_dev
        vac_ya_recortado = True

    vac_pend = dias_vac_total_dev - vac_ya_eff
    if vac_pend < 0:
        vac_pend = D0
    vacaciones_a_tiempo = _q(salario_diario * vac_pend)

    # Prima vacacional desacoplada: usa días cuya prima aún no fue cubierta.
    prima_base_dias = dias_vac_total_dev - prima_dias_cubiertos
    if prima_base_dias < 0:
        prima_base_dias = D0
    prima_base_monto = _q(salario_diario * prima_base_dias)
    prima_vacacional = _q(prima_base_monto * (prima_vacacional_pct / Decimal("100")))

    # Aguinaldo acumulado: según pago previo (año actual) o desde ingreso.
    inicio_anio = date(baja.year, 1, 1)
    dias_trabajados_anio = (baja - inicio_anio).days + 1
    dias_anio_cal = 366 if baja.year % 4 == 0 and (baja.year % 100 != 0 or baja.year % 400 == 0) else 365
    ag_prop_actual = dias_aguinaldo_politica * Decimal(dias_trabajados_anio) / Decimal(dias_anio_cal)
    ag_completos_no_pag = D0
    ag_prop_historico = D0
    if aguinaldo_pagado_previamente:
        ag_total_dias = ag_prop_actual
    else:
        for y in range(ingreso.year, baja.year):
            y_ini = date(y, 1, 1)
            y_fin = date(y, 12, 31)
            ini = ingreso if ingreso > y_ini else y_ini
            fin = y_fin
            if fin < ini:
                continue
            worked = (fin - ini).days + 1
            y_days = 366 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 365
            if worked >= y_days:
                ag_completos_no_pag += dias_aguinaldo_politica
            else:
                ag_prop_historico += dias_aguinaldo_politica * Decimal(worked) / Decimal(y_days)
        ag_total_dias = ag_completos_no_pag + ag_prop_historico + ag_prop_actual

    aguinaldo_bruto = _q(salario_diario * ag_total_dias)
    aguinaldo = aguinaldo_bruto - aguinaldo_ya_pagado
    if aguinaldo < 0:
        aguinaldo = D0
    aguinaldo = _q(aguinaldo)

    prima_vac_neta = prima_vacacional - prima_vac_ya_pagada
    if prima_vac_neta < 0:
        prima_vac_neta = D0
    prima_vac_neta = _q(prima_vac_neta)

    prima_dom = _q(max(D0, prima_dominical_monto))
    ptu = _q(max(D0, ptu_monto))

    sueldo = _q(salario_diario * dias_sueldo_pendientes)
    septimo = _q(salario_diario * septimos_pendientes)

    anios_exact = anios_servicio_exactos(ingreso, baja)
    salario_tope = salario_diario if salario_diario < 2 * smg else 2 * smg
    prima_antig_monto = D0
    if incluir_prima_antiguedad and _prima_antiguedad_procede(motivo_baja, anios_exact):
        prima_antig_monto = _q(salario_tope * Decimal("12") * anios_exact)

    total_perc = _q(
        sueldo + septimo + vacaciones_a_tiempo + prima_vac_neta + aguinaldo + prima_antig_monto + prima_dom + ptu
    )

    if modo == "total_gravable":
        part = particionar_bases_total_gravable(
            total_percepciones=total_perc,
            aguinaldo=aguinaldo,
            prima_vacacional=prima_vac_neta,
            prima_dominical=prima_dom,
            ptu=ptu,
            fecha_referencia=fecha_emision,
        )
        bucket_isr_mes_grav = part.base_isr_mes
        extra_art174 = part.base_art174
        ag_ex = part.aguinaldo_exento_aplicado
        ag_gr = part.excedente_aguinaldo
        pv_ex = part.prima_vac_exenta_aplicada
        pv_gr = part.excedente_prima_vacacional
        pa_ex = D0
        pa_gr = _q(prima_antig_monto)
    else:
        part = particionar_bases_correcto_fiscal(
            sueldo=sueldo,
            septimo=septimo,
            vacaciones=vacaciones_a_tiempo,
            aguinaldo=aguinaldo,
            prima_vacacional=prima_vac_neta,
            prima_dominical=prima_dom,
            ptu=ptu,
            fecha_referencia=fecha_emision,
        )
        bucket_isr_mes_grav = part.base_isr_mes
        extra_art174 = part.base_art174
        ag_ex = part.aguinaldo_exento_aplicado
        ag_gr = part.excedente_aguinaldo
        pv_ex = part.prima_vac_exenta_aplicada
        pv_gr = part.excedente_prima_vacacional
        anios_ex = anios_exentos_separacion(anios_exact)
        lim_sep = 90 * smg * Decimal(anios_ex)
        pa_ex = _q(min(prima_antig_monto, lim_sep))
        pa_gr = _q(max(D0, prima_antig_monto - pa_ex))

    ingreso_mensual_equiv = salario_mensual_capturado if salario_mensual_capturado is not None else _q(salario_diario * Decimal("30.4"))
    ultimo_mensual = ingreso_mensual_equiv

    mes_pkg = calc_isr_mes_semanal_mensualizado(bucket_isr_mes_grav, fecha_emision, ingreso_mensual_equiv)
    base_isr_mes_mensualizada = mes_pkg["base_isr_mes_mensualizada"]
    isr_tabla_mensual = mes_pkg["isr_mensual_tabla_art96"]
    isr_mes_neto = mes_pkg["isr_mes_neto"]
    isr_antes_subsidio = isr_mes_neto
    sub_ap = D0
    sub_mensual_ap = D0

    isr_174, art174_audit = calc_isr_art174_con_detalle(extra_art174, ultimo_mensual)
    particion_aud = _auditoria_particion(part, total_perc, prima_dom, ptu)
    mes_aud = _mes_pkg_para_auditoria(mes_pkg)

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

    ded_reales = _q(isr_mes_neto + isr_174 + isr_sep)
    neto_prev = _q(total_perc - ded_reales)
    neto_final, ajuste_neto = _ajuste_neto_permitido(neto_prev)
    # ajuste_neto = neto_prev - neto_final: >0 baja el neto → línea 99 en deducciones; <0 sube → en percepciones.
    extra_99_deduccion = _q(abs(ajuste_neto)) if ajuste_neto > 0 else D0
    suma_43_45_99 = _q(isr_mes_neto + isr_174 + extra_99_deduccion)

    pdf_map = _mapear_pdf(
        isr_antes_subsidio=isr_antes_subsidio,
        isr_mes_neto=isr_mes_neto,
        isr_174=isr_174,
        isr_sep=isr_sep,
        ajuste_neto=ajuste_neto,
    )

    return {
        "laboral": {
            "dias_servicio": dias_servicio(ingreso, baja),
            "anios_servicio_exactos": float(anios_exact),
            "anios_servicio_completos": full_years_between(ingreso, baja),
            "ultimo_aniversario": ult_ann.isoformat(),
            "dias_vacaciones_anuales_ley": float(dias_vac_anuales_actual),
            "dias_laborados_ciclo_vacaciones": dias_transcurridos_ciclo,
            "dias_anio_ciclo_vacaciones": dias_anio_ciclo,
            "dias_trabajados_anio": dias_trabajados_anio,
            "dias_anio_aguinaldo": dias_anio_cal,
            "dias_aguinaldo_aplicables": float(dias_aguinaldo_politica),
            "factor_vacaciones_ciclo": float(factor_vac.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
            "factor_aguinaldo": float((Decimal(dias_trabajados_anio) / Decimal(dias_anio_cal)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
            "vacaciones_devengadas": float(dias_vac_total_dev),
            "vacaciones_ya_usadas_capturadas": float(vac_ya_in),
            "vacaciones_ya_usadas_efectivas": float(vac_ya_eff),
            "vacaciones_ya_usadas_tope_por_ingreso_año_actual": tope_vac_ya_activo,
            "vacaciones_ya_usadas_max_permitido": float(dias_vac_total_dev) if tope_vac_ya_activo else None,
            "vacaciones_ya_usadas_se_recorto": vac_ya_recortado,
            "vacaciones_completas_no_pagadas_dias": float(dias_vac_completos),
            "vacaciones_proporcionales_dias": float(dias_vac_prop_actual),
            "vacaciones_pendientes": float(vac_pend),
            "aguinaldo_completos_no_pagados_dias": float(ag_completos_no_pag),
            "aguinaldo_historico_proporcional_no_pagado_dias": float(ag_prop_historico),
            "aguinaldo_proporcional_dias": float(ag_prop_actual),
            "aguinaldo_total_dias": float(ag_total_dias),
            "prima_vac_base_dias": float(prima_base_dias),
            "sueldo": float(sueldo),
            "septimo_dia": float(septimo),
            "vacaciones_a_tiempo": float(vacaciones_a_tiempo),
            "prima_vacacional": float(prima_vac_neta),
            "aguinaldo": float(aguinaldo),
            "prima_antiguedad_monto": float(prima_antig_monto),
            "prima_dominical": float(prima_dom),
            "ptu": float(ptu),
        },
        "fiscal": {
            "ingreso_mensual_equiv": float(ingreso_mensual_equiv),
            "criterio_isr_ordinario": "semanal_mensualizada_tipo_contpaq",
            "modo_total_gravable_activo": modo == "total_gravable",
            "base_isr_mes_mensualizada": float(base_isr_mes_mensualizada),
            "isr_antes_subsidio_mensual": float(isr_tabla_mensual),
            "subsidio_mensualizado_aplicado": 0.0,
            "salario_minimo_zona": float(smg),
            "aguinaldo_exento": float(ag_ex),
            "aguinaldo_gravado": float(ag_gr),
            "prima_vac_exenta": float(pv_ex),
            "prima_vac_gravada": float(pv_gr),
            "prima_antig_exenta": float(pa_ex),
            "prima_antig_gravada": float(pa_gr),
            "bucket_isr_mes_gravado": float(bucket_isr_mes_grav),
            "bucket_ordinario_gravado": float(bucket_isr_mes_grav),
            "bucket_art174_gravado": float(extra_art174),
            "bucket_separacion_gravado": float(pa_gr),
            "base_ordinaria_mensualizada": float(base_isr_mes_mensualizada),
            "isr_antes_subsidio_periodo": float(isr_mes_neto),
            "isr_ordinario_antes_subsidio": float(isr_mes_neto),
            "subsidio_aplicado": 0.0,
            "isr_mes_neto": float(isr_mes_neto),
            "isr_ordinario_neto": float(isr_mes_neto),
            "isr_art174": float(isr_174),
            "isr_separacion": float(isr_sep),
        },
        "auditoria": {
            "particion_isr": particion_aud,
            "percepciones_finiquito": [
                {"concepto": "Sueldo", "monto": float(sueldo)},
                {"concepto": "Séptimo día", "monto": float(septimo)},
                {"concepto": "Vacaciones", "monto": float(vacaciones_a_tiempo)},
                {"concepto": "Prima vacacional", "monto": float(prima_vac_neta)},
                {"concepto": "Aguinaldo", "monto": float(aguinaldo)},
                {"concepto": "Prima de antigüedad", "monto": float(prima_antig_monto)},
                {"concepto": "Prima dominical", "monto": float(prima_dom)},
                {"concepto": "PTU", "monto": float(ptu)},
            ],
            "seccion_fiscal": {
                "A_resumen_percepciones": {
                    "titulo": "A) Resumen de percepciones",
                    "sueldo": float(sueldo),
                    "septimo_dia": float(septimo),
                    "vacaciones": float(vacaciones_a_tiempo),
                    "aguinaldo": float(aguinaldo),
                    "prima_vacacional": float(prima_vac_neta),
                    "prima_dominical": float(prima_dom),
                    "ptu": float(ptu),
                    "prima_antiguedad": float(prima_antig_monto),
                    "total_percepciones": float(total_perc),
                },
                "B_determinacion_art174": {
                    "titulo": "B) Determinación de base Art. 174",
                    **particion_aud,
                },
                "C_base_isr_mes": {
                    "titulo": "C) Determinación de base ISR (mes)",
                    "total_percepciones": float(total_perc),
                    "base_art174_resta": float(extra_art174),
                    "base_isr_mes": float(bucket_isr_mes_grav),
                    "usa_total_menos_art174": modo == "total_gravable",
                },
                "D_isr_mes_art96": mes_aud,
                "E_isr_art174": {"titulo": "5) ISR Art. 174 (cálculo separado)", **art174_audit},
                "F_ajuste_neto": {
                    "titulo": "F) Ajuste al neto",
                    "neto_previo": float(neto_prev),
                    "ajuste": float(ajuste_neto),
                    "neto_final": float(neto_final),
                },
            },
            "isr_mes_detalle": {
                "total_percepciones": float(total_perc),
                "base_art174": float(extra_art174),
                "base_isr_mes": float(bucket_isr_mes_grav),
                "usa_total_menos_art174": modo == "total_gravable",
                "base_isr_mes_mensualizada": float(base_isr_mes_mensualizada),
                "isr_mensual_tabla_art96": float(isr_tabla_mensual),
                "isr_periodo_41_y_45": float(isr_mes_neto),
                "isr_mes_final": float(isr_mes_neto),
                "tarifa_mensual_art96": mes_pkg["tarifa_mensual_art96"],
                "nota_41_45": mes_aud.get("nota_41_45", ""),
            },
            "isr_art174_detalle": {
                **art174_audit,
                "titulo": "5) ISR Art. 174 (cálculo separado)",
            },
            "base_sueldo": {
                "sueldo_semanal_estimado": float(_q(salario_diario * Decimal("7"))),
                "salario_diario_calculado": float(salario_diario),
                "formula": "salario_diario = sueldo_semanal / 7",
            },
            "vacaciones": {
                "antiguedad_anios": float(anios_exact),
                "dias_vacaciones_corresponden": float(dias_vac_anuales_actual),
                "dias_vacaciones_completos_no_pagados": float(dias_vac_completos),
                "dias_laborados_ciclo": dias_transcurridos_ciclo,
                "dias_anio_ciclo": dias_anio_ciclo,
                "factor": float(factor_vac.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
                "formula": "vacaciones_devengadas = dias_vacaciones_anuales * dias_laborados_ciclo / dias_anio_ciclo",
                "resultado_dias_proporcionales": float(dias_vac_prop_actual),
                "resultado_dias_totales_acumulados": float(dias_vac_total_dev),
                "resultado_dias_pendientes": float(vac_pend),
                "monto": float(vacaciones_a_tiempo),
                "tope_vacaciones_ya_usadas_activo": tope_vac_ya_activo,
                "tope_max_vacaciones_ya_usadas_dias": float(dias_vac_total_dev) if tope_vac_ya_activo else None,
            },
            "aguinaldo": {
                "dias_aguinaldo": float(dias_aguinaldo_politica),
                "dias_completos_no_pagados": float(ag_completos_no_pag),
                "dias_historico_proporcional_no_pagado": float(ag_prop_historico),
                "dias_laborados": dias_trabajados_anio,
                "dias_anio": dias_anio_cal,
                "factor": float((Decimal(dias_trabajados_anio) / Decimal(dias_anio_cal)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
                "formula": "aguinaldo = salario_diario * dias_aguinaldo * dias_laborados / dias_anio",
                "resultado_dias_proporcionales": float(ag_prop_actual),
                "resultado_dias_totales": float(ag_total_dias),
                "resultado": float(aguinaldo),
            },
            "prima_vacacional": {
                "base_dias": float(prima_base_dias),
                "base": float(prima_base_monto),
                "porcentaje": float(prima_vacacional_pct),
                "formula": "prima_vacacional = base_prima_vacacional * (pct/100)",
                "resultado": float(prima_vac_neta),
            },
            "isr": {
                "base_isr_mes_periodo": float(bucket_isr_mes_grav),
                "base_ordinaria_periodo": float(bucket_isr_mes_grav),
                "base_isr_mes_mensualizada": float(base_isr_mes_mensualizada),
                "base_ordinaria_mensualizada": float(base_isr_mes_mensualizada),
                "mensualizacion_formula": "base_mensualizada = base_isr_mes_periodo / 7 * 30.4",
                "isr_mensual_tabla_art96": float(isr_tabla_mensual),
                "isr_mensualizado": float(isr_tabla_mensual),
                "subsidio_mensualizado": 0.0,
                "isr_periodo_41_y_45": float(isr_mes_neto),
                "isr_periodo_final": float(isr_mes_neto),
                "isr_mes_neto_periodo": float(isr_mes_neto),
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
            "suma_deducciones_43_45_99": float(suma_43_45_99),
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


def _neto_centavos_en_malla_20(neto: Decimal) -> bool:
    """Centavos del neto (2 decimales) en {00,20,40,60,80} → no hace falta ajuste al neto."""
    q = _q(neto)
    cent = int((q * 100) % 100)
    return cent in (0, 20, 40, 60, 80)


def _ajuste_neto_permitido(neto_prev: Decimal) -> tuple[Decimal, Decimal]:
    """Menor |ajuste| para que centavos finales ∈ {00,20,40,60,80}; empate → preferir .00."""
    neto_prev = _q(neto_prev)
    if _neto_centavos_en_malla_20(neto_prev):
        return neto_prev, D0
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
    isr_antes_subsidio: Decimal,
    isr_mes_neto: Decimal,
    isr_174: Decimal,
    isr_sep: Decimal,
    ajuste_neto: Decimal,
) -> dict[str, Any]:
    """
    Numeración fija: 41 (referencia, mismo importe que 45; no suma), 43 Art.174 (suma), 45 ISR (mes) (suma).
    No se aplica subsidio al empleo: 41 y 45 reflejan el ISR del periodo.
    99 Ajuste al neto se arma en export_docx (suma solo si aplica como deducción).
    suma_d = 45 + 43 + (99 si ajuste > 0) + ISR separación si aplica (para cuadrar neto).
    """
    lbl_41 = "I.S.R. antes de Subs al empleo"
    lbl_43 = "I.S.R. Art174"
    lbl_45 = "I.S.R. (mes)"

    if isr_antes_subsidio > 0:
        n8, c8, t8 = "41", lbl_41, format_importe(isr_antes_subsidio)
    else:
        n8, c8, t8 = "", "", ""

    if isr_174 > 0:
        n9, c9, t9 = "43", lbl_43, format_importe(isr_174)
    else:
        n9, c9, t9 = "", "", ""

    if isr_mes_neto > 0:
        n10, c10, t10 = "45", lbl_45, format_importe(isr_mes_neto)
    else:
        n10, c10, t10 = "", "", ""

    extra_ajuste_ded = _q(abs(ajuste_neto)) if ajuste_neto > 0 else D0
    suma_43_45_99 = _q(isr_mes_neto + isr_174 + extra_ajuste_ded)
    suma_d_num = _q(suma_43_45_99 + isr_sep)

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
        "n_sep": "",
        "c_sep": "",
        "t_sep": "",
        "suma_d": format_importe(suma_d_num),
        "suma_d_43_45_99": format_importe(suma_43_45_99),
    }


def format_importe(x: Decimal) -> str:
    """Formato #,##0.00 (coma miles, punto decimal)."""
    return f"{float(_q(x)):,.2f}"
