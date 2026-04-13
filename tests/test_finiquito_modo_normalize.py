"""Normalización de modo_calculo (solo Fiscal / Total gravable; legados → total_gravable)."""

from __future__ import annotations

import unittest

from modules.finiquitos.blueprint import _normalize_finiquito_modo


class TestNormalizeFiniquitoModo(unittest.TestCase):
    def test_activos(self) -> None:
        self.assertEqual(_normalize_finiquito_modo("correcto_fiscal"), "correcto_fiscal")
        self.assertEqual(_normalize_finiquito_modo("total_gravable"), "total_gravable")

    def test_legados_texto(self) -> None:
        self.assertEqual(_normalize_finiquito_modo("aguinaldo_todo_gravable"), "total_gravable")
        self.assertEqual(_normalize_finiquito_modo("Aguinaldo todo gravable"), "total_gravable")
        self.assertEqual(_normalize_finiquito_modo("aguinaldo-todo-gravable"), "total_gravable")

    def test_desconocido_va_a_total_gravable(self) -> None:
        self.assertEqual(_normalize_finiquito_modo("modo_inventado"), "total_gravable")


if __name__ == "__main__":
    unittest.main()
