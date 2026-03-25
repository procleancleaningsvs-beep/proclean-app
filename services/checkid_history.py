"""
Historial de consultas CheckID (persistencia SQLite + extracción de campos para UI).
Listado global compartido; user_id queda en fila para auditoría.
La extracción usa body.data.resultado (misma forma que la vista CheckID / extractCheckidFields).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any


def extract_checkid_display_fields(data: Any) -> dict[str, str]:
    """
    Mapea el objeto `data` del JSON de respuesta (bajo `ok`) usando `data.resultado` como base.
    """
    wrapper = data if isinstance(data, dict) else {}
    r = wrapper.get("resultado")
    r = r if isinstance(r, dict) else {}

    rfc_block = r.get("rfc") if isinstance(r.get("rfc"), dict) else None
    curp_block = r.get("curp") if isinstance(r.get("curp"), dict) else None

    nombre = ""
    if rfc_block and rfc_block.get("razonSocial") is not None:
        nombre = str(rfc_block["razonSocial"]).strip()
    if not nombre and curp_block:
        parts = [
            curp_block.get("nombres"),
            curp_block.get("primerApellido"),
            curp_block.get("segundoApellido"),
        ]
        nombre = " ".join(
            str(p).strip() for p in parts if p is not None and str(p).strip() != ""
        )

    rfc_val = str(rfc_block["rfc"]).strip() if rfc_block and rfc_block.get("rfc") is not None else ""

    curp_val = ""
    if curp_block and curp_block.get("curp") is not None:
        curp_val = str(curp_block["curp"]).strip()
    if not curp_val and rfc_block and rfc_block.get("curp") is not None:
        curp_val = str(rfc_block["curp"]).strip()

    nss_block = r.get("nss") if isinstance(r.get("nss"), dict) else None
    nss_val = str(nss_block["nss"]).strip() if nss_block and nss_block.get("nss") is not None else ""

    reg_block = r.get("regimenFiscal") if isinstance(r.get("regimenFiscal"), dict) else None
    regimen_val = ""
    if reg_block and reg_block.get("regimenesFiscales") is not None:
        rf = reg_block["regimenesFiscales"]
        if isinstance(rf, (str, int, float, bool)):
            regimen_val = str(rf).strip()

    cp_block = r.get("codigoPostal") if isinstance(r.get("codigoPostal"), dict) else None
    cp_val = (
        str(cp_block["codigoPostal"]).strip()
        if cp_block and cp_block.get("codigoPostal") is not None
        else ""
    )

    e69 = r.get("estado69o69B") if isinstance(r.get("estado69o69B"), dict) else None
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
    # data = { codigoError, exitoso, resultado: { rfc, curp, nss, ... } }
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
