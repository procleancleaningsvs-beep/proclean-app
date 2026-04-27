from __future__ import annotations

import unittest
from datetime import date

from modules.examenes_medicos.clinical_autogen import generate_clinical_bundle
from modules.examenes_medicos.export_helpers import (
    build_orina_data_for_mapping,
    build_orina_mapping,
    build_paciente_sangre,
    build_sangre_data_for_mapping,
    build_sangre_mapping,
    default_hora_val_sugerida,
    fecha_iso_a_dd_mm_yyyy,
    sexo_para_orina,
)
from modules.examenes_medicos.validation import (
    classify_imc,
    edad_desde_fecha_nacimiento,
    validate_codigo_barra,
    validate_folio_orina,
    validate_folio_sangre,
)


class TestExamenesMedicosValidation(unittest.TestCase):
    def test_folio_orina(self):
        self.assertIsNone(validate_folio_orina("123456"))
        self.assertIsNotNone(validate_folio_orina("12345"))

    def test_folio_sangre(self):
        self.assertIsNone(validate_folio_sangre("123456789012"))
        self.assertIsNotNone(validate_folio_sangre("123"))

    def test_codigo_barra(self):
        self.assertIsNone(validate_codigo_barra("ANC8349230417"))
        self.assertIsNotNone(validate_codigo_barra("AN8349230417"))

    def test_edad_fnac(self):
        self.assertEqual(edad_desde_fecha_nacimiento(date(1993, 3, 7), date(2025, 3, 6)), 31)

    def test_clasificacion_imc(self):
        self.assertEqual(classify_imc(17.0), "Bajo peso")
        self.assertEqual(classify_imc(22.0), "Normal")
        self.assertEqual(classify_imc(27.0), "Sobrepeso")
        self.assertEqual(classify_imc(31.0), "Obesidad")

    def test_paciente_sangre_format(self):
        self.assertEqual(
            build_paciente_sangre("Denisse Aracely", "Hernández Martínez"),
            "HERNÁNDEZ MARTÍNEZ DENISSE ARACELY",
        )

    def test_sexo_para_orina(self):
        self.assertEqual(sexo_para_orina("Mujer"), "Femenino")
        self.assertEqual(sexo_para_orina("Hombre"), "Masculino")

    def test_fecha_iso_ddmmyyyy(self):
        self.assertEqual(fecha_iso_a_dd_mm_yyyy("1993-03-07"), "07/03/1993")

    def test_default_hora_val(self):
        self.assertEqual(default_hora_val_sugerida("08:30:00"), "12:30:00")

    def test_build_orina_mapping_fecha(self):
        m = build_orina_mapping(
            {
                "nombres": "Juan",
                "apellidos": "Pérez",
                "edad": "30",
                "sexo": "Masculino",
                "folio": "123456",
                "fecha_estudio": "2026-04-19",
                "aspecto": "Límpido",
                "color": "Amarillo",
                "densidad": "1.010",
                "ph_orina": "6",
                "eritrocitos": "0",
                "leucocitos": "0",
            }
        )
        self.assertEqual(m["{fecha_estudio}"], "19 DE ABRIL DEL 2026")
        self.assertEqual(m["{paciente_nombre_completo}"], "JUAN PÉREZ")
        self.assertEqual(m["{eritrocitos}"], "0/C")
        self.assertEqual(m["{leucocitos}"], "0/C")

    def test_master_orina_merge(self):
        bundle = generate_clinical_bundle(sexo="Hombre", seed=42)
        master = {
            "nombres": "Juan",
            "apellidos": "Pérez",
            "edad": "40",
            "sexo": "Hombre",
            "folio_orina": "123456",
            "fecha_estudio": "2026-04-19",
        }
        odata = build_orina_data_for_mapping(master, bundle["orina"])
        self.assertEqual(odata["sexo"], "Masculino")
        m = build_orina_mapping(odata)
        self.assertIn("{aspecto}", m)
        self.assertEqual(m["{folio}"], "123456")

    def test_clinical_bundle_deterministic(self):
        a = generate_clinical_bundle(sexo="Mujer", seed=99)
        b = generate_clinical_bundle(sexo="Mujer", seed=99)
        self.assertEqual(a["orina"]["aspecto"], b["orina"]["aspecto"])
        self.assertEqual(a["sangre"]["glucosa"], b["sangre"]["glucosa"])

    def test_sangre_master_merge(self):
        bundle = generate_clinical_bundle(sexo="Mujer", seed=1)
        master = {
            "nombres": "Ana",
            "apellidos": "López Ruiz",
            "fecha_nacimiento": "1990-01-15",
            "sexo": "Mujer",
            "folio_sangre": "123456789012",
            "cliente_numero": "12345678",
            "codigo_barra": "abc1234567890",
            "fecha_toma": "2026-04-18",
            "fecha_val": "2026-04-18",
            "hora_toma": "08:00",
            "hora_val": "12:00",
        }
        sdata = build_sangre_data_for_mapping(master, bundle["sangre"])
        m = build_sangre_mapping(sdata)
        self.assertEqual(m["{folio}"], "123456789012")
        self.assertEqual(m["{codigo_barra}"], "ABC1234567890")

    def test_sangre_decimals_export_format(self):
        m = build_sangre_mapping(
            {
                "leucocitos": "7.2",
                "eritrocitos": "5.5",
                "hemoglobina": "11.5",
                "hematocrito": "40",
                "VCM": "86",
                "HCM": "28",
                "conc_media_hb_corp": "33",
                "AD_D.E.": "42",
                "AD_C.V.": "12",
                "plaquetas": "301.0",
                "V_plaquetario_medio": "9.5",
                "linfocitos_pct": "22",
                "neutrofilos_pct": "60",
                "monocitos_pct": "7",
                "eosinofilos_pct": "2",
                "basofilos_pct": "1",
                "linfocitos_abs": "1.2",
                "neutrofilos_abs": "3.4",
                "monocitos_abs": "0.6",
                "eosinofilos_abs": "0.2",
                "basofilos_abs": "0.05",
                "glucosa": "86",
                "urea": "21",
                "bun": "9",
                "creatinina": "0.6",
                "acido_urico": "5",
                "colesterol_total": "180",
                "trigliceridos": "140",
            }
        )
        self.assertEqual(m["{leucocitos}"], "7.20")
        self.assertEqual(m["{conc_media_hb_corp}"], "33.0")
        self.assertEqual(m["{plaquetas}"], "301")
        self.assertEqual(m["{creatinina}"], "0.60")
        self.assertEqual(m["{glucosa}"], "86.0")

    def test_sangre_sexo_export_mapping(self):
        d = build_sangre_data_for_mapping({"sexo": "Hombre"}, {})
        self.assertEqual(d["sexo"], "Masculino")
        d2 = build_sangre_data_for_mapping({"sexo": "Mujer"}, {})
        self.assertEqual(d2["sexo"], "Femenino")


if __name__ == "__main__":
    unittest.main()
