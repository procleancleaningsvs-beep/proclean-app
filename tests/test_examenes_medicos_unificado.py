from __future__ import annotations

import hashlib
import inspect
import sqlite3
import tempfile
import unittest
from io import BytesIO
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from flask import Blueprint, Flask, g

from modules.examenes_medicos.blueprint import api_master_download, register_examenes_medicos
from modules.examenes_medicos.clinical_autogen import generate_clinical_bundle
from modules.examenes_medicos.identifiers import (
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
    render_unified_docx_bytes,
)


UNIFIED_TEMPLATE_SHA256 = "e99736784fc898923da759e7894bff850779b83d980ccdbd1af8ec334c12d27b"


def _valid_payload() -> dict[str, str | bool]:
    payload: dict[str, str | bool] = {
        "nombres": "Juan",
        "apellidos": "Perez Lopez",
        "fecha_nacimiento": "1990-01-15",
        "sexo": "Hombre",
        "fecha_estudio": "2026-07-01",
        "fecha_toma": "2026-07-01",
        "hora_toma": "08:00:00",
        "fecha_val": "2026-07-01",
        "hora_val": "12:00:00",
        "gsanth": "A Positivo",
        "scope": "unificado",
        "format": "pdf",
        "confirmar_generacion": True,
    }
    return payload


def _valid_generated_values() -> dict[str, str]:
    generated = generate_clinical_bundle(sexo="Hombre", seed=12345)["unificado"]
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


def _app(tmp: Path) -> Flask:
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
        g.user = {"id": 1, "role": "admin", "username": "admin"}

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

    def test_productive_download_does_not_reference_clinical_autogen(self):
        source = inspect.getsource(api_master_download)
        self.assertIn("generate_clinical_bundle", source)
        self.assertNotIn("bundle_clinico", source)
        self.assertNotIn("generate_unique_folio_orina", source)
        self.assertNotIn("generate_unique_folio_sangre", source)


class TestUnifiedFormUI(unittest.TestCase):
    def test_form_is_trimmed_to_admin_and_blood_group(self):
        with tempfile.TemporaryDirectory() as td:
            app = _app(Path(td))
            with app.test_client() as client:
                res = client.get("/vitroflex/examenes-medicos/")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn('name="gsanth"', html)
        self.assertIn("Selecciona una opción", html)
        self.assertNotIn('name="gluco"', html)
        self.assertNotIn('name="hemog"', html)
        self.assertNotIn('name="o_dens"', html)
        self.assertNotIn('name="dirigido_a"', html)
        self.assertNotIn("Tiempo Testigo", html)
        self.assertNotIn("Solo orina", html)
        self.assertNotIn("Solo sangre", html)
        self.assertNotIn(">Ambos<", html)


class TestUnifiedReferenceRanges(unittest.TestCase):
    def test_generate_clinical_bundle_unified_values_are_valid(self):
        generated = generate_clinical_bundle(sexo="Hombre", seed=321)["unificado"]
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
                apellidos="Pérez",
                fecha_nacimiento="1990-01-01",
            )
            b = get_or_create_paciente_id(
                conn,
                nombres="jose",
                apellidos="PEREZ",
                fecha_nacimiento="1990-01-01",
            )
            c = get_or_create_paciente_id(
                conn,
                nombres="Jose",
                apellidos="Perez",
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
            generated.update({"gluco": "87", "hemog": "15.7", "o_dens": "1.021"})
            with app.test_client() as client:
                with patch("modules.examenes_medicos.blueprint.generate_clinical_bundle", return_value={"unificado": generated}):
                    with patch("modules.examenes_medicos.blueprint.log_app_activity"):
                        res = client.post("/vitroflex/examenes-medicos/api/master/download", json=payload)
            self.assertEqual(res.status_code, 200)
            text = _docx_text(res.data)
            self.assertIn("87", text)
            self.assertIn("15.7", text)
            self.assertIn("1.021", text)
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


if __name__ == "__main__":
    unittest.main()

