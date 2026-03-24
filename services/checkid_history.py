"""
Historial de consultas CheckID (persistencia SQLite + extracción de campos para UI).
Listado global compartido; user_id queda en fila para auditoría.
La extracción replica la lógica de templates/checkid.html (extractCheckidFields).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any


def _scalar_str(obj: Any, *keys: str) -> str:
    if not obj or not isinstance(obj, dict):
        return ""
    for k in keys:
        v = obj.get(k)
        if v is None or v == "":
            continue
        if isinstance(v, (dict, list)):
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def _nombre_desde_curp(curp_block: Any) -> str:
    if not curp_block or not isinstance(curp_block, dict):
        return ""
    parts = [
        _scalar_str(curp_block, "nombres", "Nombres"),
        _scalar_str(curp_block, "primerApellido", "PrimerApellido"),
        _scalar_str(curp_block, "segundoApellido", "SegundoApellido"),
    ]
    return " ".join(p for p in parts if p).strip()


def _format_regimenes_fiscales(val: Any) -> str:
    if val is None or val == "":
        return ""
    if isinstance(val, (str, int, float, bool)):
        return str(val).strip()
    if isinstance(val, list):
        parts: list[str] = []
        for item in val:
            if item is None:
                continue
            if isinstance(item, (str, int, float)):
                parts.append(str(item).strip())
                continue
            if not isinstance(item, dict):
                continue
            desc = _scalar_str(item, "descripcion", "Descripcion", "nombre", "Nombre")
            cod = _scalar_str(item, "codigo", "Codigo", "clave", "Clave")
            if desc and cod:
                parts.append(f"{desc} ({cod})")
            elif desc or cod:
                parts.append(desc or cod)
        return "; ".join(parts) if parts else ""
    if isinstance(val, dict):
        return _format_regimenes_fiscales([val])
    return ""


def _bloque_estado69(d: dict[str, Any]) -> dict[str, Any] | None:
    b = d.get("estado69o69B") or d.get("estado69O69B")
    return b if isinstance(b, dict) else None


def extract_checkid_display_fields(payload: Any) -> dict[str, str]:
    """Mapea el cuerpo `data` de una respuesta CheckID exitosa a strings legibles."""
    d = payload if isinstance(payload, dict) else {}
    rfc_block = d.get("rfc") if isinstance(d.get("rfc"), dict) else None
    curp_block = d.get("curp") if isinstance(d.get("curp"), dict) else None

    nombre = _scalar_str(rfc_block, "razonSocial", "RazonSocial") if rfc_block else ""
    if not nombre:
        nb = _nombre_desde_curp(curp_block)
        if nb:
            nombre = nb
    if not nombre:
        e69 = _bloque_estado69(d)
        det = e69.get("detalles") if e69 and isinstance(e69.get("detalles"), dict) else None
        if det:
            nombre = _scalar_str(det, "nombre", "Nombre")

    rfc_val = ""
    curp_val = ""
    if rfc_block:
        rfc_val = _scalar_str(rfc_block, "rfc", "RFC")
    if curp_block:
        curp_val = _scalar_str(curp_block, "curp", "CURP")
    if not curp_val and rfc_block:
        curp_val = _scalar_str(rfc_block, "curp", "CURP")

    nss_block = d.get("nss") if isinstance(d.get("nss"), dict) else None
    nss_val = _scalar_str(nss_block, "nss", "NSS") if nss_block else ""

    reg_block = d.get("regimenFiscal") if isinstance(d.get("regimenFiscal"), dict) else None
    regimen_val = ""
    if reg_block:
        rf = reg_block.get("regimenesFiscales")
        if rf is None:
            rf = reg_block.get("RegimenesFiscales")
        regimen_val = _format_regimenes_fiscales(rf)

    cp_block = d.get("codigoPostal") if isinstance(d.get("codigoPostal"), dict) else None
    cp_val = _scalar_str(cp_block, "codigoPostal", "CodigoPostal", "CP") if cp_block else ""

    e69 = _bloque_estado69(d)
    estado69 = "Sin información"
    if e69 is not None and "conProblema" in e69:
        cpv = e69.get("conProblema")
        if cpv is True:
            estado69 = "Con problema"
        elif cpv is False:
            estado69 = "Sin problema"
        else:
            estado69 = "Sin información"

    return {
        "nombre": nombre,
        "rfc": rfc_val,
        "curp": curp_val,
        "nss": nss_val,
        "regimen": regimen_val,
        "cp": cp_val,
        "estado69": estado69,
    }


def persist_checkid_query(db_path: str, user_id: int, termino_busqueda: str, response_body: dict[str, Any]) -> None:
    """
    Guarda una fila de historial a partir del cuerpo JSON devuelto al cliente
    (mismas claves que checkid_http_response / cliente CheckID).
    """
    ok = 1 if response_body.get("ok") else 0
    err_msg = (response_body.get("message") or "")[:2000]
    err_code = (response_body.get("error_code") or "")[:64]
    data = response_body.get("data")
    extracted = extract_checkid_display_fields(data) if ok and isinstance(data, dict) else {}

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO checkid_query_log (
                user_id, created_at, termino_busqueda, ok, error_code, error_message,
                rfc, curp, nombre, nss, regimen_fiscal, codigo_postal, estado_69
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                created_at,
                (termino_busqueda or "")[:512],
                ok,
                err_code or None,
                err_msg or None,
                extracted.get("rfc") or None,
                extracted.get("curp") or None,
                extracted.get("nombre") or None,
                extracted.get("nss") or None,
                extracted.get("regimen") or None,
                extracted.get("cp") or None,
                extracted.get("estado69") or None,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_checkid_queries_global(db_path: str, limit: int = 200) -> list[dict[str, Any]]:
    """Últimas consultas CheckID de todos los usuarios (compartido), con username para auditoría."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT h.id, h.user_id, h.created_at, h.termino_busqueda, h.ok, h.error_code, h.error_message,
                   h.rfc, h.curp, h.nombre, h.nss, h.regimen_fiscal, h.codigo_postal, h.estado_69,
                   u.username AS username
            FROM checkid_query_log h
            LEFT JOIN users u ON u.id = h.user_id
            ORDER BY h.id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            blob_parts = [
                str(d.get("created_at") or ""),
                str(d.get("termino_busqueda") or ""),
                str(d.get("username") or ""),
                str(d.get("user_id") or ""),
                str(d.get("rfc") or ""),
                str(d.get("curp") or ""),
                str(d.get("nombre") or ""),
                str(d.get("nss") or ""),
                str(d.get("regimen_fiscal") or ""),
                str(d.get("codigo_postal") or ""),
                str(d.get("estado_69") or ""),
                str(d.get("error_message") or ""),
                str(d.get("error_code") or ""),
                "éxito" if d.get("ok") else "error",
            ]
            d["search_blob"] = " ".join(blob_parts).casefold()
            out.append(d)
        return out
    finally:
        conn.close()


def delete_checkid_query_by_id(db_path: str, entry_id: int) -> bool:
    """Elimina una fila de historial CheckID por id. Devuelve True si se borró alguna fila."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("DELETE FROM checkid_query_log WHERE id = ?", (int(entry_id),))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
