from __future__ import annotations

from modules.nomina.asistencia_groups import (
    GROUP_AURIGA,
    GROUP_CARRIER,
    GROUP_PEPSI,
    GROUP_VITRO,
    classify_asistencia_group,
    normalize_group_text,
)


def test_classify_asistencia_group_priority():
    assert classify_asistencia_group(["Pepsi Planta 1"]) == GROUP_PEPSI
    assert classify_asistencia_group(["carrier monterrey"]) == GROUP_CARRIER
    assert classify_asistencia_group(["Torre Balzak", "Armida"]) == GROUP_AURIGA
    assert classify_asistencia_group(["Vitro Saltillo"]) == GROUP_VITRO
    assert classify_asistencia_group(["Pepsi + Carrier + GM"]) == GROUP_PEPSI


def test_classify_asistencia_group_varios_clients_fallback():
    assert classify_asistencia_group(["Sin cliente claro"], is_varios_clients=True) == GROUP_VITRO
    assert classify_asistencia_group(["Cliente nuevo no catalogado"]) is None


def test_normalize_group_text_removes_accents_and_noise():
    assert normalize_group_text("  Días   varios / vítró  ") == "DIAS VARIOS VITRO"
