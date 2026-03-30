"""Constantes fiscales y laborales 2026 (configuración anual)."""

from __future__ import annotations

from decimal import Decimal

# --- UMA diaria (subsidio al empleo: coherente con tablas y ejemplo operativo) ---
UMA_DIARIA_2026 = Decimal("117.31")

# --- Salario mínimo 2026 ---
SMG_GENERAL_2026 = Decimal("315.04")
SMG_FRONTERA_2026 = Decimal("440.87")

# --- UMA 2026 ---
UMA_MENSUAL_2026 = Decimal("3566.22")
UMA_ANUAL_2026 = Decimal("42794.64")

# UMA mensual vigente 2025 (enero 2026 subsidio según decreto; INEGI UMA 2025).
UMA_MENSUAL_2025 = Decimal("3438.42")

# --- Subsidio al empleo 2026 ---
SUBSIDIO_PCT_2026 = Decimal("0.1502")
SUBSIDIO_PCT_ENERO_2026 = Decimal("0.1559")
SUBSIDIO_LIMITE_MENSUAL_2026 = Decimal("11492.66")

# Tablas ISR artículo 96 — límites inferiores en pesos (2026 RMF Anexo 8).
# Formato: (lim_inf, lim_sup, cuota_fija, pct_excedente)
# "En adelante" se representa con lim_sup = None.

ISR_TABLA_QUINCENAL_2026: list[tuple[Decimal, Decimal | None, Decimal, Decimal]] = [
    (Decimal("0.01"), Decimal("416.70"), Decimal("0.00"), Decimal("1.92")),
    (Decimal("416.71"), Decimal("3537.15"), Decimal("7.95"), Decimal("6.40")),
    (Decimal("3537.16"), Decimal("6216.15"), Decimal("207.75"), Decimal("10.88")),
    (Decimal("6216.16"), Decimal("7225.95"), Decimal("499.20"), Decimal("16.00")),
    (Decimal("7225.96"), Decimal("8651.40"), Decimal("660.75"), Decimal("17.92")),
    (Decimal("8651.41"), Decimal("17448.75"), Decimal("916.20"), Decimal("21.36")),
    (Decimal("17448.76"), Decimal("27501.60"), Decimal("2795.25"), Decimal("23.52")),
    (Decimal("27501.61"), Decimal("52505.25"), Decimal("5159.70"), Decimal("30.00")),
    (Decimal("52505.26"), Decimal("70006.95"), Decimal("12660.75"), Decimal("32.00")),
    (Decimal("70006.96"), Decimal("210020.70"), Decimal("18261.30"), Decimal("34.00")),
    (Decimal("210020.71"), None, Decimal("65866.05"), Decimal("35.00")),
]

ISR_TABLA_MENSUAL_2026: list[tuple[Decimal, Decimal | None, Decimal, Decimal]] = [
    (Decimal("0.01"), Decimal("844.59"), Decimal("0.00"), Decimal("1.92")),
    (Decimal("844.60"), Decimal("7168.51"), Decimal("16.22"), Decimal("6.40")),
    (Decimal("7168.52"), Decimal("12598.02"), Decimal("420.95"), Decimal("10.88")),
    (Decimal("12598.03"), Decimal("14644.64"), Decimal("1011.68"), Decimal("16.00")),
    (Decimal("14644.65"), Decimal("17533.64"), Decimal("1339.14"), Decimal("17.92")),
    (Decimal("17533.65"), Decimal("35362.83"), Decimal("1856.84"), Decimal("21.36")),
    (Decimal("35362.84"), Decimal("55736.68"), Decimal("5665.16"), Decimal("23.52")),
    (Decimal("55736.69"), Decimal("106410.50"), Decimal("10457.09"), Decimal("30.00")),
    (Decimal("106410.51"), Decimal("141880.66"), Decimal("25659.23"), Decimal("32.00")),
    (Decimal("141880.67"), Decimal("425641.99"), Decimal("37009.69"), Decimal("34.00")),
    (Decimal("425642.00"), None, Decimal("133488.54"), Decimal("35.00")),
]
