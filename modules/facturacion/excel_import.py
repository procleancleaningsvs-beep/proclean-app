from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from io import BytesIO
from typing import Any

import openpyxl

from modules.facturacion.db import insert_factura
from modules.facturacion.normalize import (
    alertas_desde_texto_excel,
    coerce_operativo_o_default,
    fix_cliente_name,
    merge_alertas,
    normalize_estatus_operativo,
    normalize_estatus_pago,
    parse_mes_num,
    split_operativo_y_pago,
)
MES_SHEETS = frozenset(
    {"ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"}
)


def _norm_cell(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, float) and str(x).endswith(".0"):
        try:
            return str(int(x))
        except ValueError:
            pass
    return str(x).strip()


def _fecha_sql(val: Any) -> str | None:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val.date().isoformat()
    if isinstance(val, date):
        return val.isoformat()
    s = str(val).strip()[:10]
    return s or None


def _float_val(val: Any) -> float | None:
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val).replace(",", ""))
    except ValueError:
        return None


def _infer_cliente(usuario: str, mes_col: str) -> str | None:
    u = (usuario or "").strip().lower()
    m = (mes_col or "").strip().upper()
    if m == "CARRIER":
        return "CARRIER"
    if m in ("GEPP", "GEEP"):
        return "GEPP"
    if "@gepp.com" in u or "gepp.com" in u:
        return "GEPP"
    if "@carrier.com" in u:
        return "CARRIER"
    if "@" in u:
        dom = u.split("@", 1)[-1]
        dom = dom.split(".")[0]
        return fix_cliente_name(dom.upper())
    return None


def _find_header_row(ws: openpyxl.worksheet.worksheet.Worksheet) -> tuple[int, dict[str, int]]:
    """Encuentra fila de encabezados y mapa nombre_columna_normalizado -> índice 0-based."""
    best_row = 0
    mapping: dict[str, int] = {}
    for ri, row in enumerate(ws.iter_rows(max_row=40, values_only=True), start=1):
        cells = [_norm_cell(c).upper() for c in row]
        if "FACTURA" in cells:
            mapping = {}
            for j, name in enumerate(cells):
                if not name:
                    continue
                key = re.sub(r"\s+", " ", name).strip()
                mapping[key] = j
            best_row = ri
            break
    return best_row, mapping


def _col(mapping: dict[str, int], *candidates: str) -> int | None:
    for c in candidates:
        k = c.upper().strip()
        if k in mapping:
            return mapping[k]
    for mk, idx in mapping.items():
        for c in candidates:
            if mk.replace(" ", "") == c.replace(" ", "").upper():
                return idx
    return None


def _row_vals(row: tuple[Any, ...], mapping: dict[str, int]) -> dict[str, Any]:
    def g(*names: str) -> Any:
        idx = _col(mapping, *names)
        if idx is None:
            return None
        if idx >= len(row):
            return None
        return row[idx]

    return {
        "mes": g("MES"),
        "asistencia": g("ASISTENCIA DE"),
        "planta": g("PLANTA"),
        "usuario": g("USUARIO"),
        "factura": g("FACTURA"),
        "po": g("PO"),
        "subtotal": g("SUBTOTAL"),
        "iva": g("IVA"),
        "total": g("TOTAL"),
        "fecha_factura": g("FECHA FACTURA", "FECHA FACTURA "),
        "fecha_venc": g("FECHA DE VENCIMIENTO", "FECHA DE VENCIMIENTO "),
        "estatus": g("ESTATUS"),
        "comentarios": g("COMENTARIOS"),
        "fecha_pago": g("FECHA DE PAGO", "FECHA DE PAGO "),
    }


def _skip_numero(num: str) -> bool:
    u = num.strip().upper()
    if not u or u in ("TOTAL", "NONE"):
        return True
    if u == "FALTA":
        return True
    if not re.search(r"\d", u):
        return True
    return False


def import_facturacion_excel(
    conn: sqlite3.Connection,
    content: bytes,
    *,
    anio_default: int,
    user_id: int | None,
    now: str,
) -> dict[str, Any]:
    bio = BytesIO(content)
    wb = openpyxl.load_workbook(bio, data_only=True)
    inserted = 0
    skipped = 0
    errors: list[str] = []

    for sheet_name in wb.sheetnames:
        key = str(sheet_name).strip().upper()
        if key == "NOTAS DE CREDITO":
            _import_notas_credito_sheet(conn, wb[sheet_name], anio_default, user_id, now)
            continue
        if key in ("DASHBOARD", "COMPLEMENTOS DE PAGO"):
            continue
        if key not in MES_SHEETS and parse_mes_num(key) is None:
            continue

        ws = wb[sheet_name]
        header_row, mapping = _find_header_row(ws)
        if not mapping or "FACTURA" not in [k.upper() for k in mapping.keys()]:
            continue

        sheet_mes_num = parse_mes_num(key)

        for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
            rv = _row_vals(tuple(row), mapping)
            num_raw = _norm_cell(rv["factura"])
            if _skip_numero(num_raw):
                skipped += 1
                continue

            usuario = _norm_cell(rv["usuario"])
            mes_txt = _norm_cell(rv["mes"])
            cli = _infer_cliente(usuario, mes_txt)
            if not cli:
                skipped += 1
                continue

            mes_fact = parse_mes_num(mes_txt) or sheet_mes_num or parse_mes_num(key)
            if mes_fact is None:
                skipped += 1
                continue

            asist = parse_mes_num(_norm_cell(rv["asistencia"]))

            est_raw = rv["estatus"]
            op_frag, pago_frag = split_operativo_y_pago(est_raw)
            tiene_fp = _fecha_sql(rv["fecha_pago"]) is not None
            est_pago = normalize_estatus_pago(pago_frag, tiene_fecha_pago=tiene_fp)

            op_norm = normalize_estatus_operativo(est_raw if op_frag is None else op_frag)
            if op_norm is None and est_raw:
                op_norm = normalize_estatus_operativo(est_raw)
            if op_norm is None and pago_frag == "PAGADO":
                operativo = "LISTO"
            else:
                operativo = coerce_operativo_o_default(op_norm or est_raw, fallback="EN COLA")

            extra_alerts = alertas_desde_texto_excel(_norm_cell(rv["comentarios"]), est_raw)
            if "NO PORTAL" in str(est_raw or "").upper():
                req_portal = 0
            elif operativo == "PORTAL":
                req_portal = 1
            else:
                req_portal = 0

            data: dict[str, Any] = {
                "mes": int(mes_fact),
                "anio": int(anio_default),
                "asistencia_mes": int(asist) if asist else None,
                "asistencia_anio": int(anio_default),
                "cliente": fix_cliente_name(cli),
                "planta_servicio": _norm_cell(rv["planta"]) or None,
                "usuario_contacto": usuario or None,
                "numero_factura": num_raw.upper() if num_raw.isalnum() else num_raw,
                "po_oc": _norm_cell(rv["po"]) or None,
                "requiere_portal": req_portal,
                "subtotal": _float_val(rv["subtotal"]),
                "iva": _float_val(rv["iva"]),
                "total": _float_val(rv["total"]),
                "fecha_factura": _fecha_sql(rv["fecha_factura"]),
                "fecha_vencimiento": _fecha_sql(rv["fecha_venc"]),
                "estatus_operativo": operativo,
                "estatus_pago": est_pago,
                "alertas": merge_alertas(extra_alerts, []),
                "comentarios": _norm_cell(rv["comentarios"]) or None,
            }
            # Portal: si no requiere, no marcamos error — ya en operativo
            try:
                insert_factura(conn, data, user_id=user_id, now=now)
                inserted += 1
            except sqlite3.IntegrityError:
                skipped += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{sheet_name} {num_raw}: {exc}")

    wb.close()
    return {"inserted": inserted, "skipped": skipped, "errors": errors[:50]}


def _import_notas_credito_sheet(
    conn: sqlite3.Connection,
    ws: Any,
    anio: int,
    user_id: int | None,
    now: str,
) -> None:
    from modules.facturacion.db import insert_nota_credito

    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        cliente = _norm_cell(row[0])
        num = row[1]
        fecha = row[2]
        com = row[3] if len(row) > 3 else None
        if not num:
            continue
        insert_nota_credito(
            conn,
            {
                "cliente": fix_cliente_name(cliente),
                "numero_nota": _norm_cell(num),
                "factura_id": None,
                "monto": None,
                "comentario": _norm_cell(com) if com else None,
                "fecha": _fecha_sql(fecha) or now[:10],
                "mes": None,
                "anio": anio,
            },
            user_id=user_id,
            now=now,
        )
