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
CLIENTE_HEADERS: frozenset[str] = frozenset(
    {"CLIENTE", "NOMBRE CLIENTE", "RAZON SOCIAL CLIENTE", "RAZÓN SOCIAL CLIENTE"}
)
PLANTA_HEADERS: frozenset[str] = frozenset({"PLANTA", "LOCALIDAD", "UBICACION", "UBICACIÓN", "CENTRO"})
CUENTA_HEADERS: frozenset[str] = frozenset({"CUENTA", "CUENTA BANCARIA", "NO. CUENTA", "NÚM. CUENTA", "CLABE"})
VALOR_HE_HEADERS: frozenset[str] = frozenset(
    {"VALOR X HE", "VALOR X HORA EXTRA", "VALOR HE", "VALOR HORA EXTRA"}
)
CONTPAQ_MARKERS: frozenset[str] = frozenset(
    {"CONTPAQ", "INFORMACION DEL EMPLEADO", "INFORMACIÓN DEL EMPLEADO", "CODIGO", "CÓDIGO"}
)
PAYROLL_HEADER_MARKERS: frozenset[str] = frozenset(
    {"NO.", "NO", "NOMBRE", "NOMBRE DE EMPLEADO", "PLANTA", "LOCALIDAD", "PUESTO", "BANCO", "CUENTA", "SALARIO", "VALOR", "FRONTERA"}
)

TOTAL_MARKERS: frozenset[str] = frozenset({"TOTAL", "TOTALES", "SUBTOTAL", "GRAN TOTAL"})
