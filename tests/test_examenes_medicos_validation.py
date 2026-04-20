from __future__ import annotations

import unittest
from datetime import date

from modules.examenes_medicos.export_helpers import (
    build_orina_mapping,
    build_paciente_sangre,
    build_sangre_mapping,
    default_hora_val_sugerida,
    fecha_iso_a_dd_mm_yyyy,
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
            "HERNÁNDEZ MARTÍNEZ,DENISSE ARACELY",
        )

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
        self.assertEqual(m["{fecha_estudio}"], "19/04/2026")
        self.assertEqual(m["{paciente_nombre_completo}"], "Juan Pérez")

    def test_build_sangre_mapping_dates(self):
        m = build_sangre_mapping(
            {
                "fecha_nacimiento": "1993-03-07",
                "fecha_toma": "2026-04-18",
                "fecha_val": "2026-04-18",
                "hora_toma": "08:30",
                "hora_val": "12:00",
                "codigo_barra": "anc8349230417",
                "nombres": "X",
                "apellidos": "Y",
                "edad": "1",
                "sexo": "Mujer",
                "paciente_nombre_completo": "Y,X",
                "cliente_numero": "12345678",
                "folio": "123456789012",
            }
            | {k: "0" for k in ("leucocitos", "eritrocitos", "hemoglobina", "hematocrito", "VCM", "HCM", "conc_media_hb_corp", "AD_D.E.", "AD_C.V.", "plaquetas", "V_plaquetario_medio", "linfocitos_pct", "neutrofilos_pct", "monocitos_pct", "eosinofilos_pct", "basofilos_pct", "linfocitos_abs", "neutrofilos_abs", "monocitos_abs", "eosinofilos_abs", "basofilos_abs", "glucosa", "urea", "bun", "creatinina", "acido_urico", "colesterol_total", "trigliceridos")}
        )
        self.assertEqual(m["{fecha_nacimiento}"], "07/03/1993")
        self.assertEqual(m["{codigo_barra}"], "ANC8349230417")
        self.assertEqual(m["{hora_toma}"], "08:30:00")


if __name__ == "__main__":
    unittest.main()
