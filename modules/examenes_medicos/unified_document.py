from __future__ import annotations

import re
from collections import Counter
from io import BytesIO
from typing import Any
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from modules.examenes_medicos.export_helpers import (
    fecha_iso_a_dd_mm_yyyy,
    build_paciente_orina,
)
from modules.examenes_medicos.reference_ranges import (
    ADMIN_PLACEHOLDER_NAMES,
    CLINICAL_PLACEHOLDER_NAMES,
    EXPECTED_UNIFIED_PLACEHOLDERS,
)
from modules.examenes_medicos.validation import _norm
from modules.finiquitos.docx_placeholders import replace_placeholders_in_docx_bytes


PLACEHOLDER_RE = re.compile(r"\{\{[^{}]+\}\}")
_WORD_TEXT = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"


class UnifiedTemplateError(RuntimeError):
    pass


def extract_docx_placeholders(docx_bytes: bytes) -> list[str]:
    placeholders: list[str] = []
    with ZipFile(BytesIO(docx_bytes), "r") as zf:
        for name in zf.namelist():
            if not (name.startswith("word/") and name.endswith(".xml")):
                continue
            try:
                root = ET.fromstring(zf.read(name))
            except ET.ParseError:
                continue
            text = "".join(t.text or "" for t in root.iter(_WORD_TEXT))
            placeholders.extend(PLACEHOLDER_RE.findall(text))
    return placeholders


def assert_template_placeholders_match(docx_bytes: bytes, mapping: dict[str, str] | None = None) -> None:
    placeholders = extract_docx_placeholders(docx_bytes)
    counter = Counter(placeholders)
    duplicates = sorted(k for k, count in counter.items() if count > 1)
    if duplicates:
        raise UnifiedTemplateError("La plantilla unificada contiene placeholders duplicados: " + ", ".join(duplicates))

    docx_set = set(counter)
    expected_set = set(EXPECTED_UNIFIED_PLACEHOLDERS)
    if docx_set != expected_set:
        missing = sorted(expected_set - docx_set)
        extra = sorted(docx_set - expected_set)
        msg = ["Los placeholders de la plantilla unificada no coinciden con el contrato esperado."]
        if missing:
            msg.append("Faltan: " + ", ".join(missing))
        if extra:
            msg.append("Sobran: " + ", ".join(extra))
        raise UnifiedTemplateError(" ".join(msg))

    if mapping is not None and set(mapping) != docx_set:
        missing = sorted(docx_set - set(mapping))
        extra = sorted(set(mapping) - docx_set)
        msg = ["El mapping unificado no coincide con los placeholders de la plantilla."]
        if missing:
            msg.append("Faltan claves: " + ", ".join(missing))
        if extra:
            msg.append("Sobran claves: " + ", ".join(extra))
        raise UnifiedTemplateError(" ".join(msg))


def _mapping_val(data: dict[str, Any], key: str, default: str = "") -> str:
    value = data.get(key)
    if value is None:
        return default
    return str(value).strip()


def build_unified_mapping(data: dict[str, Any]) -> dict[str, str]:
    nombres = _mapping_val(data, "nombres")
    apellidos = _mapping_val(data, "apellidos")
    fnac = _mapping_val(data, "fecha_nacimiento")
    fecha_registro = _mapping_val(data, "fecha_estudio") or _mapping_val(data, "fecha_registro")
    mapping: dict[str, str] = {
        "{{folio}}": _mapping_val(data, "folio"),
        "{{orden}}": _mapping_val(data, "orden"),
        "{{paciente_id}}": _mapping_val(data, "paciente_id"),
        "{{paciente_nombre}}": build_paciente_orina(nombres, apellidos).upper(),
        "{{sexo}}": _mapping_val(data, "sexo"),
        "{{fecha_nacimiento}}": fecha_iso_a_dd_mm_yyyy(fnac) if fnac else "",
        "{{edad}}": _mapping_val(data, "edad"),
        "{{fecha_registro}}": fecha_iso_a_dd_mm_yyyy(fecha_registro) if fecha_registro else "",
    }
    for name in CLINICAL_PLACEHOLDER_NAMES:
        mapping[f"{{{{{name}}}}}"] = _mapping_val(data, name)

    expected_names = set(ADMIN_PLACEHOLDER_NAMES + CLINICAL_PLACEHOLDER_NAMES)
    if set(EXPECTED_UNIFIED_PLACEHOLDERS) != set(mapping):
        missing = sorted(set(EXPECTED_UNIFIED_PLACEHOLDERS) - set(mapping))
        extra = sorted(set(mapping) - set(EXPECTED_UNIFIED_PLACEHOLDERS))
        raise UnifiedTemplateError(
            "El mapping unificado no tiene exactamente las claves esperadas. "
            f"Faltan: {missing}. Sobran: {extra}. Campos: {len(expected_names)}."
        )
    return mapping


def render_unified_docx_bytes(template_bytes: bytes, mapping: dict[str, str]) -> bytes:
    assert_template_placeholders_match(template_bytes, mapping)
    rendered = replace_placeholders_in_docx_bytes(template_bytes, mapping)
    if not rendered:
        raise UnifiedTemplateError("El DOCX generado esta vacio.")
    remaining = extract_docx_placeholders(rendered)
    if remaining:
        raise UnifiedTemplateError("Quedaron placeholders sin reemplazar: " + ", ".join(sorted(set(remaining))))
    if _docx_text_contains(rendered, "{{") or _docx_text_contains(rendered, "}}"):
        raise UnifiedTemplateError("Quedaron secuencias de placeholder en el DOCX generado.")
    return rendered


def _docx_text_contains(docx_bytes: bytes, token: str) -> bool:
    with ZipFile(BytesIO(docx_bytes), "r") as zf:
        for name in zf.namelist():
            if not (name.startswith("word/") and name.endswith(".xml")):
                continue
            try:
                root = ET.fromstring(zf.read(name))
            except ET.ParseError:
                continue
            text = "".join(t.text or "" for t in root.iter(_WORD_TEXT))
            if token in text:
                return True
    return False

