from __future__ import annotations

import os

# Cliente cuando no se pudo inferir desde el Excel
CLIENTE_POR_CLASIFICAR = "POR CLASIFICAR"

# Solo en estos estatus se añade automáticamente la alerta ARCHIVO FALTANTE (evita saturar EN COLA / cotización).
# Forzar en todos: variable de entorno PROCLEAN_FACT_ADJUNTO_ALERTA_TODOS=1
Estatus_ALERTA_ADJUNTO_AUTO: frozenset[str] = frozenset(
    {"LISTO", "PORTAL", "PENDIENTE NR", "ENVIADO"}
)


def archivofaltante_auto_activo(estatus_operativo: str) -> bool:
    if os.environ.get("PROCLEAN_FACT_ADJUNTO_ALERTA_TODOS", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return str(estatus_operativo or "").strip().upper() in Estatus_ALERTA_ADJUNTO_AUTO


# Estatus operativos permitidos (clave canónica)
OPERATIVO_ORDER: tuple[str, ...] = (
    "EN COLA",
    "COTIZACIÓN ENVIADA",
    "ENVIADO",
    "PORTAL",
    "PENDIENTE NR",
    "LISTO",
)

OPERATIVO_SET = frozenset(OPERATIVO_ORDER)

# Estatus de pago
PAGO_ORDER: tuple[str, ...] = ("PENDIENTE", "PAGADO", "PARCIAL", "NO APLICA")
PAGO_SET = frozenset(PAGO_ORDER)

# Alertas (catálogo)
ALERTA_SET = frozenset(
    {
        "URGENTE",
        "ERROR",
        "REFACTURAR",
        "FALTA COMPROBANTE",
        "SIN PO/OC",
        "ARCHIVO FALTANTE",
    }
)

# Clientes que exigen PO/OC (comparación insensible a mayúsculas)
CLIENTES_PO_OC_OBLIGATORIO: frozenset[str] = frozenset({"GEPP", "CARRIER"})


def cliente_requiere_po_oc(cliente: str) -> bool:
    c = " ".join(str(cliente or "").strip().upper().split())
    return c in CLIENTES_PO_OC_OBLIGATORIO
