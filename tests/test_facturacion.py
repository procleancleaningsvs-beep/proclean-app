"""Tests módulo facturación (normalización y reglas)."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import flask
from flask import g

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


def test_archivo_faltante_condicionado_por_estatus():
    from modules.facturacion.normalize import compute_auto_alertas

    a = compute_auto_alertas(
        cliente="VITRO",
        po_oc="PO1",
        tiene_pdf=False,
        tiene_xml=False,
        estatus_operativo="EN COLA",
        numero_factura="1234",
        manual_alertas=[],
    )
    assert "ARCHIVO FALTANTE" not in a
    b = compute_auto_alertas(
        cliente="VITRO",
        po_oc="PO1",
        tiene_pdf=False,
        tiene_xml=False,
        estatus_operativo="LISTO",
        numero_factura="1234",
        manual_alertas=[],
    )
    assert "ARCHIVO FALTANTE" in b


def test_archivo_faltante_sin_numero_valido():
    from modules.facturacion.normalize import compute_auto_alertas

    c = compute_auto_alertas(
        cliente="VITRO",
        po_oc="PO1",
        tiene_pdf=False,
        tiene_xml=False,
        estatus_operativo="LISTO",
        numero_factura="",
        manual_alertas=[],
    )
    assert "ARCHIVO FALTANTE" not in c
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


def _memory_row(role: str) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE u (id INTEGER, username TEXT, role TEXT)")
    conn.execute("INSERT INTO u VALUES (1, 't', ?)", (role,))
    return conn.execute("SELECT * FROM u").fetchone()


def test_current_user_role_and_is_admin_with_sqlite_row():
    from modules.facturacion.blueprint import _current_user_role, _is_admin

    app = flask.Flask(__name__)
    with app.app_context():
        with app.test_request_context():
            g.user = _memory_row("admin")
            assert _current_user_role() == "admin"
            assert _is_admin() is True
            g.user = _memory_row("usuario")
            assert _current_user_role() == "usuario"
            assert _is_admin() is False


def test_current_user_role_with_dict_user():
    from modules.facturacion.blueprint import _current_user_role, _is_admin

    app = flask.Flask(__name__)
    with app.app_context():
        with app.test_request_context():
            g.user = {"id": 1, "role": "admin"}
            assert _current_user_role() == "admin"
            assert _is_admin() is True


def test_facturacion_dashboard_passes_is_admin_with_sqlite_row_user(tmp_path):
    """Regresión: g.user como sqlite3.Row no debe lanzar AttributeError al armar el dashboard."""
    from modules.facturacion.blueprint import dashboard, facturacion_bp

    dbp = tmp_path / "app.db"
    conn = sqlite3.connect(dbp)
    conn.row_factory = sqlite3.Row
    ensure_facturacion_tables(conn)
    conn.close()

    app = flask.Flask(__name__)
    app.config["DATABASE"] = str(dbp)
    app.register_blueprint(facturacion_bp)

    with app.app_context():
        with app.test_request_context("/facturacion/dashboard"):
            g.user = _memory_row("admin")
            with patch("modules.facturacion.blueprint.render_template", return_value="<html/>") as rt:
                dashboard()
            assert rt.call_args is not None
            assert rt.call_args.kwargs["is_admin"] is True

        with app.test_request_context("/facturacion/dashboard"):
            g.user = _memory_row("usuario")
            with patch("modules.facturacion.blueprint.render_template", return_value="<html/>") as rt:
                dashboard()
            assert rt.call_args.kwargs["is_admin"] is False
