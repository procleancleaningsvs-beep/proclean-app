"""Compactación de filas de deducción en placeholders DOCX del finiquito."""

from __future__ import annotations

import unittest

from modules.finiquitos.export_docx import empaquetar_filas_deduccion_para_docx


class TestEmpaquetarDeduccionesDocx(unittest.TestCase):
    def test_41_45_99_sin_43_sin_hueco(self):
        pdf = {
            "n8": "41",
            "c_isa": "I.S.R. antes de Subs al empleo",
            "t8": "100.00",
            "n9": "",
            "c_i174": "",
            "t9": "",
            "n10": "45",
            "c_imes": "I.S.R. (mes)",
            "t10": "100.00",
            "n_sep": "",
            "c_sep": "",
            "t_sep": "",
        }
        d = empaquetar_filas_deduccion_para_docx(
            pdf,
            nd="99",
            cd="Ajuste al neto",
            t11d="50.00",
        )
        self.assertEqual(d["n8"], "41")
        self.assertEqual(d["n9"], "45")
        self.assertEqual(d["n10"], "99")
        self.assertEqual(d["nd"], "")
        self.assertEqual(d["cd"], "")
        self.assertEqual(d["t11d"], "")

    def test_41_43_45_99_orden_natural(self):
        pdf = {
            "n8": "41",
            "c_isa": "A",
            "t8": "1.00",
            "n9": "43",
            "c_i174": "B",
            "t9": "2.00",
            "n10": "45",
            "c_imes": "C",
            "t10": "3.00",
            "n_sep": "",
            "c_sep": "",
            "t_sep": "",
        }
        d = empaquetar_filas_deduccion_para_docx(pdf, nd="99", cd="D", t11d="4.00")
        self.assertEqual((d["n8"], d["n9"], d["n10"], d["nd"]), ("41", "43", "45", "99"))


if __name__ == "__main__":
    unittest.main()
