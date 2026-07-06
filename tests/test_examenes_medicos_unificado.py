from __future__ import annotations

import hashlib
import inspect
import sqlite3
import tempfile
import unittest
import xml.etree.ElementTree as ET
from io import BytesIO
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from flask import Blueprint, Flask, g

from modules.examenes_medicos.blueprint import api_master_download, register_examenes_medicos
from modules.examenes_medicos.clinical_autogen import generate_clinical_bundle
from modules.examenes_medicos.identifiers import (
    build_patient_display_name,
    build_unified_filename_base,
    compact_order,
    generate_unique_folio_unificado,
    generate_unique_orden_unificada,
    get_or_create_paciente_id,
    migrate_examenes_medicos_identifier_tables,
    validate_orden_unificada,
)
from modules.examenes_medicos.paths import UNIFICADO_DOCX
from modules.examenes_medicos.reference_ranges import (
    BLOOD_GROUP_OPTIONS,
    CLINICAL_PLACEHOLDER_NAMES,
    EXPECTED_UNIFIED_PLACEHOLDERS,
    GENERATED_CLINICAL_PLACEHOLDER_NAMES,
    REFERENCE_FIELDS,
    validate_generated_clinical_results,
    validate_field_value,
)
from modules.examenes_medicos.unified_document import (
    build_unified_mapping,
    extract_docx_placeholders,
    generate_unified_medical_document,
    read_validated_unified_template,
    render_unified_docx_bytes,
)
from modules.examenes_medicos.validation import format_registration_datetime


UNIFIED_TEMPLATE_SHA256 = "deb1420e2002546f3458ecfb4c02ab34581e2d68df97d097cdbdff997994f41e"


def _valid_payload() -> dict[str, str | bool]:
    payload: dict[str, str | bool] = {
        "nombres": "Juan",
        "apellido_paterno": "Perez",
        "apellido_materno": "Lopez",
        "fecha_nacimiento": "1990-01-15",
        "sexo": "Masculino",
        "fecha_registro": "2026-07-01",
        "hora_registro": "08:00:00",
        "gsanth": "A Positivo",
        "scope": "unificado",
        "format": "pdf",
        "confirmar_generacion": True,
    }
    return payload


def _valid_generated_values() -> dict[str, str]:
    generated = generate_clinical_bundle(sexo="Masculino", seed=12345)["unificado"]
    assert validate_generated_clinical_results(generated) == []
    return generated


def _valid_mapping_payload() -> dict[str, str | bool]:
    payload = _valid_payload()
    payload.update(_valid_generated_values())
    payload.update(
        {
            "folio": "34126431",
            "orden": "B20002724",
            "paciente_id": "12345678",
            "edad": "36",
        }
    )
    return payload


def _docx_text(docx_bytes: bytes) -> str:
    with ZipFile(BytesIO(docx_bytes), "r") as zf:
        parts = []
        for name in zf.namelist():
            if name.startswith("word/") and name.endswith(".xml"):
                parts.append(zf.read(name).decode("utf-8", errors="ignore"))
    return "\n".join(parts)


def _run_font_sizes_for_text(docx_bytes: bytes, text: str) -> list[str]:
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    sizes: list[str] = []
    with ZipFile(BytesIO(docx_bytes), "r") as zf:
        for name in zf.namelist():
            if not (name.startswith("word/") and name.endswith(".xml")):
                continue
            root = ET.fromstring(zf.read(name))
            for run in root.iter(w + "r"):
                run_text = "".join(t.text or "" for t in run.iter(w + "t"))
                if run_text == text:
                    rpr = run.find(w + "rPr")
                    sz = rpr.find(w + "sz") if rpr is not None else None
                    if sz is not None:
                        sizes.append(str(sz.attrib.get(w + "val") or ""))
    return sizes


def _zip_part(docx_bytes: bytes, name: str) -> bytes:
    with ZipFile(BytesIO(docx_bytes), "r") as zf:
        return zf.read(name)


def _word_xml_names(docx_bytes: bytes, prefix: str) -> list[str]:
    with ZipFile(BytesIO(docx_bytes), "r") as zf:
        return sorted(name for name in zf.namelist() if name.startswith(prefix) and name.endswith(".xml"))


def _xml_text(docx_bytes: bytes, name: str) -> str:
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    root = ET.fromstring(_zip_part(docx_bytes, name))
    return "".join(t.text or "" for t in root.iter(w + "t"))


def _border_signature(docx_bytes: bytes):
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    root = ET.fromstring(_zip_part(docx_bytes, "word/document.xml"))
    out = []
    for tbl_i, table in enumerate(root.iter(w + "tbl")):
        tbl_pr = table.find(w + "tblPr")
        tbl_borders = tbl_pr.find(w + "tblBorders") if tbl_pr is not None else None
        table_edges = []
        if tbl_borders is not None:
            table_edges = [
                (edge.tag, tuple(sorted(edge.attrib.items())))
                for edge in tbl_borders
                if edge.tag.startswith(w)
            ]
        cell_edges = []
        for cell_i, cell in enumerate(table.iter(w + "tc")):
            tc_pr = cell.find(w + "tcPr")
            tc_borders = tc_pr.find(w + "tcBorders") if tc_pr is not None else None
            if tc_borders is None:
                continue
            for edge in tc_borders:
                if edge.tag.startswith(w):
                    cell_edges.append((cell_i, edge.tag, tuple(sorted(edge.attrib.items()))))
        if table_edges or cell_edges:
            out.append((tbl_i, tuple(table_edges), tuple(cell_edges)))
    return tuple(out)


def _paragraph_style_signature(docx_bytes: bytes, needles: tuple[str, ...]):
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    root = ET.fromstring(_zip_part(docx_bytes, "word/document.xml"))
    out = []
    for idx, paragraph in enumerate(root.iter(w + "p")):
        text = "".join(t.text or "" for t in paragraph.iter(w + "t"))
        if not any(needle in text for needle in needles):
            continue
        p_pr = paragraph.find(w + "pPr")
        runs = []
        for run in paragraph.iter(w + "r"):
            run_text = "".join(t.text or "" for t in run.iter(w + "t"))
            if not run_text:
                continue
            r_pr = run.find(w + "rPr")
            runs.append(
                (
                    run_text,
                    ET.tostring(r_pr, encoding="unicode") if r_pr is not None else "",
                )
            )
        out.append(
            (
                idx,
                text,
                ET.tostring(p_pr, encoding="unicode") if p_pr is not None else "",
                tuple(runs),
            )
        )
    return tuple(out)


def _app(tmp: Path, *, role: str = "admin") -> Flask:
    repo = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(repo / "templates"))
    app.config.update(
        TESTING=True,
        SECRET_KEY="test",
        DATABASE=str(tmp / "test.db"),
        GENERATED_DIR=str(tmp / "generated"),
    )
    conn = sqlite3.connect(app.config["DATABASE"])
    try:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL, role TEXT NOT NULL)")
        conn.execute("INSERT INTO users (id, username, role) VALUES (1, 'admin', 'admin')")
        conn.commit()
    finally:
        conn.close()
    vitroflex_bp = Blueprint(
        "vitroflex",
        __name__,
        static_folder=str(repo / "static" / "vitroflex"),
        static_url_path="/vf-test-static",
    )

    @vitroflex_bp.route("/memo")
    def memo_mensual() -> str:
        return ""

    app.register_blueprint(vitroflex_bp)
    register_examenes_medicos(app)

    @app.before_request
    def _user() -> None:
        g.user = {"id": 1, "role": role, "username": "admin"}

    return app


class TestUnifiedTemplateContract(unittest.TestCase):
    def test_template_sha_and_placeholders(self):
        raw = UNIFICADO_DOCX.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), UNIFIED_TEMPLATE_SHA256)
        placeholders = extract_docx_placeholders(raw)
        self.assertEqual(len(placeholders), len(set(placeholders)))
        self.assertEqual(len(set(placeholders)), 85)
        self.assertEqual(set(placeholders), set(EXPECTED_UNIFIED_PLACEHOLDERS))
        self.assertNotIn("{{inr}}", placeholders)
        self.assertNotIn("{{tiempo_testigo}}", placeholders)
        self.assertNotIn("{{isi}}", placeholders)

    def test_mapping_replaces_all_placeholders(self):
        payload = _valid_mapping_payload()
        mapping = build_unified_mapping(payload)
        self.assertEqual(len(mapping), 85)
        self.assertEqual(set(mapping), set(EXPECTED_UNIFIED_PLACEHOLDERS))
        rendered = render_unified_docx_bytes(UNIFICADO_DOCX.read_bytes(), mapping)
        self.assertGreater(len(rendered), 0)
        self.assertEqual(extract_docx_placeholders(rendered), [])

    def test_public_generator_uses_active_template_and_mapping(self):
        payload = _valid_mapping_payload()
        document = generate_unified_medical_document(payload)
        _raw, sha = read_validated_unified_template()
        self.assertEqual(document.template_path, UNIFICADO_DOCX.resolve())
        self.assertEqual(document.template_sha256, sha)
        self.assertEqual(document.mapping, build_unified_mapping(payload))
        self.assertEqual(extract_docx_placeholders(document.docx_bytes), [])

    def test_render_preserves_template_ooxml_structure_outside_placeholders(self):
        raw = UNIFICADO_DOCX.read_bytes()
        payload = _valid_mapping_payload()
        payload["o_col"] = "Transparente"
        rendered = render_unified_docx_bytes(raw, build_unified_mapping(payload))

        self.assertEqual(_border_signature(rendered), _border_signature(raw))
        self.assertEqual(
            _paragraph_style_signature(rendered, ("Resultados", "Análisis Clínicos")),
            _paragraph_style_signature(raw, ("Resultados", "Análisis Clínicos")),
        )
        for name in _word_xml_names(raw, "word/footer"):
            self.assertEqual(_zip_part(rendered, name), _zip_part(raw, name), name)
            footer_text = _xml_text(rendered, name).lower()
            self.assertNotIn("cl-001", footer_text)
            self.assertNotIn("acredit", footer_text)
        self.assertEqual(_run_font_sizes_for_text(rendered, "Transparente"), ["17"])

    def test_alignment_spaces_are_presentation_only_for_affected_results(self):
        payload = _valid_mapping_payload()
        payload.update({"basopc": "0.7", "baso_a": "0.03", "o_leva": "Ausentes"})
        mapping = build_unified_mapping(payload)
        self.assertEqual(mapping["{{basopc}}"], "0.7")
        self.assertEqual(mapping["{{baso_a}}"], "0.03")
        self.assertEqual(mapping["{{o_leva}}"], "Ausentes")
        rendered = render_unified_docx_bytes(UNIFICADO_DOCX.read_bytes(), mapping)
        document_text = _xml_text(rendered, "word/document.xml")
        self.assertIn(" 0.7", document_text)
        self.assertIn(" 0.03", document_text)
        self.assertIn(" Ausentes", document_text)

    def test_mapping_uses_canonical_patient_name_and_registration_datetime(self):
        payload = _valid_mapping_payload()
        payload.update(
            {
                "nombres": "Yahir Ramon",
                "apellido_paterno": "Ramirez",
                "apellido_materno": "Mata",
                "fecha_registro": "2026-06-30",
                "hora_registro": "08:47:20",
            }
        )
        mapping = build_unified_mapping(payload)
        self.assertEqual(mapping["{{paciente_nombre}}"], "RAMIREZ MATA YAHIR RAMON")
        self.assertEqual(mapping["{{fecha_registro}}"], "30/06/2026  08:47:20a. m.")

    def test_sexo_mapping_uses_masculino_femenino_and_legacy_aliases(self):
        payload = _valid_mapping_payload()
        payload["sexo"] = "Masculino"
        self.assertEqual(build_unified_mapping(payload)["{{sexo}}"], "Masculino")
        payload["sexo"] = "Femenino"
        self.assertEqual(build_unified_mapping(payload)["{{sexo}}"], "Femenino")
        payload["sexo"] = "Hombre"
        self.assertEqual(build_unified_mapping(payload)["{{sexo}}"], "Masculino")
        payload["sexo"] = "Mujer"
        self.assertEqual(build_unified_mapping(payload)["{{sexo}}"], "Femenino")

    def test_productive_download_does_not_reference_clinical_autogen(self):
        source = inspect.getsource(api_master_download)
        self.assertIn("generate_clinical_bundle", source)
        self.assertIn("generate_unified_medical_document", source)
        self.assertNotIn("render_unified_docx_bytes", source)
        self.assertNotIn("bundle_clinico", source)
        self.assertNotIn("generate_unique_folio_orina", source)
        self.assertNotIn("generate_unique_folio_sangre", source)
        document_source = inspect.getsource(render_unified_docx_bytes)
        self.assertNotIn("shrink_transparente", document_source)
        self.assertNotIn("w:sz", document_source)


class TestUnifiedFormUI(unittest.TestCase):
    def test_form_is_trimmed_to_admin_and_blood_group(self):
        with tempfile.TemporaryDirectory() as td:
            app = _app(Path(td))
            with app.test_client() as client:
                res = client.get("/vitroflex/examenes-medicos/")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn('name="nombres"', html)
        self.assertIn('name="apellido_paterno"', html)
        self.assertIn('name="apellido_materno"', html)
        self.assertIn("Nombre o nombres", html)
        self.assertIn("Apellido paterno", html)
        self.assertIn("Apellido materno", html)
        self.assertIn('name="fecha_registro"', html)
        self.assertIn('name="hora_registro"', html)
        self.assertIn("Fecha de Registro", html)
        self.assertIn("Hora de Registro", html)
        self.assertNotIn('name="apellidos"', html)
        self.assertNotIn('name="fecha_toma"', html)
        self.assertNotIn('name="fecha_val"', html)
        self.assertNotIn('name="hora_val"', html)
        self.assertNotIn("Fecha de toma", html)
        self.assertNotIn("Fecha de validación", html)
        self.assertNotIn("Hora de validación", html)
        self.assertIn('name="gsanth"', html)
        self.assertIn("Selecciona una opción", html)
        self.assertIn('value="Masculino"', html)
        self.assertIn('value="Femenino"', html)
        self.assertNotIn(">Hombre<", html)
        self.assertNotIn(">Mujer<", html)
        self.assertNotIn(">Otro<", html)
        self.assertNotIn('name="gluco"', html)
        self.assertNotIn('name="hemog"', html)
        self.assertNotIn('name="o_dens"', html)
        self.assertNotIn('name="dirigido_a"', html)
        self.assertNotIn("Tiempo Testigo", html)
        self.assertNotIn("Solo orina", html)
        self.assertNotIn("Solo sangre", html)
        self.assertNotIn(">Ambos<", html)

    def test_delete_ui_has_context_and_loading_guard(self):
        repo = Path(__file__).resolve().parents[1]
        html = (repo / "templates" / "examenes_medicos" / "examenes_medicos_historial.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("data-patient", html)
        self.assertIn("data-order", html)
        self.assertIn("data-em-delete-btn", html)
        self.assertIn('method: "DELETE"', html)
        self.assertIn('btn.dataset.loading === "1"', html)
        self.assertIn("btn.disabled = true", html)


class TestUnifiedReferenceRanges(unittest.TestCase):
    def test_generate_clinical_bundle_unified_values_are_valid(self):
        generated = generate_clinical_bundle(sexo="Masculino", seed=321)["unificado"]
        self.assertEqual(set(generated), set(GENERATED_CLINICAL_PLACEHOLDER_NAMES))
        self.assertEqual(validate_generated_clinical_results(generated), [])
        self.assertIn(generated["o_col"], {"Amarillo", "Transparente"})

    def test_inclusive_boundaries(self):
        for field in REFERENCE_FIELDS.values():
            if field.validation_type != "numeric_range" or not field.max_inclusive:
                continue
            assert field.minimum is not None and field.maximum is not None
            self.assertIsNone(validate_field_value(field, str(field.minimum)), field.placeholder)
            self.assertIsNone(validate_field_value(field, str(field.maximum)), field.placeholder)
            self.assertIsNotNone(validate_field_value(field, str(field.minimum - Decimal("0.01"))), field.placeholder)
            self.assertIsNotNone(validate_field_value(field, str(field.maximum + Decimal("0.01"))), field.placeholder)

    def test_exclusive_max_boundaries(self):
        for field in REFERENCE_FIELDS.values():
            if field.validation_type != "numeric_range" or field.max_inclusive:
                continue
            assert field.maximum is not None
            self.assertIsNone(validate_field_value(field, "0"), field.placeholder)
            self.assertIsNotNone(validate_field_value(field, str(field.maximum)), field.placeholder)
            self.assertIsNotNone(validate_field_value(field, str(field.maximum + Decimal("0.01"))), field.placeholder)
            self.assertIsNotNone(validate_field_value(field, "-0.01"), field.placeholder)

    def test_urine_qualitative_and_negative_or_less_than(self):
        for name in ("o_col", "o_asp", "o_nit", "o_erid", "o_cili", "o_cri", "o_bact", "o_leva"):
            field = REFERENCE_FIELDS[name]
            self.assertIsNone(validate_field_value(field, field.options[0]), name)
            self.assertIsNotNone(validate_field_value(field, "Abundantes"), name)

        for name in ("o_cpa", "o_dtra", "o_ctr", "o_rmuc"):
            field = REFERENCE_FIELDS[name]
            self.assertIsNone(validate_field_value(field, "Ausentes"), name)
            self.assertIsNone(validate_field_value(field, "Escasas"), name)
            self.assertIsNotNone(validate_field_value(field, "Negativo"), name)

        for name in ("o_el", "o_pro", "o_glu", "o_cet", "o_bili", "o_uro", "o_hemo"):
            field = REFERENCE_FIELDS[name]
            assert field.maximum is not None
            self.assertIsNone(validate_field_value(field, "Negativo"), name)
            self.assertIsNone(validate_field_value(field, "0"), name)
            self.assertIsNone(validate_field_value(field, str(field.maximum - Decimal("0.01"))), name)
            self.assertIsNotNone(validate_field_value(field, str(field.maximum)), name)
            self.assertIsNotNone(validate_field_value(field, str(field.maximum + Decimal("0.01"))), name)

    def test_urine_counts_and_no_text_number_conversion(self):
        leu = REFERENCE_FIELDS["o_leu"]
        for value in ("Ausentes", "1", "3", "5", "1-2", "2-4", "4-5"):
            self.assertIsNone(validate_field_value(leu, value))
        for value in ("0", "6", "1-6", "5-2", "Abundantes"):
            self.assertIsNotNone(validate_field_value(leu, value))

        eri = REFERENCE_FIELDS["o_eri"]
        for value in ("Ausentes", "1", "2", "1-2"):
            self.assertIsNone(validate_field_value(eri, value))
        for value in ("0", "3", "2-3", "Negativo"):
            self.assertIsNotNone(validate_field_value(eri, value))

        payload = _valid_mapping_payload()
        payload["o_el"] = "0"
        self.assertEqual(build_unified_mapping(payload)["{{o_el}}"], "0")
        payload["o_el"] = "Negativo"
        self.assertEqual(build_unified_mapping(payload)["{{o_el}}"], "Negativo")

    def test_fixed_template_values_are_not_captured_or_mapped(self):
        for name in ("inr", "tiempo_testigo", "isi"):
            self.assertNotIn(name, REFERENCE_FIELDS)
            self.assertNotIn(name, CLINICAL_PLACEHOLDER_NAMES)
            self.assertNotIn(f"{{{{{name}}}}}", EXPECTED_UNIFIED_PLACEHOLDERS)
        gsanth = REFERENCE_FIELDS["gsanth"]
        for value in BLOOD_GROUP_OPTIONS:
            self.assertIsNone(validate_field_value(gsanth, value))
        self.assertIsNotNone(validate_field_value(gsanth, "A+"))

        payload = _valid_mapping_payload()
        mapping = build_unified_mapping(payload)
        self.assertNotIn("{{inr}}", mapping)
        self.assertNotIn("{{tiempo_testigo}}", mapping)
        self.assertNotIn("{{isi}}", mapping)


class TestUnifiedIdentifiers(unittest.TestCase):
    def test_patient_display_name_components(self):
        self.assertEqual(
            build_patient_display_name("Yahir Ramon", "Ramirez", "Mata"),
            "RAMIREZ MATA YAHIR RAMON",
        )
        self.assertEqual(
            build_patient_display_name("  Yahir   Ramon  ", "  Ramirez  ", "  Mata  "),
            "RAMIREZ MATA YAHIR RAMON",
        )
        self.assertEqual(
            build_patient_display_name("Íñigo  Ángel", "Muñoz", "Pérez"),
            "MUÑOZ PÉREZ ÍÑIGO ÁNGEL",
        )
        self.assertEqual(
            build_patient_display_name("Yahir Ramon", "Ramirez", ""),
            "RAMIREZ YAHIR RAMON",
        )

    def test_registration_datetime_format(self):
        self.assertEqual(
            format_registration_datetime("2026-06-30", "08:47:20"),
            "30/06/2026  08:47:20a. m.",
        )
        self.assertEqual(
            format_registration_datetime("2026-06-30", "15:15"),
            "30/06/2026  03:15:00p. m.",
        )
        self.assertEqual(
            format_registration_datetime("2026-06-30", "00:05"),
            "30/06/2026  12:05:00a. m.",
        )
        self.assertEqual(
            format_registration_datetime("2026-06-30", "12:00"),
            "30/06/2026  12:00:00p. m.",
        )
        for fecha, hora in (
            ("2026-02-30", "08:00"),
            ("", "08:00"),
            ("2026-06-30", "25:00"),
            ("2026-06-30", ""),
        ):
            with self.subTest(fecha=fecha, hora=hora):
                with self.assertRaises(ValueError):
                    format_registration_datetime(fecha, hora)

    def test_order_pattern_and_filename(self):
        self.assertTrue(validate_orden_unificada("B20002724"))
        self.assertEqual(compact_order("B20002724"), "B22724")
        self.assertEqual(compact_order("B20002004"), "B22004")
        self.assertEqual(
            build_unified_filename_base("B20002724", "34126431", "2026-07-01"),
            "B22724-34126431-010726",
        )

    def test_patient_id_reuse_and_different_birthdate(self):
        conn = sqlite3.connect(":memory:")
        try:
            migrate_examenes_medicos_identifier_tables(conn)
            a = get_or_create_paciente_id(
                conn,
                nombres=" José  ",
                apellido_paterno="Pérez",
                apellido_materno="",
                fecha_nacimiento="1990-01-01",
            )
            b = get_or_create_paciente_id(
                conn,
                nombres="jose",
                apellido_paterno=" PEREZ ",
                apellido_materno="",
                fecha_nacimiento="1990-01-01",
            )
            c = get_or_create_paciente_id(
                conn,
                nombres="Jose",
                apellido_paterno="Perez",
                apellido_materno="",
                fecha_nacimiento="1991-01-01",
            )
        finally:
            conn.close()
        self.assertRegex(a, r"^\d{8}$")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_order_and_folio_collisions_retry(self):
        conn = sqlite3.connect(":memory:")
        try:
            migrate_examenes_medicos_identifier_tables(conn)
            conn.execute("INSERT INTO examenes_medicos_ordenes_usadas (orden) VALUES ('B20002724')")
            conn.execute("INSERT INTO examenes_medicos_folios_unificados_usados (folio) VALUES ('00001234')")
            with patch(
                "modules.examenes_medicos.identifiers.generate_order_candidate",
                side_effect=["B20002724", "B10000001"],
            ):
                self.assertEqual(generate_unique_orden_unificada(conn), "B10000001")
            with patch(
                "modules.examenes_medicos.identifiers.generate_folio_candidate",
                side_effect=["00001234", "00000005"],
            ):
                self.assertEqual(generate_unique_folio_unificado(conn), "00000005")
        finally:
            conn.close()


class TestUnifiedGenerationEndpoint(unittest.TestCase):
    def test_no_confirmation_does_not_generate(self):
        with tempfile.TemporaryDirectory() as td:
            app = _app(Path(td))
            payload = _valid_payload()
            payload.pop("confirmar_generacion")
            with app.test_client() as client:
                with patch("modules.examenes_medicos.blueprint.docx_bytes_to_pdf_bytes") as pdf_mock:
                    res = client.post("/vitroflex/examenes-medicos/api/master/download", json=payload)
            self.assertEqual(res.status_code, 400)
            pdf_mock.assert_not_called()
            self.assertFalse((Path(td) / "generated").exists())

    def test_out_of_range_does_not_create_files_or_history(self):
        with tempfile.TemporaryDirectory() as td:
            app = _app(Path(td))
            payload = _valid_payload()
            generated = _valid_generated_values()
            generated["gluco"] = "54.99"
            with app.test_client() as client:
                with patch("modules.examenes_medicos.blueprint.generate_clinical_bundle", return_value={"unificado": generated}):
                    with patch("modules.examenes_medicos.blueprint.docx_bytes_to_pdf_bytes") as pdf_mock:
                        res = client.post("/vitroflex/examenes-medicos/api/master/download", json=payload)
            self.assertEqual(res.status_code, 500)
            pdf_mock.assert_not_called()
            self.assertFalse((Path(td) / "generated").exists())
            conn = sqlite3.connect(app.config["DATABASE"])
            try:
                count = conn.execute("SELECT COUNT(*) FROM examenes_medicos_expediente").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(count, 0)

    def test_invalid_generated_bundle_blocks_without_history(self):
        cases = [
            ("missing", lambda d: d.pop("gluco")),
            ("empty", lambda d: d.__setitem__("gluco", "")),
            ("invalid_qualitative", lambda d: d.__setitem__("o_col", "Azul")),
        ]
        for _label, mutate in cases:
            with self.subTest(_label):
                with tempfile.TemporaryDirectory() as td:
                    app = _app(Path(td))
                    payload = _valid_payload()
                    generated = _valid_generated_values()
                    mutate(generated)
                    with app.test_client() as client:
                        with patch("modules.examenes_medicos.blueprint.generate_clinical_bundle", return_value={"unificado": generated}):
                            with patch("modules.examenes_medicos.blueprint.docx_bytes_to_pdf_bytes") as pdf_mock:
                                res = client.post("/vitroflex/examenes-medicos/api/master/download", json=payload)
                    self.assertEqual(res.status_code, 500)
                    pdf_mock.assert_not_called()
                    self.assertFalse((Path(td) / "generated").exists())
                    conn = sqlite3.connect(app.config["DATABASE"])
                    try:
                        count = conn.execute("SELECT COUNT(*) FROM examenes_medicos_expediente").fetchone()[0]
                    finally:
                        conn.close()
                    self.assertEqual(count, 0)

    def test_manual_blood_group_invalid_blocks_generation(self):
        with tempfile.TemporaryDirectory() as td:
            app = _app(Path(td))
            payload = _valid_payload()
            payload["gsanth"] = "A+"
            with app.test_client() as client:
                with patch("modules.examenes_medicos.blueprint.generate_clinical_bundle") as gen_mock:
                    res = client.post("/vitroflex/examenes-medicos/api/master/download", json=payload)
            self.assertEqual(res.status_code, 400)
            gen_mock.assert_not_called()

    def test_new_capture_rejects_legacy_or_invalid_sexo(self):
        for sexo in ("Hombre", "Mujer", "Otro"):
            with self.subTest(sexo):
                with tempfile.TemporaryDirectory() as td:
                    app = _app(Path(td))
                    payload = _valid_payload()
                    payload["sexo"] = sexo
                    with app.test_client() as client:
                        with patch("modules.examenes_medicos.blueprint.generate_clinical_bundle") as gen_mock:
                            res = client.post("/vitroflex/examenes-medicos/api/master/download", json=payload)
                    self.assertEqual(res.status_code, 400)
                    self.assertIn("Sexo no válido.", res.get_json()["errors"])
                    gen_mock.assert_not_called()

    def test_registration_datetime_errors_block_generation_and_history(self):
        cases = [
            ("missing_date", "fecha_registro", "", "Captura la Fecha de Registro."),
            ("missing_time", "hora_registro", "", "Captura la Hora de Registro."),
            ("invalid_date", "fecha_registro", "2026-02-30", "La Fecha de Registro no es válida."),
            ("invalid_time", "hora_registro", "25:00", "La Hora de Registro no es válida."),
        ]
        for label, key, value, expected in cases:
            with self.subTest(label):
                with tempfile.TemporaryDirectory() as td:
                    app = _app(Path(td))
                    payload = _valid_payload()
                    payload[key] = value
                    with app.test_client() as client:
                        with patch("modules.examenes_medicos.blueprint.generate_clinical_bundle") as gen_mock:
                            with patch("modules.examenes_medicos.blueprint.docx_bytes_to_pdf_bytes") as pdf_mock:
                                res = client.post("/vitroflex/examenes-medicos/api/master/download", json=payload)
                    self.assertEqual(res.status_code, 400)
                    self.assertIn(expected, " ".join(res.get_json()["errors"]))
                    gen_mock.assert_not_called()
                    pdf_mock.assert_not_called()
                    conn = sqlite3.connect(app.config["DATABASE"])
                    try:
                        count = conn.execute("SELECT COUNT(*) FROM examenes_medicos_expediente").fetchone()[0]
                    finally:
                        conn.close()
                    self.assertEqual(count, 0)

    def test_valid_payload_generates_single_document_and_history(self):
        with tempfile.TemporaryDirectory() as td:
            app = _app(Path(td))
            payload = _valid_payload()
            with app.test_client() as client:
                with patch(
                    "modules.examenes_medicos.blueprint.docx_bytes_to_pdf_bytes",
                    return_value=b"%PDF-1.4\n%test\n",
                ):
                    with patch("modules.examenes_medicos.blueprint.log_app_activity"):
                        res = client.post("/vitroflex/examenes-medicos/api/master/download", json=payload)
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.mimetype, "application/pdf")
            gen_files = [p.name for p in (Path(td) / "generated").glob("examenes_medicos/*/*")]
            self.assertEqual(len(gen_files), 2)
            self.assertTrue(all(name.endswith((".docx", ".pdf")) for name in gen_files))
            self.assertEqual({Path(name).stem for name in gen_files}, {Path(gen_files[0]).stem})
            conn = sqlite3.connect(app.config["DATABASE"])
            try:
                row = conn.execute(
                    """
                    SELECT last_scope, sangre_pdf_relpath, orina_pdf_relpath, paciente_id, orden, folio, filename_base
                    FROM examenes_medicos_expediente
                    """
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row[0], "unificado")
            self.assertTrue(row[1])
            self.assertIsNone(row[2])
            self.assertRegex(row[3], r"^\d{8}$")
            self.assertTrue(validate_orden_unificada(row[4]))
            self.assertRegex(row[5], r"^\d{8}$")
            self.assertEqual(row[6], build_unified_filename_base(row[4], row[5], "1990-01-15"))
            self.assertEqual(res.headers["Content-Disposition"], f'attachment; filename="{row[6]}.pdf"')

    def test_generated_values_appear_in_docx(self):
        with tempfile.TemporaryDirectory() as td:
            app = _app(Path(td))
            payload = _valid_payload()
            payload["format"] = "docx"
            generated = _valid_generated_values()
            generated.update({"gluco": "87", "hemog": "15.7", "o_dens": "1.021", "o_col": "Transparente"})
            with app.test_client() as client:
                with patch("modules.examenes_medicos.blueprint.generate_clinical_bundle", return_value={"unificado": generated}):
                    with patch("modules.examenes_medicos.blueprint.log_app_activity"):
                        res = client.post("/vitroflex/examenes-medicos/api/master/download", json=payload)
            self.assertEqual(res.status_code, 200)
            text = _docx_text(res.data)
            self.assertIn("87", text)
            self.assertIn("15.7", text)
            self.assertIn("1.021", text)
            self.assertIn("PEREZ LOPEZ JUAN", text)
            self.assertIn("01/07/2026  08:00:00a. m.", text)
            self.assertIn("Transparente", text)
            self.assertEqual(_run_font_sizes_for_text(res.data, "Transparente"), ["17"])
            self.assertEqual(extract_docx_placeholders(res.data), [])

    def test_word_then_pdf_same_expediente_reuses_identifiers_and_name(self):
        with tempfile.TemporaryDirectory() as td:
            app = _app(Path(td))
            payload = _valid_payload()
            payload["format"] = "docx"
            with app.test_client() as client:
                with patch("modules.examenes_medicos.blueprint.docx_bytes_to_pdf_bytes") as pdf_mock:
                    with patch("modules.examenes_medicos.blueprint.log_app_activity"):
                        res_docx = client.post("/vitroflex/examenes-medicos/api/master/download", json=payload)
                self.assertEqual(res_docx.status_code, 200)
                pdf_mock.assert_not_called()
                expediente_id = res_docx.headers["X-Examenes-Expediente-Id"]

                payload["format"] = "pdf"
                payload["expediente_id"] = expediente_id
                with patch(
                    "modules.examenes_medicos.blueprint.docx_bytes_to_pdf_bytes",
                    return_value=b"%PDF-1.4\n%test\n",
                ):
                    with patch("modules.examenes_medicos.blueprint.log_app_activity"):
                        res_pdf = client.post("/vitroflex/examenes-medicos/api/master/download", json=payload)
            self.assertEqual(res_pdf.status_code, 200)
            self.assertEqual(res_docx.headers["Content-Disposition"].replace(".docx", ""), res_pdf.headers["Content-Disposition"].replace(".pdf", ""))
            conn = sqlite3.connect(app.config["DATABASE"])
            try:
                rows = conn.execute(
                    "SELECT paciente_id, orden, folio, filename_base, sangre_docx_relpath, sangre_pdf_relpath FROM examenes_medicos_expediente"
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0][4])
            self.assertTrue(rows[0][5])

    def test_same_patient_new_exam_gets_new_order_and_folio(self):
        with tempfile.TemporaryDirectory() as td:
            app = _app(Path(td))
            payload = _valid_payload()
            payload["format"] = "docx"
            with app.test_client() as client:
                with patch("modules.examenes_medicos.blueprint.log_app_activity"):
                    first = client.post("/vitroflex/examenes-medicos/api/master/download", json=payload)
                    second = client.post("/vitroflex/examenes-medicos/api/master/download", json=payload)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 200)
            conn = sqlite3.connect(app.config["DATABASE"])
            try:
                rows = conn.execute(
                    "SELECT paciente_id, orden, folio FROM examenes_medicos_expediente ORDER BY id"
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][0], rows[1][0])
            self.assertNotEqual(rows[0][1], rows[1][1])
            self.assertNotEqual(rows[0][2], rows[1][2])

    def test_delete_requires_admin_permission(self):
        with tempfile.TemporaryDirectory() as td:
            app = _app(Path(td), role="operador")
            payload = _valid_payload()
            with app.test_client() as client:
                with patch(
                    "modules.examenes_medicos.blueprint.docx_bytes_to_pdf_bytes",
                    return_value=b"%PDF-1.4\n%test\n",
                ):
                    with patch("modules.examenes_medicos.blueprint.log_app_activity"):
                        created = client.post("/vitroflex/examenes-medicos/api/master/download", json=payload)
                self.assertEqual(created.status_code, 200)
                expediente_id = created.headers["X-Examenes-Expediente-Id"]
                res = client.delete(f"/vitroflex/examenes-medicos/api/historial/{expediente_id}")
            self.assertEqual(res.status_code, 403)

    def test_delete_missing_id_returns_404(self):
        with tempfile.TemporaryDirectory() as td:
            app = _app(Path(td))
            with app.test_client() as client:
                res = client.delete("/vitroflex/examenes-medicos/api/historial/999")
            self.assertEqual(res.status_code, 404)

    def test_delete_removes_only_selected_expediente_files_and_keeps_patient_and_template(self):
        with tempfile.TemporaryDirectory() as td:
            app = _app(Path(td))
            payload = _valid_payload()
            with app.test_client() as client:
                with patch(
                    "modules.examenes_medicos.blueprint.docx_bytes_to_pdf_bytes",
                    return_value=b"%PDF-1.4\n%test\n",
                ):
                    with patch("modules.examenes_medicos.blueprint.log_app_activity"):
                        first = client.post("/vitroflex/examenes-medicos/api/master/download", json=payload)
                        second = client.post("/vitroflex/examenes-medicos/api/master/download", json=payload)
                self.assertEqual(first.status_code, 200)
                self.assertEqual(second.status_code, 200)
                first_id = first.headers["X-Examenes-Expediente-Id"]
                second_id = second.headers["X-Examenes-Expediente-Id"]
                gen_dir = Path(app.config["GENERATED_DIR"])
                template_sha = hashlib.sha256(UNIFICADO_DOCX.read_bytes()).hexdigest()
                conn = sqlite3.connect(app.config["DATABASE"])
                try:
                    first_paths = conn.execute(
                        "SELECT sangre_docx_relpath, sangre_pdf_relpath FROM examenes_medicos_expediente WHERE id = ?",
                        (first_id,),
                    ).fetchone()
                    second_paths = conn.execute(
                        "SELECT sangre_docx_relpath, sangre_pdf_relpath FROM examenes_medicos_expediente WHERE id = ?",
                        (second_id,),
                    ).fetchone()
                finally:
                    conn.close()
                first_files = [gen_dir / first_paths[0], gen_dir / first_paths[1]]
                second_files = [gen_dir / second_paths[0], gen_dir / second_paths[1]]
                self.assertTrue(all(path.is_file() for path in first_files + second_files))

                res = client.delete(f"/vitroflex/examenes-medicos/api/historial/{first_id}")

            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.get_json()["deleted_files"], 2)
            self.assertFalse(any(path.exists() for path in first_files))
            self.assertTrue(all(path.is_file() for path in second_files))
            self.assertEqual(hashlib.sha256(UNIFICADO_DOCX.read_bytes()).hexdigest(), template_sha)
            conn = sqlite3.connect(app.config["DATABASE"])
            try:
                rows = conn.execute("SELECT id FROM examenes_medicos_expediente ORDER BY id").fetchall()
                patient_ids = conn.execute("SELECT COUNT(*) FROM examenes_medicos_paciente_ids").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual([int(row[0]) for row in rows], [int(second_id)])
            self.assertEqual(patient_ids, 1)

    def test_delete_allows_missing_generated_file_but_deletes_record(self):
        with tempfile.TemporaryDirectory() as td:
            app = _app(Path(td))
            payload = _valid_payload()
            with app.test_client() as client:
                with patch(
                    "modules.examenes_medicos.blueprint.docx_bytes_to_pdf_bytes",
                    return_value=b"%PDF-1.4\n%test\n",
                ):
                    with patch("modules.examenes_medicos.blueprint.log_app_activity"):
                        created = client.post("/vitroflex/examenes-medicos/api/master/download", json=payload)
                self.assertEqual(created.status_code, 200)
                expediente_id = created.headers["X-Examenes-Expediente-Id"]
                conn = sqlite3.connect(app.config["DATABASE"])
                try:
                    rels = conn.execute(
                        "SELECT sangre_docx_relpath, sangre_pdf_relpath FROM examenes_medicos_expediente WHERE id = ?",
                        (expediente_id,),
                    ).fetchone()
                finally:
                    conn.close()
                missing = Path(app.config["GENERATED_DIR"]) / rels[1]
                missing.unlink()
                res = client.delete(f"/vitroflex/examenes-medicos/api/historial/{expediente_id}")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.get_json()["missing_files"], 1)
            conn = sqlite3.connect(app.config["DATABASE"])
            try:
                count = conn.execute("SELECT COUNT(*) FROM examenes_medicos_expediente").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()

