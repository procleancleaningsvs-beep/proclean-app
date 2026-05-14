from __future__ import annotations

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
