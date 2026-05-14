"""Tests módulo facturación (normalización y reglas)."""

from __future__ import annotations

import sqlite3

from modules.facturacion.config import cliente_requiere_po_oc
from modules.facturacion.db import ensure_facturacion_tables, insert_factura
from modules.facturacion.normalize import (
    fix_cliente_name,
    normalize_estatus_operativo,
    normalize_estatus_pago,
    split_operativo_y_pago,
)


def test_gepp_typo():
    assert fix_cliente_name("GEEP SA") == "GEPP SA"


def test_operativo_normalization():
    assert normalize_estatus_operativo("RECEPCIÓN") == "PENDIENTE NR"
    assert normalize_estatus_operativo("FALTA NR") == "PENDIENTE NR"
    assert normalize_estatus_operativo("ENVIADA") == "ENVIADO"
    assert normalize_estatus_operativo("ENVIADO") == "ENVIADO"
    assert normalize_estatus_operativo("NO PORTAL") == "ENVIADO"
    assert normalize_estatus_operativo("SE MANDÓ COT") == "COTIZACIÓN ENVIADA"


def test_pago_split():
    assert split_operativo_y_pago("PAGADA") == (None, "PAGADO")
    _, p = split_operativo_y_pago("PAGADA")
    assert normalize_estatus_pago(p, tiene_fecha_pago=False) == "PAGADO"


def test_po_oc_rule():
    assert cliente_requiere_po_oc("GEPP") is True
    assert cliente_requiere_po_oc("carrier") is True
    assert cliente_requiere_po_oc("VITRO") is False


def test_insert_and_duplicate_active(tmp_path):
    dbp = tmp_path / "t.db"
    conn = sqlite3.connect(dbp)
    conn.row_factory = sqlite3.Row
    ensure_facturacion_tables(conn)
    now = "2026-01-01 00:00:00"
    base = {
        "mes": 2,
        "anio": 2026,
        "cliente": "CARRIER",
        "numero_factura": "5001",
        "estatus_operativo": "EN COLA",
        "estatus_pago": "PENDIENTE",
    }
    insert_factura(conn, base, user_id=1, now=now)
    conn.commit()
    try:
        insert_factura(conn, base, user_id=1, now=now)
        conn.commit()
        raise AssertionError("expected IntegrityError")
    except sqlite3.IntegrityError:
        conn.rollback()
