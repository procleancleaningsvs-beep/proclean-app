"""Banorte payroll export submodule (Nóminas > Exportaciones > Banorte)."""

from modules.nomina.banorte.schema import BANORTE_TABLES, ensure_banorte_tables

__all__ = ["BANORTE_TABLES", "ensure_banorte_tables"]
