from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
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
from modules.examenes_medicos.paths import UNIFICADO_DOCX, UNIFICADO_DOCX_SHA256
from modules.examenes_medicos.validation import _norm, format_registration_datetime, normalize_sexo_display
from modules.finiquitos.docx_placeholders import replace_placeholders_in_docx_bytes


PLACEHOLDER_RE = re.compile(r"\{\{[^{}]+\}\}")
_WORD_TEXT = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
_WORD_RUN = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}r"
_WORD_RPR = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}rPr"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_ALIGNMENT_SPACE_PLACEHOLDERS = ("{{basopc}}", "{{baso_a}}", "{{o_leva}}")


@dataclass(frozen=True)
class UnifiedMedicalDocument:
    template_path: Path
    template_sha256: str
    mapping: dict[str, str]
    docx_bytes: bytes


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
        "{{sexo}}": normalize_sexo_display(_mapping_val(data, "sexo")),
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


def generate_unified_medical_document(
    data: dict[str, Any],
    *,
    template_path: Path = UNIFICADO_DOCX,
    expected_sha256: str = UNIFICADO_DOCX_SHA256,
) -> UnifiedMedicalDocument:
    template_bytes, template_sha = read_validated_unified_template(
        template_path,
        expected_sha256=expected_sha256,
    )
    mapping = build_unified_mapping(data)
    docx_bytes = render_unified_docx_bytes(template_bytes, mapping)
    return UnifiedMedicalDocument(
        template_path=template_path.resolve(),
        template_sha256=template_sha,
        mapping=mapping,
        docx_bytes=docx_bytes,
    )


def read_validated_unified_template(
    template_path: Path = UNIFICADO_DOCX,
    *,
    expected_sha256: str = UNIFICADO_DOCX_SHA256,
) -> tuple[bytes, str]:
    path = template_path.resolve()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if sha.lower() != expected_sha256.lower():
        raise UnifiedTemplateError(
            "La plantilla unificada activa no coincide con el SHA-256 esperado. "
            f"Esperado: {expected_sha256}. Actual: {sha}. Ruta: {path}."
        )
    return raw, sha


def render_unified_docx_bytes(template_bytes: bytes, mapping: dict[str, str]) -> bytes:
    assert_template_placeholders_match(template_bytes, mapping)
    template_copy = _add_alignment_spaces_before_placeholders(template_bytes)
    rendered = replace_placeholders_in_docx_bytes(template_copy, mapping)
    rendered = _restore_word_xml_parts_without_placeholders(template_bytes, rendered, mapping)
    if not rendered:
        raise UnifiedTemplateError("El DOCX generado esta vacio.")
    remaining = extract_docx_placeholders(rendered)
    if remaining:
        raise UnifiedTemplateError("Quedaron placeholders sin reemplazar: " + ", ".join(sorted(set(remaining))))
    if _docx_text_contains(rendered, "{{") or _docx_text_contains(rendered, "}}"):
        raise UnifiedTemplateError("Quedaron secuencias de placeholder en el DOCX generado.")
    return rendered


def _add_alignment_spaces_before_placeholders(template_bytes: bytes) -> bytes:
    out = BytesIO()
    with ZipFile(BytesIO(template_bytes), "r") as zin:
        with ZipFile(out, "w", ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == "word/document.xml":
                    root = ET.fromstring(data)
                    if _add_alignment_spaces_to_document(root):
                        data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                zout.writestr(info, data)
    return out.getvalue()


def _add_alignment_spaces_to_document(root: ET.Element) -> bool:
    changed = False
    for paragraph in root.iter(_w("p")):
        text_nodes = list(paragraph.iter(_WORD_TEXT))
        full = "".join(t.text or "" for t in text_nodes)
        if full not in _ALIGNMENT_SPACE_PLACEHOLDERS:
            continue
        first_text = next((t for t in paragraph.iter(_WORD_TEXT) if t.text), None)
        if first_text is None:
            continue
        first_run = _ancestor(paragraph, first_text, _WORD_RUN)
        if first_run is None:
            continue
        space_run = ET.Element(_WORD_RUN)
        rpr = first_run.find(_WORD_RPR)
        if rpr is not None:
            space_run.append(ET.fromstring(ET.tostring(rpr, encoding="utf-8")))
        text = ET.SubElement(space_run, _WORD_TEXT)
        text.set(_XML_SPACE, "preserve")
        text.text = " "
        paragraph.insert(list(paragraph).index(first_run), space_run)
        changed = True
    return changed


def _ancestor(root: ET.Element, child: ET.Element, tag: str) -> ET.Element | None:
    parent = {c: p for p in root.iter() for c in p}
    current = child
    while current in parent:
        current = parent[current]
        if current.tag == tag:
            return current
    return None


def _w(name: str) -> str:
    return f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}{name}"


def _restore_word_xml_parts_without_placeholders(
    template_bytes: bytes,
    rendered_bytes: bytes,
    mapping: dict[str, str],
) -> bytes:
    """Preserva byte a byte las partes XML de Word que no contienen placeholders."""
    out = BytesIO()
    with ZipFile(BytesIO(template_bytes), "r") as ztemplate:
        restore_names = {
            name
            for name in ztemplate.namelist()
            if name.startswith("word/")
            and name.endswith(".xml")
            and not _part_contains_any_placeholder(ztemplate.read(name), mapping)
        }
        original = {name: ztemplate.read(name) for name in restore_names}
        original_info = {name: ztemplate.getinfo(name) for name in restore_names}

    with ZipFile(BytesIO(rendered_bytes), "r") as zin:
        with ZipFile(out, "w", ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                if info.filename in original:
                    zout.writestr(original_info[info.filename], original[info.filename])
                else:
                    zout.writestr(info, zin.read(info.filename))
    return out.getvalue()


def _part_contains_any_placeholder(part_bytes: bytes, mapping: dict[str, str]) -> bool:
    try:
        root = ET.fromstring(part_bytes)
    except ET.ParseError:
        return False
    text = "".join(t.text or "" for t in root.iter(_WORD_TEXT))
    return any(key in text for key in mapping)


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

