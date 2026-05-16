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
    "PENDIENTE FACTURA",
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
        "SIN NÚMERO FACTURA",
    }
)

# Clientes que exigen PO/OC (comparación insensible a mayúsculas)
CLIENTES_PO_OC_OBLIGATORIO: frozenset[str] = frozenset({"GEPP", "CARRIER"})

# Nombres frecuentes en columna MES del Excel como encabezado de bloque (no es mes calendario).
# Refuerza el contexto cuando aún no hay filas en catálogo de crédito/razón.
CLIENTE_BLOQUE_EXCEL_NAMES: frozenset[str] = frozenset(
    {
        "CARRIER",
        "GEPP",
        "GEEP",
        "VITRO",
        "PEPSI",
        "AURIGA",
        "CENTRIKA",
        "EMBOBOTELLADOR",
    }
)

# Dominios de correo personal que no deben usarse para inferir cliente automáticamente.
DOMINIOS_CORREO_PUBLICO: frozenset[str] = frozenset(
    {
        "gmail.com",
        "hotmail.com",
        "outlook.com",
        "yahoo.com",
        "yahoo.com.mx",
        "live.com",
        "msn.com",
        "icloud.com",
        "proton.me",
        "protonmail.com",
    }
)


def cliente_requiere_po_oc(cliente: str) -> bool:
    c = " ".join(str(cliente or "").strip().upper().split())
    return c in CLIENTES_PO_OC_OBLIGATORIO
