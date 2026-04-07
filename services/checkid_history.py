"""
Historial de consultas CheckID (persistencia SQLite + extracción de campos para UI).
Listado global compartido; user_id queda en fila para auditoría.
La extracción usa body.data.resultado (misma forma que la vista CheckID / extractCheckidFields).
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Any

from services.checkid_client import normalize_termino_busqueda


def _s(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def normalize_date_only(value: Any) -> str:
    """
    Devuelve solo la parte fecha (YYYY-MM-DD) si el valor trae hora (ISO, datetime SQL, etc.).
    """
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    if "T" in s:
        return s.split("T", 1)[0][:10]
    if " " in s:
        head = s.split()[0]
        if len(head) >= 10 and head[4] == "-" and head[7] == "-":
            return head[:10]
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    return s


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


def build_detail_bundle_from_resultado(r: dict[str, Any]) -> dict[str, Any]:
    """Estructura para modal «Ver todo»: secciones RFC e IMSS con claves estables."""
    rfc_block = r.get("rfc") if isinstance(r.get("rfc"), dict) else {}
    curp_block = r.get("curp") if isinstance(r.get("curp"), dict) else {}
    nss_block = r.get("nss") if isinstance(r.get("nss"), dict) else {}
    cp_block = r.get("codigoPostal") if isinstance(r.get("codigoPostal"), dict) else {}
    reg_block = r.get("regimenFiscal") if isinstance(r.get("regimenFiscal"), dict) else {}

    def rfc_get(*keys: str) -> str:
        for k in keys:
            if k in rfc_block and rfc_block.get(k) is not None:
                return _s(rfc_block.get(k))
        return ""

    regimen_val = ""
    if reg_block and reg_block.get("regimenesFiscales") is not None:
        rf = reg_block["regimenesFiscales"]
        if isinstance(rf, (str, int, float, bool)):
            regimen_val = str(rf).strip()

    cp_val = ""
    if cp_block and cp_block.get("codigoPostal") is not None:
        cp_val = _s(cp_block.get("codigoPostal"))
    if not cp_val:
        cp_val = rfc_get("codigoPostal", "cp", "codigo_postal")

    rfc_section = {
        "rfc": rfc_get("rfc"),
        "razon_social": rfc_get("razonSocial", "razon_social"),
        "codigo_postal": cp_val,
        "estado": rfc_get("estado", "estadoEntidad", "entidadFederativa"),
        "situacion": rfc_get("situacion", "situacionDelContribuyente"),
        "fiel_valida_hasta": rfc_get("fielValidaHasta", "fielValidaHastaEl", "fechaVencimientoFiel"),
        "rfc_representante": rfc_get("rfcRepresentanteLegal", "rfcRepresentante"),
        "curp_representante": rfc_get("curpRepresentanteLegal", "curpRepresentante"),
        "email_contacto": rfc_get("emailContacto", "email", "correo"),
        "regimen_fiscal": regimen_val or rfc_get("regimenFiscal", "regimen"),
    }

    def cq(*keys: str) -> str:
        for k in keys:
            if k in curp_block and curp_block.get(k) is not None:
                return _s(curp_block.get(k))
        return ""

    curp_val = cq("curp")
    if not curp_val:
        curp_val = rfc_get("curp")

    raw_fecha_imss = cq("fechaNacimiento", "fecha_nacimiento", "fechaNac")
    fecha_imss = normalize_date_only(raw_fecha_imss) or raw_fecha_imss

    imss_section = {
        "curp": curp_val,
        "fecha_nacimiento": fecha_imss,
        "nombre": cq("nombres", "nombre"),
        "apellido_paterno": cq("primerApellido", "apellidoPaterno"),
        "apellido_materno": cq("segundoApellido", "apellidoMaterno"),
        "sexo": cq("sexo", "genero"),
        "nacionalidad": cq("nacionalidad"),
        "entidad": cq("entidad", "entidadFederativa", "estadoNacimiento"),
        "nss": _s(nss_block.get("nss")) if nss_block else "",
    }

    return {"rfc_section": rfc_section, "imss_section": imss_section}


def extract_checkid_persist_bundle(data: Any) -> dict[str, Any]:
    """Campos para INSERT + detail_json (modal «Ver todo»)."""
    base = extract_checkid_display_fields(data)
    wrapper = data if isinstance(data, dict) else {}
    r = wrapper.get("resultado")
    r = r if isinstance(r, dict) else {}
    curp_block = r.get("curp") if isinstance(r.get("curp"), dict) else None

    ap_pat = ""
    ap_mat = ""
    nombres = ""
    fecha_nac = ""
    if curp_block:
        ap_pat = _s(curp_block.get("primerApellido"))
        ap_mat = _s(curp_block.get("segundoApellido"))
        nombres = _s(curp_block.get("nombres"))
        raw_fn = curp_block.get("fechaNacimiento")
        fecha_nac = normalize_date_only(raw_fn) if raw_fn is not None else ""
        if not fecha_nac and raw_fn is not None:
            fecha_nac = _s(raw_fn)

    detail_json: str | None = None
    if r:
        bundle = build_detail_bundle_from_resultado(r)
        detail_json = json.dumps(bundle, ensure_ascii=False)

    out = {
        **base,
        "apellido_paterno": ap_pat,
        "apellido_materno": ap_mat,
        "nombres": nombres,
        "fecha_nacimiento": fecha_nac,
        "detail_json": detail_json,
    }
    return out


def find_checkid_history_match_by_rfc_curp(db_path: str, term: str) -> int | None:
    """
    Si `term` normalizado coincide exactamente con el RFC o CURP de un registro exitoso
    (comparación con normalize_termino_busqueda en ambos lados), devuelve el id más reciente.
    """
    norm = normalize_termino_busqueda(term)
    if not norm:
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, rfc, curp FROM checkid_query_log
            WHERE ok = 1
            ORDER BY id DESC
            """,
        ).fetchall()
        for r in rows:
            rid = int(r["id"])
            rf = r["rfc"] or ""
            cr = r["curp"] or ""
            if normalize_termino_busqueda(rf) == norm or normalize_termino_busqueda(cr) == norm:
                return rid
        return None
    finally:
        conn.close()


def persist_checkid_query(db_path: str, user_id: int, termino_busqueda: str, response_body: dict[str, Any]) -> None:
    """
    Guarda una fila de historial a partir del cuerpo JSON devuelto al cliente
    (mismas claves que checkid_http_response / cliente CheckID).
    """
    ok = 1 if response_body.get("ok") else 0
    err_msg = (response_body.get("message") or "")[:2000]
    err_code = (response_body.get("error_code") or "")[:64]
    data = response_body.get("data")
    extracted = extract_checkid_persist_bundle(data) if ok and isinstance(data, dict) else {}

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO checkid_query_log (
                user_id, created_at, termino_busqueda, ok, error_code, error_message,
                rfc, curp, nombre, nss, regimen_fiscal, codigo_postal, estado_69,
                apellido_paterno, apellido_materno, nombres, fecha_nacimiento, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                extracted.get("apellido_paterno") or None,
                extracted.get("apellido_materno") or None,
                extracted.get("nombres") or None,
                extracted.get("fecha_nacimiento") or None,
                extracted.get("detail_json") or None,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _detail_bundle_from_row(d: dict[str, Any]) -> dict[str, Any] | None:
    raw = d.get("detail_json")
    if not raw or not isinstance(raw, str):
        return None
    try:
        b = json.loads(raw)
        return b if isinstance(b, dict) else None
    except json.JSONDecodeError:
        return None


def enrich_checkid_history_row(d: dict[str, Any]) -> None:
    """
    Completa columnas vacías desde detail_json y calcula campos de presentación.
    """
    bundle = _detail_bundle_from_row(d)
    rfc_sec: dict[str, Any] = {}
    imss: dict[str, Any] = {}
    if bundle:
        rfc_sec = bundle.get("rfc_section") or {}
        if not isinstance(rfc_sec, dict):
            rfc_sec = {}
        imss = bundle.get("imss_section") or {}
        if not isinstance(imss, dict):
            imss = {}

        def fill(col: str, *keys: str) -> None:
            if _s(d.get(col)):
                return
            for src in (imss, rfc_sec):
                if not isinstance(src, dict):
                    continue
                for k in keys:
                    if k in src and src.get(k) is not None:
                        v = _s(src.get(k))
                        if v:
                            d[col] = v
                            return

        fill("curp", "curp")
        fill("apellido_paterno", "apellido_paterno")
        fill("apellido_materno", "apellido_materno")
        fill("nombres", "nombre", "nombres")
        if not _s(d.get("fecha_nacimiento")):
            fn = imss.get("fecha_nacimiento") if isinstance(imss, dict) else None
            if fn is not None:
                d["fecha_nacimiento"] = normalize_date_only(fn) or _s(fn)
        if not _s(d.get("codigo_postal")) and rfc_sec.get("codigo_postal") is not None:
            d["codigo_postal"] = _s(rfc_sec.get("codigo_postal")) or d.get("codigo_postal")
        if not _s(d.get("nss")) and imss.get("nss") is not None:
            d["nss"] = _s(imss.get("nss")) or d.get("nss")

    razon = _s(rfc_sec.get("razon_social")) if isinstance(rfc_sec, dict) else ""
    n = _s(d.get("nombres"))
    ap = _s(d.get("apellido_paterno"))
    am = _s(d.get("apellido_materno"))
    legacy_nombre = _s(d.get("nombre"))

    if razon:
        nombre_completo = razon
    elif n or ap or am:
        nombre_completo = " ".join(x for x in (n, ap, am) if x)
    elif legacy_nombre:
        nombre_completo = legacy_nombre
    else:
        nombre_completo = ""

    d["nombre_completo"] = nombre_completo

    fn = d.get("fecha_nacimiento")
    if fn is not None and str(fn).strip() != "":
        d["fecha_nacimiento"] = normalize_date_only(fn) or str(fn).strip()


def _checkid_history_row_dict(r: sqlite3.Row) -> dict[str, Any]:
    d = {k: r[k] for k in r.keys()}
    enrich_checkid_history_row(d)
    blob_parts = [
        str(d.get("created_at") or ""),
        str(d.get("username") or ""),
        str(d.get("user_id") or ""),
        str(d.get("rfc") or ""),
        str(d.get("curp") or ""),
        str(d.get("nombre") or ""),
        str(d.get("nombre_completo") or ""),
        str(d.get("nss") or ""),
        str(d.get("regimen_fiscal") or ""),
        str(d.get("codigo_postal") or ""),
        str(d.get("estado_69") or ""),
        str(d.get("apellido_paterno") or ""),
        str(d.get("apellido_materno") or ""),
        str(d.get("nombres") or ""),
        str(d.get("fecha_nacimiento") or ""),
    ]
    d["search_blob"] = " ".join(blob_parts).casefold()
    return d


_CHECKID_HISTORY_LIST_SQL = """
            SELECT h.id, h.user_id, h.created_at, h.termino_busqueda, h.ok, h.error_code, h.error_message,
                   h.rfc, h.curp, h.nombre, h.nss, h.regimen_fiscal, h.codigo_postal, h.estado_69,
                   h.apellido_paterno, h.apellido_materno, h.nombres, h.fecha_nacimiento, h.detail_json,
                   u.username AS username
            FROM checkid_query_log h
            LEFT JOIN users u ON u.id = h.user_id
"""


def get_checkid_success_row_by_id(db_path: str, row_id: int) -> dict[str, Any] | None:
    """Una fila exitosa por id (misma forma que list_checkid_queries_global)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute(
            _CHECKID_HISTORY_LIST_SQL + " WHERE h.id = ? AND h.ok = 1 ",
            (int(row_id),),
        ).fetchone()
        return _checkid_history_row_dict(r) if r else None
    finally:
        conn.close()


def get_checkid_detail_bundle_for_row(db_path: str, row_id: int) -> dict[str, Any] | None:
    """Devuelve el JSON de detalle para modal «Ver todo» o None si no hay fila exitosa."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT detail_json FROM checkid_query_log WHERE id = ? AND ok = 1",
            (int(row_id),),
        ).fetchone()
        if not row:
            return None
        raw = row[0]
        if raw:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        return {"rfc_section": {}, "imss_section": {}}
    except json.JSONDecodeError:
        return {"rfc_section": {}, "imss_section": {}}
    finally:
        conn.close()


def list_checkid_queries_global(
    db_path: str, limit: int = 200, ensure_row_id: int | None = None
) -> list[dict[str, Any]]:
    """Últimas consultas CheckID exitosas de todos los usuarios (compartido), con username para auditoría."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            _CHECKID_HISTORY_LIST_SQL + """
            WHERE h.ok = 1
            ORDER BY h.id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        out: list[dict[str, Any]] = [_checkid_history_row_dict(r) for r in rows]
        if ensure_row_id is not None:
            have = {int(d["id"]) for d in out}
            if int(ensure_row_id) not in have:
                extra = get_checkid_success_row_by_id(db_path, int(ensure_row_id))
                if extra:
                    out.append(extra)
                    out.sort(key=lambda x: int(x["id"]), reverse=True)
        return out
    finally:
        conn.close()


def update_checkid_success_row_from_response(
    db_path: str,
    entry_id: int,
    termino_busqueda: str,
    response_body: dict[str, Any],
) -> bool:
    """
    Sobrescribe columnas de un registro exitoso con una nueva respuesta CheckID (misma fila, sin INSERT).
    """
    if not response_body.get("ok"):
        return False
    data = response_body.get("data")
    extracted = extract_checkid_persist_bundle(data) if isinstance(data, dict) else {}
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            UPDATE checkid_query_log SET
                termino_busqueda = ?,
                error_code = NULL,
                error_message = NULL,
                rfc = ?, curp = ?, nombre = ?, nss = ?, regimen_fiscal = ?, codigo_postal = ?, estado_69 = ?,
                apellido_paterno = ?, apellido_materno = ?, nombres = ?, fecha_nacimiento = ?, detail_json = ?
            WHERE id = ? AND ok = 1
            """,
            (
                (termino_busqueda or "")[:512],
                extracted.get("rfc") or None,
                extracted.get("curp") or None,
                extracted.get("nombre") or None,
                extracted.get("nss") or None,
                extracted.get("regimen") or None,
                extracted.get("cp") or None,
                extracted.get("estado69") or None,
                extracted.get("apellido_paterno") or None,
                extracted.get("apellido_materno") or None,
                extracted.get("nombres") or None,
                extracted.get("fecha_nacimiento") or None,
                extracted.get("detail_json") or None,
                int(entry_id),
            ),
        )
        conn.commit()
        return cur.rowcount > 0
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
