"""Motor puro de nómina preliminar (Microfase 4.1). Usa Decimal y ROUND_HALF_UP."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any, Iterable

from modules.nomina.config import (
    BONO_TPT_TOPE_2026,
    DIAS_TARIFA_ISR_DEFAULT,
    DIAS_TARIFA_SUBSIDIO_DEFAULT,
    DOMINGO_FACTOR_MANUAL,
    DOMINGO_FACTOR_PRIMA,
    DOMINGO_FACTOR_PROPORCIONAL,
    FACTOR_ISR_MENSUAL,
    ISR_MENSUAL_2026_BRACKETS,
    NETO_INTEGER_THRESHOLD,
    NETO_ROUND_STEP,
    PAID_DAILY_KEYS,
    SUBSIDIO_BASE_MENSUAL_MAX_EXCL,
    SUBSIDIO_BASE_MENSUAL_MIN_INCL,
    SUBSIDIO_MACRO_MENSUAL_2026,
    WARN_BLOCK_CALC_MISSING_SALARY,
    WARN_BLOCK_CALC_MISSING_VALOR_HE,
    salario_minimo_semanal_2026,
)
from modules.nomina.validators import _norm_header

Q2 = Decimal("0.01")


def q2(value: Decimal) -> Decimal:
    return value.quantize(Q2, rounding=ROUND_HALF_UP)


def _to_decimal(raw: Any) -> Decimal:
    if raw is None:
        return Decimal(0)
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, (int, float)):
        return Decimal(str(raw))
    s = str(raw).strip().replace(",", "")
    if not s:
        return Decimal(0)
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal(0)


def _parse_date_any(raw: Any) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    s = str(raw).strip()[:10]
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def bimestre_index(d: date) -> int:
    return (d.month - 1) // 2


def dias_bimestre_calendario(year: int, bim_idx: int) -> int:
    m1 = bim_idx * 2 + 1
    m2 = m1 + 1
    return calendar.monthrange(year, m1)[1] + calendar.monthrange(year, m2)[1]


def infonavit_bimestre_vigente(fecha_inicio: date, fecha_fin: date) -> tuple[int, int]:
    """Si la semana cruza bimestre, usar el bimestre del fin de periodo (siguiente)."""
    bi_s = bimestre_index(fecha_inicio)
    bi_e = bimestre_index(fecha_fin)
    if fecha_inicio.year != fecha_fin.year or bi_s != bi_e:
        return fecha_fin.year, bi_e
    return fecha_inicio.year, bi_s


def calcular_infonavit_semanal(
    deduccion_mensual: Decimal,
    fecha_inicio: date,
    fecha_fin: date,
) -> tuple[Decimal, dict[str, Any]]:
    year, bidx = infonavit_bimestre_vigente(fecha_inicio, fecha_fin)
    dias_b = dias_bimestre_calendario(year, bidx)
    if dias_b <= 0:
        return Decimal(0), {"error": "dias_bimestre_invalido"}
    semanal = ((deduccion_mensual * Decimal(2)) / Decimal(dias_b)) * Decimal(7)
    return q2(semanal), {
        "dias_bimestre_vigente": dias_b,
        "bimestre_index": bidx,
        "anio": year,
    }


def calcular_dias_y_septimo(daily_values: Iterable[str]) -> dict[str, Any]:
    warnings: list[str] = []
    codes: list[str] = []
    fl = dl = 0
    for raw in daily_values:
        code = _norm_header(raw)
        codes.append(code)
        if code == "NI":
            warnings.append("nuevo_ingreso_ni_no_computa")
            continue
        if code in PAID_DAILY_KEYS:
            if code == "FL":
                fl += 1
            if code == "DL":
                dl += 1
            if code == "R":
                warnings.append("retardo_r_computa_posible_deduccion_manual")
        # OT and empty do not add to paid unless in PAID - OT not in PAID

    computable = sum(1 for c in codes if c in PAID_DAILY_KEYS)
    dc = Decimal(computable)
    sept = (dc / Decimal(6)).quantize(Q2, rounding=ROUND_HALF_UP)
    dias_pago = q2(dc + sept)
    return {
        "dias_computables": float(dc),
        "septimo_dia": float(sept),
        "dias_pago": float(dias_pago),
        "festivo_laborado_detected": fl,
        "domingo_laborado_detected": dl,
        "daily_codes": codes,
        "warnings": warnings,
    }


def calcular_valor_he_fiscal(horas_extra: Decimal, smg: Decimal) -> Decimal:
    if horas_extra <= 0 or smg <= 0:
        return Decimal(0)
    valor_hora = smg / Decimal(8)
    horas_dobles = min(horas_extra, Decimal(9))
    horas_triples = max(Decimal(0), horas_extra - Decimal(9))
    fiscal = horas_dobles * valor_hora * Decimal(2) + horas_triples * valor_hora * Decimal(3)
    return q2(fiscal)


def calcular_base_gravada(
    sueldo_base_smg: Decimal,
    concepto_gravable: Decimal,
    valor_he_fiscal: Decimal,
    exento_he: Decimal,
) -> Decimal:
    excedente = max(Decimal(0), valor_he_fiscal - exento_he)
    return q2(sueldo_base_smg + concepto_gravable + excedente)


def _isr_mensual_tabla_2026(base_mensual: Decimal) -> Decimal:
    if base_mensual <= 0:
        return Decimal(0)
    for br in ISR_MENSUAL_2026_BRACKETS:
        sup = br.limite_superior
        if base_mensual >= br.limite_inferior and (sup is None or base_mensual <= sup):
            return br.cuota_fija + (base_mensual - br.limite_inferior) * (br.tasa_pct / Decimal(100))
    last = ISR_MENSUAL_2026_BRACKETS[-1]
    return last.cuota_fija + (base_mensual - last.limite_inferior) * (last.tasa_pct / Decimal(100))


def redondear_neto_operativo(
    valor: Decimal,
    *,
    step: Decimal = NETO_ROUND_STEP,
    threshold: Decimal = NETO_INTEGER_THRESHOLD,
) -> Decimal:
    """Neto con terminación .00, .20, … según regla operativa (macro / Excel)."""
    if valor <= 0:
        return Decimal(0)
    int_part = valor.to_integral_value(rounding=ROUND_FLOOR)
    frac = valor - int_part
    if frac >= threshold:
        return q2(int_part + Decimal(1))
    n = (valor / step).to_integral_value(rounding=ROUND_CEILING)
    return q2(n * step)


def calcular_isr_2026(
    base_gravada: Decimal,
    *,
    total_percepciones_periodo: Decimal,
    dias_tarifa_isr: Decimal,
    dias_tarifa_subs: Decimal,
    es_fin_de_mes: bool,
    es_frontera: bool,
    permitir_negativo: bool = False,
) -> Decimal:
    """Réplica macro ISR_2026: baseISR_Mes y baseSub_Mes con FACTOR_MES=30.4; subsidio 536.21 o 0."""
    if es_fin_de_mes:
        # Reservado: con EsFinDeMes=False no se aplica tope fin de mes en esta fase.
        pass
    if base_gravada <= 0:
        return Decimal(0)
    smg_sem = salario_minimo_semanal_2026(es_frontera=es_frontera)
    if total_percepciones_periodo <= smg_sem:
        return Decimal(0)
    d_isr = dias_tarifa_isr if dias_tarifa_isr > 0 else Decimal(str(DIAS_TARIFA_ISR_DEFAULT))
    d_sub = dias_tarifa_subs if dias_tarifa_subs > 0 else Decimal(str(DIAS_TARIFA_SUBSIDIO_DEFAULT))
    base_isr_mes = (base_gravada / d_isr) * FACTOR_ISR_MENSUAL
    base_sub_mes = (base_gravada / d_sub) * FACTOR_ISR_MENSUAL
    isr_mes = q2(_isr_mensual_tabla_2026(base_isr_mes))
    if SUBSIDIO_BASE_MENSUAL_MIN_INCL <= base_sub_mes < SUBSIDIO_BASE_MENSUAL_MAX_EXCL:
        subsidio_mes = SUBSIDIO_MACRO_MENSUAL_2026
    else:
        subsidio_mes = Decimal(0)
    isr_periodo = (isr_mes / FACTOR_ISR_MENSUAL) * d_isr
    subsidio_periodo = (subsidio_mes / FACTOR_ISR_MENSUAL) * d_sub
    isr = q2(isr_periodo - subsidio_periodo)
    if not permitir_negativo and isr < 0:
        isr = Decimal(0)
    return q2(isr)


def calcular_bono_tpt_y_prima_eficiencia(
    *,
    salario_operativo: Decimal,
    smg: Decimal,
    dias_pago: Decimal,
    valor_x_he: Decimal,
    horas_extra: Decimal,
    valor_he_fiscal: Decimal,
    isr: Decimal,
    tope: Decimal = BONO_TPT_TOPE_2026,
) -> tuple[Decimal, Decimal]:
    if salario_operativo <= 0 or smg <= 0:
        return Decimal(0), Decimal(0)
    sueldo_prop = (salario_operativo / Decimal(7)) * dias_pago
    parte_smg = smg * dias_pago
    comp_he = (valor_x_he * horas_extra) - valor_he_fiscal
    diferencia = sueldo_prop - parte_smg + comp_he + isr
    if diferencia < 0:
        diferencia = Decimal(0)
    bono_tpt = min(tope, diferencia)
    bono_tpt = max(Decimal(0), bono_tpt)
    prima = max(Decimal(0), diferencia - bono_tpt)
    return q2(bono_tpt), q2(prima)


def domingo_factor_from_opcion(opcion: str) -> Decimal:
    o = (opcion or "").strip().lower()
    if o in {"prima", "1.25", "125"}:
        return DOMINGO_FACTOR_PRIMA
    if o in {"no", "manual", "0", "none"}:
        return DOMINGO_FACTOR_MANUAL
    return DOMINGO_FACTOR_PROPORCIONAL


def recalcular_totales_percepciones_deducciones(
    *,
    sueldo_base_smg: Decimal,
    valor_he_fiscal: Decimal,
    concepto_gravable: Decimal,
    concepto_exento: Decimal,
    bono_tpt: Decimal,
    prima_eficiencia: Decimal,
    isr: Decimal,
    infonavit_semanal: Decimal,
    deduccion_manual: Decimal,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    total_perc = q2(
        sueldo_base_smg + valor_he_fiscal + concepto_gravable + concepto_exento + bono_tpt + prima_eficiencia
    )
    total_ded = q2(isr + infonavit_semanal + deduccion_manual)
    neto_simple_fiscal = q2(total_perc - isr)
    neto_pagar = q2(total_perc - total_ded)
    return total_perc, total_ded, neto_simple_fiscal, neto_pagar


def enriquecer_netos_y_auditoria(
    row: dict[str, Any],
    *,
    salario_op: Decimal | None,
    dias_pago: Decimal,
    he: Decimal,
    valor_x_he_para_extra: Decimal | None,
    concepto_gravable: Decimal,
    concepto_exento: Decimal,
    total_perc: Decimal,
    total_ded: Decimal,
    isr: Decimal,
    inf_semanal: Decimal,
    detail: dict[str, Any],
) -> None:
    """NETO SIMPLE operativo, neto fiscal en detail, redondeo .00/.20 y neto final."""
    neto_simple_fiscal = q2(total_perc - isr)
    neto_sin_ajuste = total_perc - total_ded
    neto_redondeado = redondear_neto_operativo(neto_sin_ajuste)
    ajuste_al_neto = q2(neto_redondeado - neto_sin_ajuste)
    vx = valor_x_he_para_extra if valor_x_he_para_extra is not None else Decimal(0)
    valor_extra_real = vx * he
    if salario_op is None or salario_op <= 0:
        base_neto_simple = Decimal(0)
        neto_simple_operativo = Decimal(0)
    else:
        base_neto_simple = (
            (salario_op / Decimal(7)) * dias_pago + valor_extra_real + concepto_gravable + concepto_exento - inf_semanal
        )
        neto_simple_operativo = redondear_neto_operativo(base_neto_simple)
    d = dict(detail)
    d["neto_sin_ajuste"] = str(neto_sin_ajuste)
    d["ajuste_al_neto"] = str(ajuste_al_neto)
    d["neto_redondeado"] = str(neto_redondeado)
    d["valor_extra_real"] = str(valor_extra_real)
    d["neto_simple_fiscal"] = str(neto_simple_fiscal)
    d["base_neto_simple"] = str(base_neto_simple)
    d["neto_simple_operativo"] = str(neto_simple_operativo)
    row["base_neto_simple"] = float(base_neto_simple)
    row["neto_simple_operativo"] = float(neto_simple_operativo)
    row["neto_redondeado"] = float(neto_redondeado)
    row["ajuste_al_neto"] = float(ajuste_al_neto)
    row["neto_a_pagar_final"] = float(neto_redondeado)
    row["neto_simple"] = float(neto_simple_fiscal)
    row["neto_a_pagar"] = float(neto_redondeado)
    row["detail_json"] = d


@dataclass
class EmpleadoCalcInput:
    asistencia_row_id: int
    nombre_empleado: str
    cliente: str
    planta: str
    puesto: str
    banco: str
    cuenta: str
    nss: str
    daily_values: list[str]
    he_raw: Any
    horas_extra_normales_raw: Any
    dias_cubiertos_normales_raw: Any
    vacaciones_laboradas_raw: Any
    prima_vacacional_raw: str
    bono_raw: Any
    deducciones_raw: Any
    headcount_match_status: str | None
    # parámetros
    parametro_empleado_id: int | None
    numero_empleado: str | None
    salario_operativo: Decimal | None
    valor_x_he: Decimal | None
    es_frontera: bool
    smg_usado: Decimal
    exento_he_usado: Decimal
    # vacaciones / infonavit ids
    vacaciones_empleado_id: int | None
    vacaciones_row: dict[str, Any] | None
    infonavit_row_id: int | None
    infonavit_row: dict[str, Any] | None


@dataclass
class CalcularNominaConfig:
    anio_smg: int = 2026
    dias_tarifa_isr: Decimal = Decimal(str(DIAS_TARIFA_ISR_DEFAULT))
    dias_tarifa_subs: Decimal = Decimal(str(DIAS_TARIFA_SUBSIDIO_DEFAULT))
    domingo_opcion: str = "proporcional"  # proporcional | prima | no
    es_fin_de_mes: bool = False
    permitir_negativo_isr: bool = False
    fecha_inicio: date | None = None
    fecha_fin: date | None = None


def _numeric_or_warn(raw: Any) -> tuple[Decimal | None, bool]:
    if raw is None:
        return None, False
    s = str(raw).strip()
    if not s:
        return None, False
    if re.fullmatch(r"[-+]?\d*[\.,]?\d+", s.replace(",", "")) is None:
        return None, True
    return _to_decimal(raw), False


def _deduccion_mensual_infonavit(row: dict[str, Any], umi_diario: Decimal | None) -> tuple[Decimal | None, str]:
    tipo = str(row.get("tipo_valor_descuento") or "").upper()
    est = str(row.get("estatus_infonavit") or "").upper()
    if "SUSPEND" in est:
        return None, "suspendido"
    ms = str(row.get("match_status") or "").lower()
    if ms in {"no_match", "pending_review", "probable_match"}:
        return None, f"match_no_confiable:{ms}"
    if est not in {"ACTIVO", "ACTIVO_MODIFICADO"}:
        return None, f"estatus_no_activo:{est or 'vacío'}"
    pesos = row.get("descuento_monto_pesos")
    if "PESO" in tipo or (pesos is not None and float(pesos or 0) > 0):
        return q2(_to_decimal(pesos)), "pesos"
    # VSM: cuota_fija_calculada = factor_vsm * UMI (sin 30.4; no doble conversión)
    if umi_diario is None or umi_diario <= 0:
        return None, "umi_no_disponible"
    fvm = row.get("descuento_factor_vsm")
    if fvm is not None and float(fvm or 0) > 0:
        return q2(_to_decimal(fvm) * umi_diario), "vsm_factor_umi"
    cf = row.get("descuento_cf_calculada")
    if cf is not None and float(cf or 0) > 0 and "VSM" in tipo:
        return q2(_to_decimal(cf) * umi_diario), "vsm_cf_umi"
    return None, "sin_monto_aplicable"


def calcular_empleado_nomina(
    emp: EmpleadoCalcInput,
    cfg: CalcularNominaConfig,
    *,
    umi_diario: Decimal | None,
) -> dict[str, Any]:
    warnings: list[str] = []
    blocks: list[str] = []
    detail: dict[str, Any] = {"domingo_opcion": cfg.domingo_opcion}

    dias_info = calcular_dias_y_septimo(emp.daily_values)
    warnings.extend(dias_info["warnings"])
    dias_computable = Decimal(str(dias_info["dias_computables"]))
    septimo = Decimal(str(dias_info["septimo_dia"]))
    dias_pago = Decimal(str(dias_info["dias_pago"]))
    fl_n = int(dias_info["festivo_laborado_detected"])
    dl_n = int(dias_info["domingo_laborado_detected"])

    hc_ms = str(emp.headcount_match_status or "").lower()
    if hc_ms and hc_ms in {"no_match", "pending_review", "probable_match"}:
        warnings.append("review_no_confident_headcount_match")

    he = _to_decimal(emp.he_raw)
    hen = _to_decimal(emp.horas_extra_normales_raw)
    dcn = _to_decimal(emp.dias_cubiertos_normales_raw)
    vac_lab = _to_decimal(emp.vacaciones_laboradas_raw)

    salario_op = emp.salario_operativo
    valor_he_param = emp.valor_x_he

    blocked_salary = salario_op is None or salario_op <= 0
    if blocked_salary:
        blocks.append(WARN_BLOCK_CALC_MISSING_SALARY)

    block_he_operativo = False
    if he > 0 and (valor_he_param is None or valor_he_param <= 0):
        blocks.append(WARN_BLOCK_CALC_MISSING_VALOR_HE)
        block_he_operativo = True

    smg = emp.smg_usado
    exento_he = emp.exento_he_usado

    valor_he_fiscal = calcular_valor_he_fiscal(he, smg) if not blocked_salary else Decimal(0)
    valor_extra_operativo = Decimal(0)
    if he > 0 and not block_he_operativo and valor_he_param:
        valor_extra_operativo = q2(valor_he_param * he)
    elif he > 0 and block_he_operativo:
        valor_extra_operativo = Decimal(0)

    sueldo_diario_op = (salario_op / Decimal(7)) if salario_op and salario_op > 0 else Decimal(0)
    sueldo_base_smg = q2(dias_pago * smg) if not blocked_salary else Decimal(0)

    importe_he_norm = q2((sueldo_diario_op / Decimal(8)) * hen) if not blocked_salary else Decimal(0)
    importe_dcn = q2(sueldo_diario_op * dcn) if not blocked_salary else Decimal(0)
    importe_fl = q2(sueldo_diario_op * Decimal(2) * Decimal(fl_n)) if not blocked_salary else Decimal(0)
    factor_dl = domingo_factor_from_opcion(cfg.domingo_opcion)
    importe_dl = q2(sueldo_diario_op * factor_dl * Decimal(dl_n)) if not blocked_salary else Decimal(0)
    detail["factor_domingo_laborado"] = str(factor_dl)

    bono_dec, bono_warn = _numeric_or_warn(emp.bono_raw)
    if bono_warn:
        warnings.append("bono_manual_no_numerico_revisar")
    bono_manual = bono_dec if bono_dec is not None else Decimal(0)
    ded_dec, ded_warn = _numeric_or_warn(emp.deducciones_raw)
    if ded_warn:
        warnings.append("deduccion_manual_no_numerica_revisar")
    deduccion_manual = ded_dec if ded_dec is not None else Decimal(0)

    concepto_gravable = q2(importe_he_norm + importe_dcn + importe_fl + importe_dl + bono_manual)

    # Prima vacacional (SOLICITA + módulo vacaciones)
    importe_prima_vac = Decimal(0)
    dias_prima_pend = Decimal(0)
    prima_aplicada = 0
    vac_row = emp.vacaciones_row
    prima_sol = _norm_header(emp.prima_vacacional_raw)
    if prima_sol == "SOLICITA":
        if not vac_row:
            warnings.append("prima_vacacional_sin_datos_vacaciones")
        else:
            dia_corr = _to_decimal(vac_row.get("dias_vacaciones_historico"))
            if dia_corr <= 0:
                dia_corr = _to_decimal(vac_row.get("dias_restantes_calculado")) + _to_decimal(
                    vac_row.get("dias_utilizados")
                )
            dias_pag_prima = _to_decimal(vac_row.get("dias_pagados"))
            prima_pagada_flag = int(vac_row.get("prima_2026_pagada") or 0) == 1
            if prima_pagada_flag and dias_pag_prima >= dia_corr > 0:
                warnings.append("prima_vacacional_ya_cubierta")
            elif dia_corr > 0:
                pend = max(Decimal(0), dia_corr - dias_pag_prima)
                if pend > 0 and not blocked_salary:
                    dias_prima_pend = pend
                    importe_prima_vac = q2(sueldo_diario_op * Decimal("0.25") * pend)
                    prima_aplicada = 1
                elif pend == 0:
                    warnings.append("prima_vacacional_ya_cubierta")

    concepto_exento = q2(importe_prima_vac)

    importe_vac_lab = q2(sueldo_diario_op * vac_lab) if not blocked_salary else Decimal(0)
    if vac_lab > 0 and vac_row:
        rest = _to_decimal(vac_row.get("dias_restantes_calculado"))
        if rest < vac_lab:
            warnings.append("vacaciones_laboradas_saldo_insuficiente")

    base_gravada = Decimal(0)
    isr = Decimal(0)
    bono_tpt = Decimal(0)
    prima_ef = Decimal(0)
    inf_mensual: Decimal | None = None
    inf_semanal = Decimal(0)
    inf_status = "no_aplica"

    if not blocked_salary:
        base_gravada = calcular_base_gravada(sueldo_base_smg, concepto_gravable, valor_he_fiscal, exento_he)
        total_perc_pre_isr = q2(
            sueldo_base_smg + valor_he_fiscal + concepto_gravable + concepto_exento
        )
        isr = calcular_isr_2026(
            base_gravada,
            total_percepciones_periodo=total_perc_pre_isr,
            dias_tarifa_isr=cfg.dias_tarifa_isr,
            dias_tarifa_subs=cfg.dias_tarifa_subs,
            es_fin_de_mes=cfg.es_fin_de_mes,
            es_frontera=emp.es_frontera,
            permitir_negativo=cfg.permitir_negativo_isr,
        )
        bono_tpt, prima_ef = calcular_bono_tpt_y_prima_eficiencia(
            salario_operativo=salario_op or Decimal(0),
            smg=smg,
            dias_pago=dias_pago,
            valor_x_he=valor_he_param or Decimal(0),
            horas_extra=he,
            valor_he_fiscal=valor_he_fiscal,
            isr=isr,
        )

    # INFONAVIT
    if emp.infonavit_row and cfg.fecha_inicio and cfg.fecha_fin:
        dm, st = _deduccion_mensual_infonavit(emp.infonavit_row, umi_diario)
        inf_status = st
        if dm is not None and dm > 0:
            inf_mensual = dm
            inf_semanal, inf_detail = calcular_infonavit_semanal(dm, cfg.fecha_inicio, cfg.fecha_fin)
            detail["infonavit"] = inf_detail
        else:
            if st not in {"suspendido"}:
                warnings.append(f"infonavit_no_aplicado_automaticamente:{st}")
    else:
        if emp.nss and emp.nss.strip():
            warnings.append("infonavit_sin_registro_para_nss")

    total_perc, total_ded, _, _ = recalcular_totales_percepciones_deducciones(
        sueldo_base_smg=sueldo_base_smg,
        valor_he_fiscal=valor_he_fiscal,
        concepto_gravable=concepto_gravable,
        concepto_exento=concepto_exento,
        bono_tpt=bono_tpt,
        prima_eficiencia=prima_ef,
        isr=isr,
        infonavit_semanal=inf_semanal,
        deduccion_manual=deduccion_manual,
    )

    row_status = "bloqueado" if blocked_salary else "calculado"

    out: dict[str, Any] = {
        "asistencia_row_id": emp.asistencia_row_id,
        "parametro_empleado_id": emp.parametro_empleado_id,
        "vacaciones_empleado_id": emp.vacaciones_empleado_id,
        "infonavit_row_id": emp.infonavit_row_id,
        "nss": emp.nss,
        "numero_empleado": emp.numero_empleado,
        "nombre_empleado": emp.nombre_empleado,
        "cliente": emp.cliente,
        "planta": emp.planta,
        "puesto": emp.puesto,
        "banco": emp.banco,
        "cuenta": emp.cuenta,
        "salario_operativo": float(salario_op) if salario_op else None,
        "valor_x_he": float(valor_he_param) if valor_he_param else None,
        "es_frontera": 1 if emp.es_frontera else 0,
        "smg_usado": float(smg),
        "exento_he_usado": float(exento_he),
        "dias_computables": float(dias_computable),
        "septimo_dia": float(septimo),
        "dias_pago": float(dias_pago),
        "horas_extra": float(he),
        "valor_he_fiscal": float(valor_he_fiscal),
        "valor_extra_operativo": float(valor_extra_operativo),
        "horas_extra_normales": float(hen),
        "importe_horas_extra_normales": float(importe_he_norm),
        "dias_cubiertos_normales": float(dcn),
        "importe_dias_cubiertos_normales": float(importe_dcn),
        "festivo_laborado_detected": fl_n,
        "importe_festivo_laborado": float(importe_fl),
        "domingo_laborado_detected": dl_n,
        "importe_domingo_laborado": float(importe_dl),
        "vacaciones_laboradas": float(vac_lab),
        "importe_vacaciones_laboradas": float(importe_vac_lab),
        "prima_vacacional_aplicada": prima_aplicada,
        "dias_prima_vacacional_pendientes": float(dias_prima_pend),
        "importe_prima_vacacional": float(importe_prima_vac),
        "bono_manual": float(bono_manual),
        "bono_manual_clasificacion": "gravable_default",
        "deduccion_manual": float(deduccion_manual),
        "sueldo_base_smg": float(sueldo_base_smg),
        "concepto_gravable": float(concepto_gravable),
        "concepto_exento": float(concepto_exento),
        "base_gravada": float(base_gravada),
        "isr": float(isr),
        "bono_tpt": float(bono_tpt),
        "prima_eficiencia": float(prima_ef),
        "infonavit_mensual": float(inf_mensual) if inf_mensual is not None else None,
        "infonavit_semanal": float(inf_semanal),
        "infonavit_status": inf_status,
        "total_percepciones": float(total_perc),
        "total_deducciones": float(total_ded),
        "warnings_json": warnings,
        "blocks_json": blocks,
        "detail_json": detail,
        "manual_overrides_json": {},
        "row_status": row_status,
    }
    enriquecer_netos_y_auditoria(
        out,
        salario_op=salario_op,
        dias_pago=dias_pago,
        he=he,
        valor_x_he_para_extra=valor_he_param,
        concepto_gravable=concepto_gravable,
        concepto_exento=concepto_exento,
        total_perc=total_perc,
        total_ded=total_ded,
        isr=isr,
        inf_semanal=inf_semanal,
        detail=detail,
    )
    return out


def calcular_nomina_preliminar(
    empleados: list[EmpleadoCalcInput],
    cfg: CalcularNominaConfig,
    *,
    umi_diario: Decimal | None,
) -> list[dict[str, Any]]:
    return [calcular_empleado_nomina(e, cfg, umi_diario=umi_diario) for e in empleados]


def aplicar_overrides_a_fila(
    base_row: dict[str, Any],
    overrides: dict[str, Any],
    *,
    cfg: CalcularNominaConfig,
) -> dict[str, Any]:
    """Fusiona manual_overrides_json y recalcula totales y base/isr si aplica."""
    row = dict(base_row)
    o = dict(overrides or {})
    for k in (
        "concepto_gravable",
        "concepto_exento",
        "bono_manual",
        "deduccion_manual",
        "importe_domingo_laborado",
        "importe_festivo_laborado",
        "infonavit_semanal",
        "isr",
        "bono_tpt",
        "prima_eficiencia",
    ):
        if k in o and o[k] is not None and o[k] != "":
            try:
                row[k] = float(Decimal(str(o[k])))
            except InvalidOperation:
                pass
    sueldo_base_smg = Decimal(str(row.get("sueldo_base_smg") or 0))
    valor_he_fiscal = Decimal(str(row.get("valor_he_fiscal") or 0))
    cg = Decimal(str(row.get("concepto_gravable") or 0))
    ce = Decimal(str(row.get("concepto_exento") or 0))
    smg = Decimal(str(row.get("smg_usado") or 0))
    exento_he = Decimal(str(row.get("exento_he_usado") or 0))
    es_frontera = bool(int(row.get("es_frontera") or 0))
    if "base_gravada" not in o or o.get("base_gravada") in (None, ""):
        row["base_gravada"] = float(calcular_base_gravada(sueldo_base_smg, cg, valor_he_fiscal, exento_he))
    else:
        row["base_gravada"] = float(Decimal(str(o["base_gravada"])))
    bg = Decimal(str(row.get("base_gravada") or 0))
    total_perc_pre_isr = q2(sueldo_base_smg + valor_he_fiscal + cg + ce)
    if "isr" not in o or o.get("isr") in (None, ""):
        row["isr"] = float(
            calcular_isr_2026(
                bg,
                total_percepciones_periodo=total_perc_pre_isr,
                dias_tarifa_isr=cfg.dias_tarifa_isr,
                dias_tarifa_subs=cfg.dias_tarifa_subs,
                es_fin_de_mes=cfg.es_fin_de_mes,
                es_frontera=es_frontera,
                permitir_negativo=cfg.permitir_negativo_isr,
            )
        )
    isr = Decimal(str(row.get("isr") or 0))
    bono_tpt = Decimal(str(row.get("bono_tpt") or 0))
    prima_ef = Decimal(str(row.get("prima_eficiencia") or 0))
    inf_sem = Decimal(str(row.get("infonavit_semanal") or 0))
    ded = Decimal(str(row.get("deduccion_manual") or 0))
    tp, td, _, _ = recalcular_totales_percepciones_deducciones(
        sueldo_base_smg=sueldo_base_smg,
        valor_he_fiscal=valor_he_fiscal,
        concepto_gravable=cg,
        concepto_exento=ce,
        bono_tpt=bono_tpt,
        prima_eficiencia=prima_ef,
        isr=isr,
        infonavit_semanal=inf_sem,
        deduccion_manual=ded,
    )
    row["total_percepciones"] = float(tp)
    row["total_deducciones"] = float(td)
    det = row.get("detail_json") if isinstance(row.get("detail_json"), dict) else {}
    salario_ov = row.get("salario_operativo")
    try:
        sal_dec = Decimal(str(salario_ov)) if salario_ov is not None else None
    except InvalidOperation:
        sal_dec = None
    if sal_dec is not None and sal_dec <= 0:
        sal_dec = None
    enriquecer_netos_y_auditoria(
        row,
        salario_op=sal_dec,
        dias_pago=Decimal(str(row.get("dias_pago") or 0)),
        he=Decimal(str(row.get("horas_extra") or 0)),
        valor_x_he_para_extra=Decimal(str(row.get("valor_x_he") or 0)),
        concepto_gravable=cg,
        concepto_exento=ce,
        total_perc=tp,
        total_ded=td,
        isr=isr,
        inf_semanal=inf_sem,
        detail=det,
    )
    row["manual_overrides_json"] = o
    return row
