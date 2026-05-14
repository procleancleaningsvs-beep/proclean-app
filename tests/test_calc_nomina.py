"""Tests motor preliminar nómina 4.1."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from modules.nomina.calc_nomina import (
    aplicar_overrides_a_fila,
    calcular_base_gravada,
    calcular_bono_tpt_y_prima_eficiencia,
    calcular_dias_y_septimo,
    calcular_infonavit_semanal,
    calcular_isr_2026,
    calcular_nomina_preliminar,
    calcular_valor_he_fiscal,
    CalcularNominaConfig,
    EmpleadoCalcInput,
    calcular_empleado_nomina,
    diff_nomina_row_vs_excel_fixture,
    enriquecer_netos_y_auditoria,
    q2,
    redondear_neto_operativo,
    terminacion_neto_operativo_valida,
    _deduccion_mensual_infonavit,
)
from modules.nomina.config import (
    BONO_TPT_TOPE_2026,
    SMG_BY_YEAR,
    salario_minimo_semanal_2026,
)

# ---------------------------------------------------------------------------
# Fixtures ISR_2026 (macro VBA): Round final a 2 decimales; isrMensual sin redondeo intermedio.
# Valores transcritos / verificados contra la macro (no derivados del código Python del motor).
# ---------------------------------------------------------------------------
MACRO_ISR_2026_BASE_2300_D7_SUBSIDIO = Decimal("53.75")
MACRO_ISR_2026_BASE_3000_D7_SIN_SUBSIDIO = Decimal("276.20")
# Borde: isrMensual pleno − subsidio deja residual < 1 centavo antes del Round final.
MACRO_ISR_2026_BASE_1805_98_D7_SUBSIDIO = Decimal("0.01")


def test_septimo_dia_y_dias_pago():
    d = calcular_dias_y_septimo(["A", "A", "A", "A", "A", "V", "D"])
    assert d["dias_computables"] == 6.0
    assert d["septimo_dia"] == 1.0
    assert d["dias_pago"] == 7.0
    d2 = calcular_dias_y_septimo(["A", "A", "A", "A", "V", "F", "D"])
    assert d2["dias_computables"] == 5.0
    assert d2["septimo_dia"] == 0.83
    assert d2["dias_pago"] == 5.83


def test_ni_no_computa():
    d = calcular_dias_y_septimo(["NI", "A", "A", "A", "A", "A", "A"])
    assert d["dias_computables"] == 6.0
    assert any("nuevo_ingreso" in w for w in d["warnings"])


def test_r_computa_y_warning():
    d = calcular_dias_y_septimo(["R", "A", "A", "A", "A", "A", "D"])
    assert d["dias_computables"] == 6.0
    assert any("retardo" in w for w in d["warnings"])


def test_valor_he_fiscal_dobles_y_triples():
    smg = Decimal("315.04")
    vh = (Decimal("315.04") / Decimal(8)).quantize(Decimal("0.01"))
    assert calcular_valor_he_fiscal(Decimal(0), smg) == Decimal("0.00")
    assert calcular_valor_he_fiscal(Decimal(5), smg) == (Decimal(5) * vh * Decimal(2)).quantize(Decimal("0.01"))
    assert calcular_valor_he_fiscal(Decimal(9), smg) == (Decimal(9) * vh * Decimal(2)).quantize(Decimal("0.01"))
    he = Decimal(10)
    expect = (Decimal(9) * vh * Decimal(2) + Decimal(1) * vh * Decimal(3)).quantize(Decimal("0.01"))
    assert calcular_valor_he_fiscal(he, smg) == expect


def test_base_gravada_formula():
    smg = SMG_BY_YEAR[2026]["GENERAL"]
    ex = Decimal("236.28")
    sueldo_base = Decimal("7") * smg
    cg = Decimal("100")
    he_fiscal = Decimal("300")
    bg = calcular_base_gravada(sueldo_base, cg, he_fiscal, ex)
    assert bg == (sueldo_base + cg + (he_fiscal - ex)).quantize(Decimal("0.01"))


def test_isr_macro_cero_por_salario_minimo_semanal():
    """Macro: si TotalPercepcionesPeriodo <= SalarioMinimoSemanal → ISR = 0."""
    smg_sem = salario_minimo_semanal_2026(es_frontera=False)
    isr = calcular_isr_2026(
        Decimal("5000"),
        total_percepciones_periodo=smg_sem - Decimal("1"),
        dias_tarifa_isr=Decimal(7),
        dias_tarifa_subs=Decimal(7),
        es_fin_de_mes=False,
        es_frontera=False,
        permitir_negativo=False,
    )
    assert isr == Decimal("0.00")


def test_isr_macro_base_media_con_subsidio_fixture():
    """Base gravada media en rango de subsidio mensual 536.21 (macro)."""
    got = calcular_isr_2026(
        Decimal("2300"),
        total_percepciones_periodo=Decimal("5000"),
        dias_tarifa_isr=Decimal(7),
        dias_tarifa_subs=Decimal(7),
        es_fin_de_mes=False,
        es_frontera=False,
        permitir_negativo=False,
    )
    assert got == MACRO_ISR_2026_BASE_2300_D7_SUBSIDIO


def test_isr_macro_base_alta_sin_subsidio_fixture():
    """Base gravada alta: baseSub_Mes fuera de rango → subsidio 0 (macro)."""
    got = calcular_isr_2026(
        Decimal("3000"),
        total_percepciones_periodo=Decimal("10000"),
        dias_tarifa_isr=Decimal(7),
        dias_tarifa_subs=Decimal(7),
        es_fin_de_mes=False,
        es_frontera=False,
        permitir_negativo=False,
    )
    assert got == MACRO_ISR_2026_BASE_3000_D7_SIN_SUBSIDIO


def test_isr_macro_borde_residual_round_final():
    """Si se redondea isrMensual antes de periodo, este caso da 0.00; la macro da 0.01."""
    got = calcular_isr_2026(
        Decimal("1805.98"),
        total_percepciones_periodo=Decimal("5000"),
        dias_tarifa_isr=Decimal(7),
        dias_tarifa_subs=Decimal(7),
        es_fin_de_mes=False,
        es_frontera=False,
        permitir_negativo=False,
    )
    assert got == MACRO_ISR_2026_BASE_1805_98_D7_SUBSIDIO


def test_bono_tpt_tope_y_prima():
    b, p = calcular_bono_tpt_y_prima_eficiencia(
        salario_operativo=Decimal("5000"),
        smg=Decimal("315.04"),
        dias_pago=Decimal(7),
        valor_x_he=Decimal("100"),
        horas_extra=Decimal(5),
        valor_he_fiscal=Decimal("400"),
        isr=Decimal("50"),
        tope=BONO_TPT_TOPE_2026,
    )
    assert b <= BONO_TPT_TOPE_2026
    diff = (Decimal("5000") / Decimal(7) * Decimal(7)) - (Decimal("315.04") * Decimal(7))
    diff += (Decimal("100") * Decimal(5) - Decimal("400")) + Decimal("50")
    if diff > BONO_TPT_TOPE_2026:
        assert b == BONO_TPT_TOPE_2026
        assert p == (diff - BONO_TPT_TOPE_2026).quantize(Decimal("0.01"))


def test_redondear_neto_operativo_ejemplos():
    assert redondear_neto_operativo(Decimal("2700.01")) == Decimal("2700.20")
    assert redondear_neto_operativo(Decimal("2700.20")) == Decimal("2700.20")
    assert redondear_neto_operativo(Decimal("2700.21")) == Decimal("2700.40")
    assert redondear_neto_operativo(Decimal("2700.79")) == Decimal("2700.80")
    assert redondear_neto_operativo(Decimal("2700.85")) == Decimal("2701.00")
    assert redondear_neto_operativo(Decimal("2700.99")) == Decimal("2701.00")


def test_base_neto_simple_y_neto_simple_operativo():
    """NETO SIMPLE: (salario/7)*dias + extra + grav + exento − INFONAVIT; operativo = redondeo .00/.20…"""
    row: dict = {"detail_json": {}}
    salario = Decimal("3500")
    dias_pago = Decimal(7)
    he = Decimal(2)
    vx = Decimal("80")
    cg = Decimal("100")
    ce = Decimal("50")
    inf = Decimal("120.50")
    enriquecer_netos_y_auditoria(
        row,
        salario_op=salario,
        dias_pago=dias_pago,
        he=he,
        valor_x_he_para_extra=vx,
        concepto_gravable=cg,
        concepto_exento=ce,
        total_perc=Decimal("4000"),
        total_ded=Decimal("500"),
        isr=Decimal("100"),
        inf_semanal=inf,
        detail={},
    )
    valor_extra = vx * he
    base_esperada = (salario / Decimal(7)) * dias_pago + valor_extra + cg + ce - inf
    assert Decimal(str(row["base_neto_simple"])) == base_esperada
    assert Decimal(str(row["neto_simple_operativo"])) == redondear_neto_operativo(base_esperada)


def test_neto_a_pagar_final_terminacion_operativa():
    row: dict = {"detail_json": {}}
    enriquecer_netos_y_auditoria(
        row,
        salario_op=Decimal("4000"),
        dias_pago=Decimal(7),
        he=Decimal(0),
        valor_x_he_para_extra=Decimal("90"),
        concepto_gravable=Decimal(0),
        concepto_exento=Decimal(0),
        total_perc=Decimal("3000"),
        total_ded=Decimal("2700.07"),
        isr=Decimal("10"),
        inf_semanal=Decimal(0),
        detail={},
    )
    final_d = Decimal(str(row["neto_a_pagar_final"]))
    assert terminacion_neto_operativo_valida(final_d)


def test_diff_nomina_row_vs_excel_fixture_sin_diferencias():
    fila = {
        "dias_computables": 6.0,
        "septimo_dia": 1.0,
        "dias_pago": 7.0,
        "valor_he_fiscal": 0.0,
        "base_gravada": 2300.0,
        "isr": 53.75,
        "bono_tpt": 0.0,
        "prima_eficiencia": 0.0,
        "infonavit_semanal": 100.0,
        "neto_simple_operativo": 4000.0,
        "neto_a_pagar_final": 3920.20,
    }
    esperado = {k: fila[k] for k in fila}
    assert diff_nomina_row_vs_excel_fixture(fila, esperado) == []


def test_diff_nomina_row_vs_excel_fixture_detecta_diferencia_y_alias_dias():
    fila = {"dias_computables": 7.0}
    assert diff_nomina_row_vs_excel_fixture(fila, {"dias": "7"}) == []
    diffs = diff_nomina_row_vs_excel_fixture(fila, {"dias": "6"})
    assert len(diffs) == 1
    assert "dias_computables" in diffs[0]
    row = {
        "tipo_valor_descuento": "VSM",
        "estatus_infonavit": "ACTIVO",
        "match_status": "exact_nss",
        "descuento_factor_vsm": 26.0528,
    }
    dm, st = _deduccion_mensual_infonavit(row, Decimal("100.81"))
    assert st == "vsm_factor_umi"
    assert dm == Decimal("2626.38")


def test_infonavit_vsm_factor_por_umi_sin_30_4():
    row = {
        "tipo_valor_descuento": "VSM",
        "estatus_infonavit": "ACTIVO",
        "match_status": "exact_nss",
        "descuento_factor_vsm": 26.0528,
    }
    dm, st = _deduccion_mensual_infonavit(row, Decimal("100.81"))
    assert st == "vsm_factor_umi"
    assert dm == Decimal("2626.38")


def test_infonavit_semanal_con_cuota_vsm_2626():
    sem, detail = calcular_infonavit_semanal(Decimal("2626.38"), date(2026, 6, 29), date(2026, 7, 5))
    dias_b = detail["dias_bimestre_vigente"]
    assert dias_b == 62
    esperado = ((Decimal("2626.38") * Decimal(2)) / Decimal(dias_b)) * Decimal(7)
    assert sem == esperado.quantize(Decimal("0.01"))


def test_infonavit_bimestre_siguiente_si_cruza():
    ded_m = Decimal("1200")
    sem, detail = calcular_infonavit_semanal(ded_m, date(2026, 6, 29), date(2026, 7, 5))
    assert detail["bimestre_index"] == 3
    dias_b = detail["dias_bimestre_vigente"]
    expect = ((ded_m * Decimal(2)) / Decimal(dias_b)) * Decimal(7)
    assert sem == expect.quantize(Decimal("0.01"))


def test_fl_importe_en_empleado():
    cfg = CalcularNominaConfig(
        fecha_inicio=date(2026, 1, 5),
        fecha_fin=date(2026, 1, 11),
        domingo_opcion="proporcional",
    )
    emp = EmpleadoCalcInput(
        asistencia_row_id=1,
        nombre_empleado="X",
        cliente="C",
        planta="P",
        puesto="",
        banco="",
        cuenta="",
        nss="123",
        daily_values=["FL", "FL", "A", "A", "A", "A", "A"],
        he_raw="0",
        horas_extra_normales_raw="0",
        dias_cubiertos_normales_raw="0",
        vacaciones_laboradas_raw="0",
        prima_vacacional_raw="N/A",
        bono_raw="",
        deducciones_raw="",
        headcount_match_status="exact_nss",
        parametro_empleado_id=1,
        numero_empleado="1",
        salario_operativo=Decimal("3500"),
        valor_x_he=Decimal("80"),
        es_frontera=False,
        smg_usado=SMG_BY_YEAR[2026]["GENERAL"],
        exento_he_usado=Decimal("236.28"),
        vacaciones_empleado_id=None,
        vacaciones_row=None,
        infonavit_row_id=None,
        infonavit_row=None,
    )
    r = calcular_empleado_nomina(emp, cfg, umi_diario=Decimal("100.81"))
    assert r["festivo_laborado_detected"] == 2
    assert r["importe_festivo_laborado"] > 0


def test_deducciones_no_reducen_base_gravada_en_aplicar():
    cfg = CalcularNominaConfig(
        fecha_inicio=date(2026, 2, 1),
        fecha_fin=date(2026, 2, 7),
    )
    base = {
        "sueldo_base_smg": 2205.28,
        "concepto_gravable": 100.0,
        "concepto_exento": 0.0,
        "valor_he_fiscal": 0.0,
        "smg_usado": 315.04,
        "exento_he_usado": 236.28,
        "es_frontera": 0,
        "isr": 10.0,
        "bono_tpt": 0.0,
        "prima_eficiencia": 0.0,
        "infonavit_semanal": 0.0,
        "deduccion_manual": 500.0,
        "salario_operativo": 5000.0,
        "dias_pago": 7.0,
        "horas_extra": 0.0,
        "valor_x_he": 50.0,
        "detail_json": {},
    }
    out = aplicar_overrides_a_fila(base, {"deduccion_manual": 500.0}, cfg=cfg)
    assert out["base_gravada"] == float(
        calcular_base_gravada(Decimal("2205.28"), Decimal("100"), Decimal(0), Decimal("236.28"))
    )


def test_nomina_preliminar_un_empleado():
    cfg = CalcularNominaConfig(fecha_inicio=date(2026, 1, 5), fecha_fin=date(2026, 1, 11))
    emp = EmpleadoCalcInput(
        asistencia_row_id=2,
        nombre_empleado="Y",
        cliente="C",
        planta="",
        puesto="",
        banco="",
        cuenta="",
        nss="999",
        daily_values=["A"] * 7,
        he_raw="0",
        horas_extra_normales_raw="0",
        dias_cubiertos_normales_raw="0",
        vacaciones_laboradas_raw="0",
        prima_vacacional_raw="N/A",
        bono_raw="",
        deducciones_raw="",
        headcount_match_status=None,
        parametro_empleado_id=None,
        numero_empleado=None,
        salario_operativo=Decimal("4000"),
        valor_x_he=Decimal("90"),
        es_frontera=False,
        smg_usado=SMG_BY_YEAR[2026]["GENERAL"],
        exento_he_usado=Decimal("236.28"),
        vacaciones_empleado_id=None,
        vacaciones_row=None,
        infonavit_row_id=None,
        infonavit_row=None,
    )
    rows = calcular_nomina_preliminar([emp], cfg, umi_diario=None)
    assert len(rows) == 1
    assert rows[0]["row_status"] == "calculado"
