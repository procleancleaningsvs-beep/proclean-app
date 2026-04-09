"""Nombre de archivo PDF del finiquito (formato propio)."""

from __future__ import annotations

import unittest

from modules.finiquitos.nombre_archivo_finiquito import (
    build_finiquito_pdf_filename,
    nombre_propio_para_archivo,
)


class TestNombrePropioArchivo(unittest.TestCase):
    def test_minusculas(self):
        self.assertEqual(
            nombre_propio_para_archivo("yahir ramon ramirez mata"),
            "Yahir Ramon Ramirez Mata",
        )

    def test_mayusculas(self):
        self.assertEqual(
            nombre_propio_para_archivo("YAHIR RAMON RAMIREZ MATA"),
            "Yahir Ramon Ramirez Mata",
        )

    def test_mixto(self):
        self.assertEqual(
            nombre_propio_para_archivo("yAhIr rAmOn rAmIrEz mAtA"),
            "Yahir Ramon Ramirez Mata",
        )

    def test_espacios_extra(self):
        self.assertEqual(
            nombre_propio_para_archivo("  yahir   ramon ramirez   mata  "),
            "Yahir Ramon Ramirez Mata",
        )

    def test_filename_completo(self):
        self.assertEqual(
            build_finiquito_pdf_filename("yahir ramon ramirez mata"),
            "Finiquito Yahir Ramon Ramirez Mata.pdf",
        )


if __name__ == "__main__":
    unittest.main()
