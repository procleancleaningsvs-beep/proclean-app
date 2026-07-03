from __future__ import annotations

import re
from collections import Counter
from io import BytesIO
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile
import xml.etree.ElementTree as ET

from modules.examenes_medicos.export_helpers import (
    fecha_iso_a_dd_mm_yyyy,
)
from modules.examenes_medicos.identifiers import (
    build_patient_display_name,
    split_legacy_apellidos,
)
from modules.examenes_medicos.reference_ranges import (
    ADMIN_PLACEHOLDER_NAMES,
    CLINICAL_PLACEHOLDER_NAMES,
    EXPECTED_UNIFIED_PLACEHOLDERS,
)
from modules.examenes_medicos.validation import _norm, format_registration_datetime
from modules.finiquitos.docx_placeholders import replace_placeholders_in_docx_bytes


PLACEHOLDER_RE = re.compile(r"\{\{[^{}]+\}\}")
_WORD_TEXT = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
_WORD_RUN = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}r"
_WORD_RPR = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}rPr"
_WORD_SZ = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}sz"
_WORD_SZ_CS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}szCs"
_WORD_VAL = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val"


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
    apellido_paterno = _mapping_val(data, "apellido_paterno")
    apellido_materno = _mapping_val(data, "apellido_materno")
    if not apellido_paterno and not apellido_materno:
        apellido_paterno, apellido_materno = split_legacy_apellidos(_mapping_val(data, "apellidos"))
    fnac = _mapping_val(data, "fecha_nacimiento")
    fecha_registro = _mapping_val(data, "fecha_registro")
    hora_registro = _mapping_val(data, "hora_registro")
    mapping: dict[str, str] = {
        "{{folio}}": _mapping_val(data, "folio"),
        "{{orden}}": _mapping_val(data, "orden"),
        "{{paciente_id}}": _mapping_val(data, "paciente_id"),
        "{{paciente_nombre}}": build_patient_display_name(nombres, apellido_paterno, apellido_materno),
        "{{sexo}}": _mapping_val(data, "sexo"),
        "{{fecha_nacimiento}}": fecha_iso_a_dd_mm_yyyy(fnac) if fnac else "",
        "{{edad}}": _mapping_val(data, "edad"),
        "{{fecha_registro}}": format_registration_datetime(fecha_registro, hora_registro),
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
    rendered = shrink_transparente_runs(rendered)
    if not rendered:
        raise UnifiedTemplateError("El DOCX generado esta vacio.")
    remaining = extract_docx_placeholders(rendered)
    if remaining:
        raise UnifiedTemplateError("Quedaron placeholders sin reemplazar: " + ", ".join(sorted(set(remaining))))
    if _docx_text_contains(rendered, "{{") or _docx_text_contains(rendered, "}}"):
        raise UnifiedTemplateError("Quedaron secuencias de placeholder en el DOCX generado.")
    return rendered


def shrink_transparente_runs(docx_bytes: bytes, half_points: int = 12) -> bytes:
    """Reduce solo el run generado con texto exacto 'Transparente' en la copia DOCX."""
    out = BytesIO()
    with ZipFile(BytesIO(docx_bytes), "r") as zin:
        with ZipFile(out, "w", ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename.startswith("word/") and info.filename.endswith(".xml"):
                    try:
                        root = ET.fromstring(data)
                    except ET.ParseError:
                        zout.writestr(info, data)
                        continue
                    changed = False
                    for run in root.iter(_WORD_RUN):
                        text = "".join(t.text or "" for t in run.iter(_WORD_TEXT))
                        if text == "Transparente":
                            _set_run_font_size(run, half_points)
                            changed = True
                    if changed:
                        data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                zout.writestr(info, data)
    return out.getvalue()


def _set_run_font_size(run: ET.Element, half_points: int) -> None:
    rpr = run.find(_WORD_RPR)
    if rpr is None:
        rpr = ET.Element(_WORD_RPR)
        run.insert(0, rpr)
    for tag in (_WORD_SZ, _WORD_SZ_CS):
        node = rpr.find(tag)
        if node is None:
            node = ET.SubElement(rpr, tag)
        node.set(_WORD_VAL, str(max(12, int(half_points))))


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

