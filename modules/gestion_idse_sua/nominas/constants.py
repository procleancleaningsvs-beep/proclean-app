from __future__ import annotations

from modules.gestion_idse_sua.nominas.text_utils import normalize_header

NOMBRE_HEADERS: frozenset[str] = frozenset(
    {
        "NOMBRE DE EMPLEADO",
        "NOMBRE EMPLEADO",
        "NOMBRE DEL EMPLEADO",
        "EMPLEADO",
    }
)

NUM_EMPLEADO_HEADERS: frozenset[str] = frozenset(
    {
        "NO.",
        "NO",
        "NUM",
        "NUM.",
        "NUMERO",
        "NÚMERO",
        "NUM EMPLEADO",
        "NÚM. EMPLEADO",
        "NUM. EMPLEADO",
        "# EMPLEADO",
    }
)

PUESTO_HEADERS: frozenset[str] = frozenset({"PUESTO", "PUESTO / CARGO", "CARGO"})
PLANTA_HEADERS: frozenset[str] = frozenset({"PLANTA", "UBICACION", "UBICACIÓN", "CENTRO"})
CUENTA_HEADERS: frozenset[str] = frozenset({"CUENTA", "CUENTA BANCARIA", "NO. CUENTA", "NÚM. CUENTA", "CLABE"})

TOTAL_MARKERS: frozenset[str] = frozenset({"TOTAL", "TOTALES", "SUBTOTAL", "GRAN TOTAL"})
