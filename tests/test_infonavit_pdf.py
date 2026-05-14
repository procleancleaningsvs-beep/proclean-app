from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from modules.nomina.config import get_umi_for_year
from modules.nomina.infonavit_pdf import parse_infonavit_text

FIXTURE = Path(__file__).parent / "fixtures" / "infonavit_sample.txt"


def _parse():
    return parse_infonavit_text(FIXTURE.read_text(encoding="utf-8"))


def test_metadata_extraction():
    result = _parse()
    assert result.metadata["registro_patronal"] == "A0000000000"
    assert result.metadata["total_avisos_reportado"] == 4
    assert "13.05.2026" in result.metadata["fecha_corte"]


def test_row_count_matches_total():
    result = _parse()
    assert len(result.rows) == 4
    assert result.warnings == []  # no mismatch


def test_classification_retencion_pesos():
    rows = _parse().rows
    r = rows[0]
    assert r["nss"] == "10000000001"
    assert r["estatus_infonavit"] == "ACTIVO"
    assert r["descuento_raw"] == "$ 1234.56"
    assert "Pesos" in r["tipo_descuento"] or "PESOS" in r["tipo_descuento"].upper()


def test_classification_modificacion_pesos_multiline_name():
    rows = _parse().rows
    r = rows[1]
    assert r["estatus_infonavit"] == "ACTIVO_MODIFICADO"
    # name with line breaks should collapse
    assert "Empleada Dos" in r["nombre_trabajador"]
    assert "Cruza Linea" in r["nombre_trabajador"]
    assert r["descuento_raw"] == "$ 789.10"


def test_classification_suspension_sin_monto():
    rows = _parse().rows
    r = rows[2]
    assert r["estatus_infonavit"] == "SUSPENDIDO"
    assert r["descuento_raw"] == ""


def test_classification_retencion_vsm():
    rows = _parse().rows
    r = rows[3]
    assert r["estatus_infonavit"] == "ACTIVO"
    assert "VSM" in r["descuento_raw"]
    # name preserved with raw character variant
    assert "Mun" in r["nombre_trabajador"]


def test_vsm_conversion_uses_umi_decimal():
    from modules.nomina.blueprint import _infonavit_descuento_logic

    rows = _parse().rows
    vsm_row = rows[3]
    umi = get_umi_for_year(2026)
    assert umi == Decimal("100.81")
    _infonavit_descuento_logic(vsm_row, umi)
    assert vsm_row["tipo_valor_descuento"] == "VSM"
    assert vsm_row["umi_usada"] == 100.81
    assert vsm_row["descuento_factor_vsm"] == 12.3456
    # 12.3456 * 100.81 = 1244.560736 -> 1244.56 (rounded half-up to 2 decimals)
    assert vsm_row["descuento_cf_calculada"] == 1244.56


def test_umi_not_configured_does_not_calculate_cf():
    from modules.nomina.blueprint import _infonavit_descuento_logic

    rows = _parse().rows
    vsm_row = rows[3]
    _infonavit_descuento_logic(vsm_row, None)
    assert vsm_row["tipo_valor_descuento"] == "VSM"
    assert vsm_row["descuento_factor_vsm"] == 12.3456
    assert vsm_row["umi_usada"] is None
    assert vsm_row["descuento_cf_calculada"] is None
    assert vsm_row["editable_json"]["umi_no_configurada"] is True
    assert vsm_row["editable_json"]["listo_para_calculo"] is False
    assert any("UMI no configurada" in w for w in vsm_row["warnings"])


def test_suspension_never_marks_active_discount():
    from modules.nomina.blueprint import _infonavit_descuento_logic

    rows = _parse().rows
    sus = rows[2]
    _infonavit_descuento_logic(sus, get_umi_for_year(2026))
    assert sus["tipo_valor_descuento"] == "SIN_MONTO"
    assert sus["descuento_monto_pesos"] is None
    assert sus["descuento_cf_calculada"] is None
    assert sus["editable_json"]["aplicar_descuento"] is False
