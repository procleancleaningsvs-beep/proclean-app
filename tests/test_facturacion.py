"""Tests módulo facturación (normalización y reglas)."""

from __future__ import annotations

import sqlite3
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import flask
import pytest
from flask import g
from werkzeug.exceptions import Forbidden

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


def _xlsx_facturacion_enero_a_mayo() -> bytes:
    import openpyxl

    headers = [
        "MES",
        "ASISTENCIA DE",
        "PLANTA",
        "USUARIO",
        "FACTURA",
        "PO",
        "SUBTOTAL",
        "IVA",
        "TOTAL",
        "FECHA FACTURA",
        "FECHA DE VENCIMIENTO",
        "ESTATUS",
        "COMENTARIOS",
        "FECHA DE PAGO",
    ]
    wb = openpyxl.Workbook()
    first = True
    for inv, mes in enumerate(("ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO"), start=1):
        if first:
            ws = wb.active
            ws.title = mes
            first = False
        else:
            ws = wb.create_sheet(mes)
        ws.append(headers)
        ws.append(
            [
                mes,
                "",
                "",
                f"u{inv}@carrier.com",
                f"F2026INV{inv:03d}",
                "PO-1",
                100,
                16,
                116,
                None,
                None,
                "EN COLA",
                "",
                None,
            ]
        )
    bio = BytesIO()
    wb.save(bio)
    wb.close()
    return bio.getvalue()


def test_import_facturacion_excel_procesa_hojas_enero_a_mayo(tmp_path):
    from modules.facturacion.db import get_import_log, insert_import_log
    from modules.facturacion.excel_import import import_facturacion_excel

    dbp = tmp_path / "imp.db"
    conn = sqlite3.connect(dbp)
    conn.row_factory = sqlite3.Row
    ensure_facturacion_tables(conn)
    content = _xlsx_facturacion_enero_a_mayo()
    res = import_facturacion_excel(
        conn,
        content,
        anio_default=2026,
        user_id=1,
        now="2026-05-01 12:00:00",
        original_filename="FACTURACION_PROCLEAN_2026_v2.xlsx",
    )
    conn.commit()
    assert set(res["hojas_procesadas"]) == {"ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO"}
    assert res["filas_leidas"] == 5
    assert res["filas_importadas"] == 5
    assert res["duplicados_omitidos"] == 0
    assert "CARRIER" in res["clientes_detectados"]
    log_id = insert_import_log(
        conn,
        res,
        user_id=1,
        now="2026-05-01 12:00:00",
        original_filename="FACTURACION_PROCLEAN_2026_v2.xlsx",
    )
    conn.commit()
    row = get_import_log(conn, log_id)
    assert row is not None
    assert row["summary"]["filas_importadas"] == 5
    conn.close()


def test_import_excel_vista_admin_ok(tmp_path):
    from modules.facturacion.blueprint import facturacion_bp, import_excel

    repo = Path(__file__).resolve().parent.parent / "templates"
    app = flask.Flask(__name__, template_folder=str(repo))
    app.config["DATABASE"] = str(tmp_path / "d.db")
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.row_factory = sqlite3.Row
    ensure_facturacion_tables(conn)
    conn.commit()
    conn.close()
    app.register_blueprint(facturacion_bp)
    with app.app_context():
        with app.test_request_context("/facturacion/import", method="GET"):
            g.user = _memory_row("admin")
            html = import_excel()
    assert "Importar Excel" in html
    assert 'name="archivo"' in html
    assert 'name="anio"' in html


def test_import_excel_vista_no_admin_403(tmp_path):
    from modules.facturacion.blueprint import facturacion_bp, import_excel

    repo = Path(__file__).resolve().parent.parent / "templates"
    app = flask.Flask(__name__, template_folder=str(repo))
    app.config["DATABASE"] = str(tmp_path / "d2.db")
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.row_factory = sqlite3.Row
    ensure_facturacion_tables(conn)
    conn.commit()
    conn.close()
    app.register_blueprint(facturacion_bp)
    with pytest.raises(Forbidden):
        with app.app_context():
            with app.test_request_context("/facturacion/import", method="GET"):
                g.user = _memory_row("usuario")
                import_excel()


def test_format_money_mx():
    from modules.facturacion.money_format import format_money_mx

    assert format_money_mx(25450.8) == "$25,450.80"
    assert format_money_mx(None) == "—"


def test_extract_fecha_from_xml_cfdi():
    from modules.facturacion.doc_fechas import extract_fecha_emision_from_xml

    xml = b'<?xml version="1.0"?><cfdi:Comprobante Fecha="2026-03-15T12:00:00" xmlns:cfdi="http://www.sat.gob.mx/cfd/4">'
    assert extract_fecha_emision_from_xml(xml) == "2026-03-15"


def test_resolve_cliente_principal_map(tmp_path):
    from modules.facturacion.cliente_catalog import load_catalog_maps, resolve_cliente_principal
    from modules.facturacion.config import CLIENTE_POR_CLASIFICAR
    from modules.facturacion.db import upsert_razon_social_map
    from modules.facturacion.normalize import fix_cliente_name

    dbp = tmp_path / "c.db"
    conn = sqlite3.connect(dbp)
    conn.row_factory = sqlite3.Row
    ensure_facturacion_tables(conn)
    upsert_razon_social_map(conn, razon_social="PEPSI NORTE SA", cliente_principal="Pepsi", now="2026-01-01 00:00:00")
    conn.commit()
    maps = load_catalog_maps(conn)
    p, rz = resolve_cliente_principal(
        maps,
        razon_social_excel="PEPSI NORTE SA",
        cli_infer=None,
        fix_cliente_name_fn=fix_cliente_name,
        por_clasificar=CLIENTE_POR_CLASIFICAR,
    )
    assert p == "Pepsi"
    assert rz == "PEPSI NORTE SA"
    conn.close()
