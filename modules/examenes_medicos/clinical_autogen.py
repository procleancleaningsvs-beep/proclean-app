"""
Generación sintética de resultados clínicos dentro de rangos de referencia típicos.

No modifica plantillas: solo produce cadenas para placeholders.
"""

from __future__ import annotations

import random
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from modules.examenes_medicos.reference_ranges import (
    GENERATED_CLINICAL_PLACEHOLDER_NAMES,
    REFERENCE_FIELDS,
    ReferenceField,
)


def _r(rng: random.Random, lo: float, hi: float, nd: int = 2) -> float:
    return round(rng.uniform(lo, hi), nd)


def _fmt(v: float, nd: int = 2) -> str:
    s = f"{v:.{nd}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _decimal_places(value: Decimal | None) -> int:
    if value is None:
        return 0
    exp = value.as_tuple().exponent
    return abs(exp) if exp < 0 else 0


def _fmt_decimal(value: Decimal, nd: int) -> str:
    if nd <= 0:
        return str(int(value))
    quantum = Decimal("1").scaleb(-nd)
    return format(value.quantize(quantum, rounding=ROUND_HALF_UP), "f")


def _generated_numeric(rng: random.Random, field: ReferenceField) -> str:
    assert field.minimum is not None and field.maximum is not None
    nd = max(_decimal_places(field.minimum), _decimal_places(field.maximum))
    if nd == 0:
        lo = int(field.minimum)
        hi = int(field.maximum) if field.max_inclusive else int(field.maximum) - 1
        return str(rng.randint(lo, hi))
    scale = Decimal(10) ** nd
    lo_i = int(field.minimum * scale)
    hi_i = int(field.maximum * scale)
    if not field.max_inclusive:
        hi_i -= 1
    n = Decimal(rng.randint(lo_i, hi_i)) / scale
    return _fmt_decimal(n, nd)


def _generated_value(rng: random.Random, field: ReferenceField) -> str:
    if field.validation_type == "numeric_range":
        return _generated_numeric(rng, field)
    if field.validation_type == "exact_option":
        if "Ausentes" in field.options:
            return "Ausentes"
        return rng.choice(list(field.options))
    if field.validation_type == "negative_or_less_than":
        if rng.random() < 0.65:
            return "Negativo"
        return _generated_numeric(rng, field)
    if field.validation_type == "leukocyte_count":
        return rng.choice(["Ausentes", "1", "2", "3", "4", "5", "1-2", "2-4", "4-5"])
    if field.validation_type == "erythrocyte_count":
        return rng.choice(["Ausentes", "1", "2", "1-2"])
    raise ValueError(f"Tipo de validacion no soportado para autogeneracion: {field.validation_type}")


def generate_unified_clinical_results(rng: random.Random) -> dict[str, str]:
    return {
        name: _generated_value(rng, REFERENCE_FIELDS[name])
        for name in GENERATED_CLINICAL_PLACEHOLDER_NAMES
    }


def generate_clinical_bundle(
    *,
    sexo: str,
    seed: int | None = None,
) -> dict[str, Any]:
    """
    Devuelve {"orina": {...}, "sangre": {...}} para fusionar con datos del formulario maestro.

    `sexo`: Femenino | Masculino (acepta Mujer/Hombre históricos para rangos).
    """
    rng = random.Random(seed)
    mujer = (sexo or "").strip() in {"Femenino", "Mujer"}

    aspecto = rng.choice(["Límpido", "Límpido", "Ligeramente turbio"])
    color = rng.choice(["Amarillo", "Amarillo", "Ámbar"])
    densidad = _r(rng, 1.010, 1.028, 3)
    ph_orina = _r(rng, 5.0, 7.5, 1)

    orina = {
        "aspecto": aspecto,
        "color": color,
        "densidad": _fmt(densidad, 3),
        "ph_orina": _fmt(ph_orina, 1),
        "eritrocitos": rng.choice(["0", "0", "0-2", "Escasas"]),
        "leucocitos": rng.choice(["0", "0-5", "Escasas", "Escasas"]),
    }

    leuc = _r(rng, 4.8, 9.5, 2)
    erit = _r(rng, 4.2 if mujer else 4.5, 5.2 if mujer else 5.7, 2)
    hb = _r(rng, 12.5 if mujer else 13.5, 15.5 if mujer else 17.0, 1)
    hto = _r(rng, 37 if mujer else 40, 47 if mujer else 52, 1)
    vcm = _r(rng, 80, 98, 1)
    hcm = _r(rng, 26, 34, 1)
    chcm = _r(rng, 31, 35, 1)
    ad_de = _r(rng, 35.5, 43.5, 1)
    ad_cv = _r(rng, 11.6, 14.4, 1)
    plt = int(rng.randint(180, 380))
    vpm = _r(rng, 8.0, 12.0, 1)

    neut_pct = _r(rng, 48, 72, 1)
    lymph_pct = _r(rng, 18, 38, 1)
    mono_pct = _r(rng, 3, 9, 1)
    eos_pct = _r(rng, 1, 5, 1)
    baso_pct = round(100.0 - neut_pct - lymph_pct - mono_pct - eos_pct, 1)
    if baso_pct < 0.3:
        baso_pct = 0.5
        neut_pct = round(neut_pct - 0.5, 1)

    sangre = {
        "leucocitos": _fmt(leuc, 2),
        "eritrocitos": _fmt(erit, 2),
        "hemoglobina": _fmt(hb, 1),
        "hematocrito": _fmt(hto, 1),
        "VCM": _fmt(vcm, 1),
        "HCM": _fmt(hcm, 1),
        "conc_media_hb_corp": _fmt(chcm, 1),
        "AD_D.E.": _fmt(ad_de, 1),
        "AD_C.V.": _fmt(ad_cv, 1),
        "plaquetas": str(plt),
        "V_plaquetario_medio": _fmt(vpm, 1),
        "linfocitos_pct": _fmt(lymph_pct, 1),
        "neutrofilos_pct": _fmt(neut_pct, 1),
        "monocitos_pct": _fmt(mono_pct, 1),
        "eosinofilos_pct": _fmt(eos_pct, 1),
        "basofilos_pct": _fmt(baso_pct, 1),
        "linfocitos_abs": _fmt(_r(rng, 1.0, 3.2, 2), 2),
        "neutrofilos_abs": _fmt(_r(rng, 2.5, 6.5, 2), 2),
        "monocitos_abs": _fmt(_r(rng, 0.15, 0.85, 2), 2),
        "eosinofilos_abs": _fmt(_r(rng, 0.04, 0.54, 2), 2),
        "basofilos_abs": _fmt(_r(rng, 0.01, 0.08, 2), 2),
        "glucosa": str(int(rng.randint(74, 99))),
        "urea": str(int(rng.randint(15, 38))),
        "bun": str(int(rng.randint(8, 22))),
        "creatinina": _fmt(_r(rng, 0.65, 1.15, 2), 2),
        "acido_urico": _fmt(_r(rng, 3.5, 6.8, 1), 1),
        "colesterol_total": str(int(rng.randint(145, 215))),
        "trigliceridos": str(int(rng.randint(65, 165))),
    }

    return {"orina": orina, "sangre": sangre, "unificado": generate_unified_clinical_results(rng)}
