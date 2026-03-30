"""
Pruebas Vitroflex + evidencia en consola (sin pytest).
Ejecutar desde la raíz del proyecto: python scripts/test_vitroflex_evidence.py
"""

from __future__ import annotations

import io
import random
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openpyxl import Workbook

from modules.vitroflex_docs.dates import linea_fecha_documento, permiso2_desde_permiso1, parse_iso_date
from modules.vitroflex_docs.excel_import import parse_excel_bytes
from modules.vitroflex_docs.naming import (
    cr_filename_opcion1,
    cr_filename_opcion2,
    memo_filename_opcion1,
    memo_filename_opcion2,
)


def _rand_name() -> str:
    return random.choice(["Ana", "Luis", "María", "Jorge", "Rosa"]) + " " + random.choice(
        ["García", "López", "Martínez", "Hernández"]
    )


def _xlsx_bytes_two_cols() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["NOMBRE TRABAJADOR", "NO. IMSS"])
    for _ in range(3):
        ws.append([_rand_name(), "".join(random.choices(string.digits, k=11))])
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def _xlsx_bytes_four_cols() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["NOMBRE TRABAJADOR", "NO. IMSS", "ACTIVIDAD A REALIZAR", "TEL. EMERGENCIA"])
    for _ in range(4):
        ws.append(
            [
                _rand_name(),
                "".join(random.choices(string.digits, k=11)),
                "Aux. de limpieza",
                "81 2183 9413",
            ]
        )
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def test_linea_fecha_documento():
    s = linea_fecha_documento(
        fecha_iso="2026-03-30",
        municipio_modo="garcia",
        municipio_otro="",
        fecha_texto_legacy=None,
    )
    assert "Garcia, N. L." in s and "30" in s and "marzo" in s and "2026" in s
    s2 = linea_fecha_documento(
        fecha_iso="2026-03-30",
        municipio_modo="otro",
        municipio_otro="Monterrey, N. L.",
        fecha_texto_legacy=None,
    )
    assert "Monterrey" in s2
    leg = linea_fecha_documento(
        fecha_iso=None,
        municipio_modo="garcia",
        municipio_otro="",
        fecha_texto_legacy="  Legacy fecha  ",
    )
    assert leg == "Legacy fecha"


def test_permiso2():
    d = parse_iso_date("2026-03-10")
    assert d
    p2 = permiso2_desde_permiso1(d)
    assert p2.isoformat() == "2026-04-27", p2


def test_excel():
    r2, nd, err = parse_excel_bytes(_xlsx_bytes_two_cols())
    assert err is None
    assert nd is True
    assert len(r2) == 3

    r4, nd4, err4 = parse_excel_bytes(_xlsx_bytes_four_cols())
    assert err4 is None
    assert nd4 is False
    assert len(r4) == 4


def test_filenames():
    nombres = ["Uno Dos", "Tres Cuatro", "Cinco Seis"]
    assert "MEMO" in memo_filename_opcion1(nombres)
    assert "MEMO MENSUAL" in memo_filename_opcion2("2026-03-15")
    assert "CR" in cr_filename_opcion1("Vitroflex", nombres)
    assert "marzo" in cr_filename_opcion2("Flotado", "2026-03-01", None).lower()


def test_flask_routes():
    import sqlite3

    from app import DB_PATH, app

    client = app.test_client()
    r = client.get("/vitroflex/memo", follow_redirects=False)
    assert r.status_code == 302

    conn = sqlite3.connect(DB_PATH)
    uid = conn.execute("SELECT id FROM users LIMIT 1").fetchone()[0]
    conn.close()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    r_ok = client.get("/vitroflex/memo")
    assert r_ok.status_code == 200
    r_tpl = client.get("/vitroflex/plantilla/descargable")
    assert r_tpl.status_code == 200
    ct = (r_tpl.content_type or "").lower()
    assert "spreadsheet" in ct or "octet-stream" in ct or "excel" in ct


def test_user_paths_excel():
    """Si existen los Excel del usuario en Descargas, parsearlos y reportar."""
    downloads = Path.home() / "Downloads"
    candidates = [
        downloads / "ejemplo 1 excel para llenado.xlsx",
        downloads / "ejemplo 2 excel para llenado.xlsx",
        downloads / "descargable de ejemplo.xlsx",
    ]
    lines = []
    for p in candidates:
        if not p.is_file():
            lines.append(f"[omitido] no existe: {p}")
            continue
        data = p.read_bytes()
        rows, nd, err = parse_excel_bytes(data)
        lines.append(f"[ok] {p.name}: filas={len(rows)} needs_defaults={nd} err={err}")
    return lines


def main():
    test_linea_fecha_documento()
    test_permiso2()
    test_excel()
    test_filenames()
    test_flask_routes()
    print("--- Vitroflex: pruebas unitarias OK ---")
    print("PERMISO_2(2026-03-10) ->", permiso2_desde_permiso1(parse_iso_date("2026-03-10")))
    print("memo opt1:", memo_filename_opcion1(["Ana López", "Luis Pérez", "Otro"]))
    print("memo opt2:", memo_filename_opcion2("2026-06-01"))
    print("cr opt1:", cr_filename_opcion1("Vitroflex", ["Ana López"]))
    print("cr opt2:", cr_filename_opcion2("Mercado Moderno", None, "Garcia a 15 de marzo de 2026"))
    print("--- Archivos opcionales en Descargas ---")
    for line in test_user_paths_excel():
        print(line)
    plantilla = ROOT / "static" / "vitroflex_docs" / "assets" / "descargable_de_ejemplo.xlsx"
    print("Plantilla empaquetada:", plantilla.is_file(), plantilla)


if __name__ == "__main__":
    main()
