from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class ReferenceField:
    placeholder: str
    label: str
    validation_type: str
    minimum: Decimal | None
    maximum: Decimal | None
    min_inclusive: bool
    max_inclusive: bool
    unit: str
    visible_limit: str
    options: tuple[str, ...] = ()


ADMIN_PLACEHOLDER_NAMES: tuple[str, ...] = (
    "folio",
    "orden",
    "paciente_id",
    "paciente_nombre",
    "sexo",
    "fecha_nacimiento",
    "edad",
    "fecha_registro",
)

EXPECTED_UNIFIED_PLACEHOLDER_NAMES: tuple[str, ...] = (
    *ADMIN_PLACEHOLDER_NAMES,
    "gluco",
    "urea",
    "bun",
    "creat",
    "acido",
    "col_t",
    "col_hd",
    "trigli",
    "col_ld",
    "col_nh",
    "ind_at",
    "prot_t",
    "albu",
    "globu",
    "relac",
    "bili_t",
    "bili_d",
    "bili_i",
    "asttgo",
    "alttgp",
    "fosact",
    "gamagt",
    "ldh",
    "hierro",
    "calcio",
    "sodio",
    "potas",
    "leuco",
    "eritro",
    "hemog",
    "hemat",
    "vcrm",
    "hemcm",
    "comhc",
    "rdw_cv",
    "rdw_sd",
    "plaqut",
    "volpm",
    "neutpc",
    "linfpc",
    "monopc",
    "eosipc",
    "basopc",
    "neut_a",
    "linf_a",
    "mono_a",
    "eosi_a",
    "baso_a",
    "gsanth",
    "t_pro",
    "pct_ac",
    "t_tpa",
    "wsterg",
    "vitad",
    "o_col",
    "o_asp",
    "o_dens",
    "o_ph",
    "o_el",
    "o_nit",
    "o_pro",
    "o_glu",
    "o_cet",
    "o_bili",
    "o_uro",
    "o_hemo",
    "o_leu",
    "o_eri",
    "o_erid",
    "o_cili",
    "o_cri",
    "o_cpa",
    "o_dtra",
    "o_ctr",
    "o_rmuc",
    "o_bact",
    "o_leva",
)

EXPECTED_UNIFIED_PLACEHOLDERS: tuple[str, ...] = tuple(
    f"{{{{{name}}}}}" for name in EXPECTED_UNIFIED_PLACEHOLDER_NAMES
)

BLOOD_GROUP_OPTIONS: tuple[str, ...] = (
    "A Positivo",
    "A Negativo",
    "B Positivo",
    "B Negativo",
    "AB Positivo",
    "AB Negativo",
    "O Positivo",
    "O Negativo",
)


def _d(value: str) -> Decimal:
    return Decimal(value)


def _range(
    placeholder: str,
    label: str,
    minimum: str,
    maximum: str,
    unit: str,
    visible_limit: str,
    *,
    min_inclusive: bool = True,
    max_inclusive: bool = True,
) -> ReferenceField:
    return ReferenceField(
        placeholder=placeholder,
        label=label,
        validation_type="numeric_range",
        minimum=_d(minimum),
        maximum=_d(maximum),
        min_inclusive=min_inclusive,
        max_inclusive=max_inclusive,
        unit=unit,
        visible_limit=visible_limit,
    )


def _positive(placeholder: str, label: str, unit: str = "") -> ReferenceField:
    return ReferenceField(
        placeholder=placeholder,
        label=label,
        validation_type="positive_decimal",
        minimum=None,
        maximum=None,
        min_inclusive=False,
        max_inclusive=False,
        unit=unit,
        visible_limit="Decimal positivo obligatorio",
    )


def _exact(placeholder: str, label: str, options: tuple[str, ...], visible_limit: str) -> ReferenceField:
    return ReferenceField(
        placeholder=placeholder,
        label=label,
        validation_type="exact_option",
        minimum=None,
        maximum=None,
        min_inclusive=False,
        max_inclusive=False,
        unit="",
        visible_limit=visible_limit,
        options=options,
    )


def _negative_or_less_than(placeholder: str, label: str, maximum: str, unit: str) -> ReferenceField:
    return ReferenceField(
        placeholder=placeholder,
        label=label,
        validation_type="negative_or_less_than",
        minimum=_d("0"),
        maximum=_d(maximum),
        min_inclusive=True,
        max_inclusive=False,
        unit=unit,
        visible_limit=f"Negativo o < {maximum}",
        options=("Negativo",),
    )


def _count_field(placeholder: str, label: str, validation_type: str, visible_limit: str) -> ReferenceField:
    return ReferenceField(
        placeholder=placeholder,
        label=label,
        validation_type=validation_type,
        minimum=None,
        maximum=None,
        min_inclusive=False,
        max_inclusive=False,
        unit="",
        visible_limit=visible_limit,
        options=("Ausentes",),
    )


REFERENCE_FIELDS: dict[str, ReferenceField] = {
    f.placeholder: f
    for f in (
        _range("gluco", "Glucosa", "55", "99", "mg/dL", "55 - 99 mg/dL"),
        _range("urea", "Urea", "16.60", "48.50", "mg/dL", "16.60 - 48.50 mg/dL"),
        _range("bun", "BUN", "6.0", "20", "mg/dL", "6.0 - 20 mg/dL"),
        _range("creat", "Creatinina", "0.70", "1.2", "mg/dL", "0.70 - 1.2 mg/dL"),
        _range("acido", "Acido urico", "3.4", "7.0", "mg/dL", "3.4 - 7.0 mg/dL"),
        _range("col_t", "Colesterol total", "0", "200", "mg/dL", "< 200 mg/dL", max_inclusive=False),
        _range("col_hd", "Colesterol HDL", "40", "60", "mg/dL", "40 - 60 mg/dL"),
        _range("trigli", "Trigliceridos", "0", "150", "mg/dL", "< 150 mg/dL", max_inclusive=False),
        _range("col_ld", "Colesterol LDL", "0", "100", "mg/dL", "< 100 mg/dL", max_inclusive=False),
        _range("col_nh", "Colesterol no HDL", "0", "130", "mg/dL", "< 130 mg/dL", max_inclusive=False),
        _range("ind_at", "Indice aterogenico", "0", "4.5", "", "< 4.5", max_inclusive=False),
        _range("prot_t", "Proteinas totales", "6.3", "8.1", "g/dL", "6.3 - 8.1 g/dL"),
        _range("albu", "Albumina", "3.9", "5.1", "g/dL", "3.9 - 5.1 g/dL"),
        _range("globu", "Globulina", "2.9", "3.1", "g/dL", "2.9 - 3.1 g/dL"),
        _range("relac", "Relacion A/G", "1.18", "2.33", "", "1.18 - 2.33"),
        _range("bili_t", "Bilirrubina total", "0", "1.2", "mg/dL", "<1.2 mg/dL", max_inclusive=False),
        _range("bili_d", "Bilirrubina directa", "0.09", "0.3", "mg/dL", "0.09 - 0.3 mg/dL"),
        _range("bili_i", "Bilirrubina indirecta", "0.01", "0.9", "mg/dL", "0.01 - 0.9 mg/dL"),
        _range("asttgo", "AST/TGO", "0", "40", "U/L", "< 40 U/L", max_inclusive=False),
        _range("alttgp", "ALT/TGP", "0", "41", "U/L", "< 41 U/L", max_inclusive=False),
        _range("fosact", "Fosfatasa alcalina", "40", "130", "U/L", "40 - 130 U/L"),
        _range("gamagt", "Gama GT", "9", "75", "U/L", "9 - 75 U/L"),
        _range("ldh", "LDH", "135", "225", "U/L", "135 - 225 U/L"),
        _range("hierro", "Hierro", "33", "193", "µg/dL", "33 - 193 µg/dL"),
        _range("calcio", "Calcio", "8.6", "10", "mg/dL", "8.6 - 10 mg/dL"),
        _range("sodio", "Sodio", "136", "145", "meq/L", "136 - 145 meq/L"),
        _range("potas", "Potasio", "3.5", "5.1", "meq/L", "3.5 - 5.1 meq/L"),
        _range("leuco", "Leucocitos", "3.8", "11.6", "miles/µL", "3.8-11.6 miles/µL"),
        _range("eritro", "Eritrocitos", "4.70", "5.80", "millones/µL", "4.70-5.80 millones/µL"),
        _range("hemog", "Hemoglobina", "14.0", "18.0", "g/dL", "14.0-18.0 g/dL"),
        _range("hemat", "Hematocrito", "40.0", "54.0", "%", "40.0-54.0 %"),
        _range("vcrm", "V.C.M.", "78.0", "99.0", "fL", "78.0-99.0 fL"),
        _range("hemcm", "H.C.M.", "27.0", "31.0", "pg", "27.0-31.0 pg"),
        _range("comhc", "C.M.H.C.", "32.0", "36.0", "g/dL (%)", "32.0-36.0 g/dL (%)"),
        _range("rdw_cv", "RDW-CV", "11.5", "17.0", "%", "11.5 - 17.0 %"),
        _range("rdw_sd", "RDW-SD", "39", "57", "fL", "39 - 57 fL"),
        _range("plaqut", "Plaquetas", "150", "500", "miles/µL", "150-500 miles/µL"),
        _range("volpm", "Volumen plaquetario medio", "9.6", "13.4", "fL", "9.6 - 13.4 fL"),
        _range("neutpc", "Neutrofilos %", "38.4", "74.6", "%", "38.4-74.6 %"),
        _range("linfpc", "Linfocitos %", "16.5", "49.6", "%", "16.5-49.6 %"),
        _range("monopc", "Monocitos %", "4.6", "12.7", "%", "4.6-12.7 %"),
        _range("eosipc", "Eosinofilos %", "1.0", "4.0", "%", "1.0-4.0 %"),
        _range("basopc", "Basofilos %", "0.0", "1.0", "%", "0.0-1.0 %"),
        _range("neut_a", "Neutrofilos absolutos", "1.69", "7.16", "miles/µL", "1.69-7.16 miles/µL"),
        _range("linf_a", "Linfocitos absolutos", "1.05", "3.53", "miles/µL", "1.05-3.53 miles/µL"),
        _range("mono_a", "Monocitos absolutos", "0.25", "0.90", "miles/µL", "0.25-0.90 miles/µL"),
        _range("eosi_a", "Eosinofilos absolutos", "0.02", "0.50", "miles/µL", "0.02-0.50 miles/µL"),
        _range("baso_a", "Basofilos absolutos", "0.01", "0.10", "miles/µL", "0.01-0.10 miles/µL"),
        _exact("gsanth", "Grupo sanguineo y Rh", BLOOD_GROUP_OPTIONS, "Ocho opciones autorizadas"),
        _range("t_pro", "Tiempo de protrombina", "10.4", "13.0", "seg", "10.4 - 13.0 seg"),
        _range("pct_ac", "Porcentaje de actividad", "70", "120", "%", "70-120 %"),
        _range("t_tpa", "Tiempo TTPA", "25.4", "44.7", "seg", "25.4 - 44.7 seg"),
        _range("wsterg", "Westergren", "1", "15", "mm/h", "1-15 mm/h"),
        _range("vitad", "Vitamina D", "30", "100", "ng/mL", "30 - 100 ng/mL"),
        _exact("o_col", "Color", ("Amarillo", "Transparente"), "Amarillo o Transparente"),
        _exact("o_asp", "Aspecto", ("Claro",), "Claro"),
        _range("o_dens", "Densidad", "1.005", "1.030", "", "1.005 - 1.030"),
        _range("o_ph", "pH", "4.8", "7.4", "", "4.8 - 7.4"),
        _negative_or_less_than("o_el", "Esterasa leucocitaria", "10", ""),
        _exact("o_nit", "Nitritos", ("Negativo",), "Negativo"),
        _negative_or_less_than("o_pro", "Proteinas", "10", ""),
        _negative_or_less_than("o_glu", "Glucosa en orina", "30", ""),
        _negative_or_less_than("o_cet", "Cetonas", "5", ""),
        _negative_or_less_than("o_bili", "Bilirrubina en orina", "0.2", ""),
        _negative_or_less_than("o_uro", "Urobilinogeno", "1", ""),
        _negative_or_less_than("o_hemo", "Hemoglobina en orina", "5", ""),
        _count_field("o_leu", "Leucocitos por campo", "leukocyte_count", "Ausentes, 1-5 o rango 1-5"),
        _count_field("o_eri", "Eritrocitos por campo", "erythrocyte_count", "Ausentes, 1, 2 o 1-2"),
        _exact("o_erid", "Eritrocitos dismorficos", ("Ausentes",), "Ausentes"),
        _exact("o_cili", "Cilindros", ("Ausentes",), "Ausentes"),
        _exact("o_cri", "Cristales", ("Ausentes",), "Ausentes"),
        _exact("o_cpa", "Celulas pavimentosas", ("Ausentes", "Escasas"), "Ausentes - Escasas"),
        _exact("o_dtra", "Detritus", ("Ausentes", "Escasas"), "Ausentes - Escasas"),
        _exact("o_ctr", "Celulas transicionales", ("Ausentes", "Escasas"), "Ausentes - Escasas"),
        _exact("o_rmuc", "Resto mucoso", ("Ausentes", "Escasas"), "Ausentes - Escasas"),
        _exact("o_bact", "Bacterias", ("Ausentes",), "Ausentes"),
        _exact("o_leva", "Levaduras", ("Ausentes",), "Ausentes"),
    )
}

CHEMISTRY_FIELDS: tuple[str, ...] = (
    "gluco",
    "urea",
    "bun",
    "creat",
    "acido",
    "col_t",
    "col_hd",
    "trigli",
    "col_ld",
    "col_nh",
    "ind_at",
    "prot_t",
    "albu",
    "globu",
    "relac",
    "bili_t",
    "bili_d",
    "bili_i",
    "asttgo",
    "alttgp",
    "fosact",
    "gamagt",
    "ldh",
    "hierro",
    "calcio",
    "sodio",
    "potas",
)

HEMATOLOGY_FIELDS: tuple[str, ...] = (
    "leuco",
    "eritro",
    "hemog",
    "hemat",
    "vcrm",
    "hemcm",
    "comhc",
    "rdw_cv",
    "rdw_sd",
    "plaqut",
    "volpm",
    "neutpc",
    "linfpc",
    "monopc",
    "eosipc",
    "basopc",
    "neut_a",
    "linf_a",
    "mono_a",
    "eosi_a",
    "baso_a",
)

COAGULATION_FIELDS: tuple[str, ...] = (
    "gsanth",
    "t_pro",
    "pct_ac",
    "t_tpa",
    "wsterg",
    "vitad",
)

URINE_FIELDS: tuple[str, ...] = (
    "o_col",
    "o_asp",
    "o_dens",
    "o_ph",
    "o_el",
    "o_nit",
    "o_pro",
    "o_glu",
    "o_cet",
    "o_bili",
    "o_uro",
    "o_hemo",
    "o_leu",
    "o_eri",
    "o_erid",
    "o_cili",
    "o_cri",
    "o_cpa",
    "o_dtra",
    "o_ctr",
    "o_rmuc",
    "o_bact",
    "o_leva",
)

CLINICAL_PLACEHOLDER_NAMES: tuple[str, ...] = (
    CHEMISTRY_FIELDS + HEMATOLOGY_FIELDS + COAGULATION_FIELDS + URINE_FIELDS
)

MANUAL_CLINICAL_PLACEHOLDER_NAMES: tuple[str, ...] = ("gsanth",)
GENERATED_CLINICAL_PLACEHOLDER_NAMES: tuple[str, ...] = tuple(
    name for name in CLINICAL_PLACEHOLDER_NAMES if name not in MANUAL_CLINICAL_PLACEHOLDER_NAMES
)


def clinical_form_sections() -> tuple[dict[str, Any], ...]:
    return (
        {
            "title": "Grupo sanguineo, coagulacion y complementarios",
            "description": "Solo el grupo sanguineo y Rh se captura manualmente.",
            "fields": _fields(MANUAL_CLINICAL_PLACEHOLDER_NAMES),
        },
    )


def _fields(names: tuple[str, ...]) -> tuple[ReferenceField, ...]:
    return tuple(REFERENCE_FIELDS[name] for name in names)


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _decimal(value: Any, label: str) -> tuple[Decimal | None, str | None]:
    s = _norm(value).replace(",", ".")
    if not s:
        return None, f"{label} es obligatorio."
    try:
        n = Decimal(s)
    except InvalidOperation:
        return None, f"{label} debe ser decimal."
    if not n.is_finite():
        return None, f"{label} debe ser decimal."
    return n, None


def _range_error(field: ReferenceField) -> str:
    return f"{field.label}: valor fuera de rango. Referencia permitida: {field.visible_limit}."


def validate_field_value(field: ReferenceField, value: Any) -> str | None:
    s = _norm(value)
    if field.validation_type == "exact_option":
        if s not in field.options:
            return f"{field.label}: valor no permitido. Referencia permitida: {field.visible_limit}."
        return None

    if field.validation_type == "positive_decimal":
        n, err = _decimal(value, field.label)
        if err:
            return err
        if n is None or n <= 0:
            return f"{field.label} debe ser mayor a cero."
        return None

    if field.validation_type == "negative_or_less_than":
        if s == "Negativo":
            return None
        n, err = _decimal(value, field.label)
        if err:
            return err
        assert n is not None and field.minimum is not None and field.maximum is not None
        if n < field.minimum or n >= field.maximum:
            return _range_error(field)
        return None

    if field.validation_type == "leukocyte_count":
        if s == "Ausentes":
            return None
        m = re.fullmatch(r"([1-5])(?:-([1-5]))?", s)
        if not m:
            return f"{field.label}: valor no permitido. Referencia permitida: {field.visible_limit}."
        first = int(m.group(1))
        second = int(m.group(2) or first)
        if first > second:
            return f"{field.label}: valor no permitido. Referencia permitida: {field.visible_limit}."
        return None

    if field.validation_type == "erythrocyte_count":
        if s in {"Ausentes", "1", "2", "1-2"}:
            return None
        return f"{field.label}: valor no permitido. Referencia permitida: {field.visible_limit}."

    if field.validation_type == "numeric_range":
        n, err = _decimal(value, field.label)
        if err:
            return err
        assert n is not None and field.minimum is not None and field.maximum is not None
        below = n < field.minimum if field.min_inclusive else n <= field.minimum
        above = n > field.maximum if field.max_inclusive else n >= field.maximum
        if below or above:
            return _range_error(field)
        return None

    return f"{field.label}: tipo de validacion no soportado."


def validate_unified_clinical_results(
    data: dict[str, Any],
    names: tuple[str, ...] = CLINICAL_PLACEHOLDER_NAMES,
) -> list[str]:
    errors: list[str] = []
    for name in names:
        err = validate_field_value(REFERENCE_FIELDS[name], data.get(name))
        if err:
            errors.append(err)
    return errors


def validate_manual_clinical_results(data: dict[str, Any]) -> list[str]:
    return validate_unified_clinical_results(data, MANUAL_CLINICAL_PLACEHOLDER_NAMES)


def validate_generated_clinical_results(data: dict[str, Any]) -> list[str]:
    return validate_unified_clinical_results(data, GENERATED_CLINICAL_PLACEHOLDER_NAMES)

