"""Empaquetado de deducciones en placeholders DOCX (sin alterar la tabla del template)."""

from __future__ import annotations

import unittest

from modules.finiquitos.export_docx import (
    empaquetar_filas_deduccion_para_docx,
    empaquetar_filas_percepcion_para_docx,
)


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
        self.assertEqual(d["c_i174"], "I.S.R. (mes)")
        self.assertEqual(d["n10"], "99")
        self.assertEqual(d["c_imes"], "Ajuste al neto")
        self.assertEqual(d["t10"], "50.00")
        self.assertEqual(d["nd"], "")
        self.assertEqual(d["cd"], "")
        self.assertEqual(d["t11d"], "")

    def test_41_y_99_sin_hueco_intermedio(self):
        pdf = {
            "n8": "41",
            "c_isa": "I.S.R. antes de Subs al empleo",
            "t8": "10.00",
            "n9": "",
            "c_i174": "",
            "t9": "",
            "n10": "",
            "c_imes": "",
            "t10": "",
            "n_sep": "",
            "c_sep": "",
            "t_sep": "",
        }
        d = empaquetar_filas_deduccion_para_docx(
            pdf,
            nd="99",
            cd="Ajuste al neto",
            t11d="5.00",
        )
        self.assertEqual(d["n8"], "41")
        self.assertEqual(d["n9"], "99")
        self.assertEqual(d["c_i174"], "Ajuste al neto")
        self.assertEqual(d["n10"], "")
        self.assertEqual(d["nd"], "")

    def test_ajuste_percepcion_sin_prima_sube_a_n7(self):
        p = empaquetar_filas_percepcion_para_docx(
            "", "", "",
            "99",
            "Ajuste al neto",
            "25.00",
        )
        self.assertEqual(p["n7"], "99")
        self.assertEqual(p["c_pant"], "Ajuste al neto")
        self.assertEqual(p["t7"], "25.00")
        self.assertEqual(p["np"], "")
        self.assertEqual(p["cp"], "")
        self.assertEqual(p["t11p"], "")


if __name__ == "__main__":
    unittest.main()
