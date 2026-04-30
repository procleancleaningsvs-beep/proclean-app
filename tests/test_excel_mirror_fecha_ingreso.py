"""Pruebas del cruce nombre → fecha (DataFrame) y normalización."""

from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from modules.finiquitos.excel_mirror_fecha_ingreso import (
    buscar_fecha_ingreso_en_dataframe,
    normalizar_nombre,
    normalizar_nombre_para_cruce,
)


class TestNormalizarNombre(unittest.TestCase):
    def test_acentos_y_espacios(self):
        self.assertEqual(normalizar_nombre("  José   Pérez  "), "JOSE PEREZ")
        self.assertEqual(normalizar_nombre_para_cruce("  José   Pérez  "), "JOSE PEREZ")

    def test_caracteres_raros(self):
        self.assertEqual(normalizar_nombre("María#López@2"), "MARIA LOPEZ 2")


class TestBuscarFechaEnDataframe(unittest.TestCase):
    def test_happy_path(self):
        df = pd.DataFrame(
            {
                "NOMBRE COMPLETO": ["María  López  García"],
                "FECHA DE INGRESO": [pd.Timestamp("2022-06-01")],
                "SUELDO SEMANAL": [2700],
            }
        )
        d, nom, err, sueldo = buscar_fecha_ingreso_en_dataframe(df, "MARIA LOPEZ GARCIA")
        self.assertIsNone(err)
        self.assertEqual(d, date(2022, 6, 1))
        self.assertEqual(nom, "María  López  García")
        self.assertEqual(sueldo, 2700.0)

    def test_no_na_n_a(self):
        df = pd.DataFrame(
            {
                "NOMBRE COMPLETO": ["Ana Ruiz", "Ana Ruiz"],
                "FECHA DE INGRESO": ["N/A", pd.Timestamp("2020-05-05")],
            }
        )
        d, nom, err, sueldo = buscar_fecha_ingreso_en_dataframe(df, "Ana Ruiz")
        self.assertIsNone(err)
        self.assertEqual(d, date(2020, 5, 5))
        self.assertIsNone(sueldo)

    def test_sin_coincidencia(self):
        df = pd.DataFrame(
            {
                "NOMBRE COMPLETO": ["Otro"],
                "FECHA DE INGRESO": [pd.Timestamp("2020-01-01")],
            }
        )
        d, nom, err, sueldo = buscar_fecha_ingreso_en_dataframe(df, "No Existo")
        self.assertIsNone(d)
        self.assertIsNone(nom)
        self.assertIn("No se encontró", err or "")
        self.assertIsNone(sueldo)

    def test_multiples_primera_sin_fecha_segunda_valida(self):
        df = pd.DataFrame(
            {
                "NOMBRE COMPLETO": ["Juan Pérez", "JUAN  PEREZ"],
                "FECHA DE INGRESO": ["NO", pd.Timestamp("2021-07-01")],
            }
        )
        d, nom, err, sueldo = buscar_fecha_ingreso_en_dataframe(df, "Juan Pérez")
        self.assertIsNone(err)
        self.assertEqual(d, date(2021, 7, 1))
        self.assertEqual(nom, "JUAN  PEREZ")
        self.assertIsNone(sueldo)

    def test_coincidencia_sin_fecha_valida(self):
        df = pd.DataFrame(
            {
                "NOMBRE COMPLETO": ["Pedro"],
                "FECHA DE INGRESO": ["NO"],
            }
        )
        d, nom, err, sueldo = buscar_fecha_ingreso_en_dataframe(df, "Pedro")
        self.assertIsNone(d)
        self.assertEqual(nom, "Pedro")
        self.assertIn("no tiene fecha válida", (err or "").lower())
        self.assertIsNone(sueldo)

    def test_sueldo_con_moneda_parsea(self):
        df = pd.DataFrame(
            {
                "NOMBRE COMPLETO": ["Laura Gómez"],
                "FECHA DE INGRESO": [pd.Timestamp("2023-03-10")],
                "sueldo semanal": ["$2,700.00"],
            }
        )
        d, _, err, sueldo = buscar_fecha_ingreso_en_dataframe(df, "Laura Gomez")
        self.assertIsNone(err)
        self.assertEqual(d, date(2023, 3, 10))
        self.assertEqual(sueldo, 2700.0)

    def test_sueldo_invalido_no_rompe(self):
        df = pd.DataFrame(
            {
                "NOMBRE COMPLETO": ["Mario Ruiz"],
                "FECHA DE INGRESO": [pd.Timestamp("2021-11-01")],
                " SUELDO   SEMANAL ": ["SIN DATO"],
            }
        )
        d, _, err, sueldo = buscar_fecha_ingreso_en_dataframe(df, "Mario Ruiz")
        self.assertIsNone(err)
        self.assertEqual(d, date(2021, 11, 1))
        self.assertIsNone(sueldo)
