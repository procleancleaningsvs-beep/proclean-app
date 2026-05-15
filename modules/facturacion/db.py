from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from modules.facturacion.config import cliente_requiere_po_oc
from modules.facturacion.normalize import (
    compute_auto_alertas,
    dump_alertas_json,
    parse_alertas_json,
)


def ensure_facturacion_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS facturacion_facturas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mes INTEGER NOT NULL,
            anio INTEGER NOT NULL,
            asistencia_mes INTEGER,
            asistencia_anio INTEGER,
            cliente TEXT NOT NULL,
            planta_servicio TEXT,
            concepto_servicio TEXT,
            usuario_contacto TEXT,
            responsable_interno TEXT,
            numero_factura TEXT NOT NULL,
            po_oc TEXT,
            requiere_po_oc INTEGER NOT NULL DEFAULT 0,
            requiere_portal INTEGER NOT NULL DEFAULT 0,
            subtotal REAL,
            iva REAL,
            total REAL,
            fecha_factura TEXT,
            fecha_vencimiento TEXT,
            estatus_operativo TEXT NOT NULL,
            estatus_pago TEXT NOT NULL DEFAULT 'PENDIENTE',
            alertas_json TEXT NOT NULL DEFAULT '[]',
            comentarios TEXT,
            factura_original_id INTEGER,
            factura_reemplazada_por_id INTEGER,
            refacturacion_motivo TEXT,
            refacturacion_fecha TEXT,
            refacturacion_por INTEGER,
            es_factura_activa INTEGER NOT NULL DEFAULT 1,
            fecha_creacion TEXT NOT NULL,
            fecha_actualizacion TEXT NOT NULL,
            actualizado_por INTEGER,
            creado_por INTEGER,
            FOREIGN KEY (factura_original_id) REFERENCES facturacion_facturas(id),
            FOREIGN KEY (factura_reemplazada_por_id) REFERENCES facturacion_facturas(id)
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_facturacion_factura_activa
        ON facturacion_facturas (numero_factura, cliente, mes, anio)
        WHERE es_factura_activa = 1
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_facturacion_facturas_mes_anio
        ON facturacion_facturas (anio, mes)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_facturacion_facturas_cliente
        ON facturacion_facturas (cliente)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS facturacion_adjuntos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            factura_id INTEGER NOT NULL,
            tipo TEXT NOT NULL CHECK (tipo IN ('pdf', 'xml')),
            stored_filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_hash TEXT,
            original_name TEXT,
            created_at TEXT NOT NULL,
            created_by INTEGER,
            FOREIGN KEY (factura_id) REFERENCES facturacion_facturas(id),
            UNIQUE (factura_id, tipo)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS facturacion_archivos_huerfanos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stored_path TEXT NOT NULL,
            original_name TEXT NOT NULL,
            ext TEXT NOT NULL,
            file_hash TEXT,
            detected_numero TEXT,
            created_at TEXT NOT NULL,
            created_by INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS facturacion_eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            factura_id INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            detalle_json TEXT,
            user_id INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (factura_id) REFERENCES facturacion_facturas(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS facturacion_notas_credito (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mes INTEGER,
            anio INTEGER,
            cliente TEXT,
            numero_nota TEXT,
            factura_id INTEGER,
            monto REAL,
            comentario TEXT,
            fecha TEXT NOT NULL,
            created_at TEXT NOT NULL,
            created_by INTEGER,
            FOREIGN KEY (factura_id) REFERENCES facturacion_facturas(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS facturacion_import_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            created_by INTEGER,
            original_filename TEXT,
            summary_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS facturacion_razon_social_map (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            razon_social TEXT NOT NULL,
            cliente_principal TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(razon_social)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS facturacion_cliente_credito (
            cliente_principal TEXT NOT NULL PRIMARY KEY,
            dias_credito INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """
    )
    _migrate_facturacion_columns(conn)


def _table_column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate_facturacion_columns(conn: sqlite3.Connection) -> None:
    cols = _table_column_names(conn, "facturacion_facturas")
    if "razon_social" not in cols:
        conn.execute("ALTER TABLE facturacion_facturas ADD COLUMN razon_social TEXT")


def _row_factura(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["alertas"] = parse_alertas_json(d.get("alertas_json"))
    return d


def log_evento(
    conn: sqlite3.Connection,
    *,
    factura_id: int,
    tipo: str,
    detalle: dict[str, Any] | None,
    user_id: int | None,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO facturacion_eventos (factura_id, tipo, detalle_json, user_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            factura_id,
            tipo,
            json.dumps(detalle or {}, ensure_ascii=False, default=str),
            user_id,
            created_at,
        ),
    )


def list_eventos_for_factura(conn: sqlite3.Connection, factura_id: int, limit: int = 80) -> list[dict[str, Any]]:
    cur = conn.execute(
        """
        SELECT e.*, u.username
        FROM facturacion_eventos e
        LEFT JOIN users u ON u.id = e.user_id
        WHERE e.factura_id = ?
        ORDER BY e.created_at DESC
        LIMIT ?
        """,
        (factura_id, int(limit)),
    )
    out: list[dict[str, Any]] = []
    for r in cur.fetchall():
        d = dict(r)
        try:
            d["detalle"] = json.loads(d.get("detalle_json") or "{}")
        except json.JSONDecodeError:
            d["detalle"] = {}
        out.append(d)
    return out


def adjuntos_for_facturas(conn: sqlite3.Connection, ids: list[int]) -> dict[int, dict[str, Any]]:
    if not ids:
        return {}
    qmarks = ",".join("?" * len(ids))
    cur = conn.execute(
        f"""
        SELECT factura_id, tipo, file_path, original_name
        FROM facturacion_adjuntos
        WHERE factura_id IN ({qmarks})
        """,
        ids,
    )
    m: dict[int, dict[str, Any]] = {}
    for r in cur.fetchall():
        fid = int(r["factura_id"])
        slot = m.setdefault(fid, {"pdf": None, "xml": None})
        slot[str(r["tipo"])] = {"file_path": r["file_path"], "original_name": r["original_name"]}
    return m


def refresh_archivo_alerta(conn: sqlite3.Connection, factura_id: int, *, now: str | None = None) -> None:
    row = conn.execute("SELECT * FROM facturacion_facturas WHERE id = ?", (factura_id,)).fetchone()
    if not row:
        return
    adj = adjuntos_for_facturas(conn, [factura_id]).get(factura_id, {})
    tiene_pdf = adj.get("pdf") is not None
    tiene_xml = adj.get("xml") is not None
    manual = [a for a in parse_alertas_json(row["alertas_json"]) if a != "ARCHIVO FALTANTE" and a != "SIN PO/OC"]
    auto = compute_auto_alertas(
        cliente=str(row["cliente"]),
        po_oc=row["po_oc"],
        tiene_pdf=tiene_pdf,
        tiene_xml=tiene_xml,
        estatus_operativo=str(row["estatus_operativo"] or ""),
        numero_factura=str(row["numero_factura"] or ""),
        manual_alertas=manual,
    )
    ts = now or str(row["fecha_actualizacion"] or "")
    conn.execute(
        "UPDATE facturacion_facturas SET alertas_json = ?, fecha_actualizacion = ? WHERE id = ?",
        (dump_alertas_json(auto), ts, factura_id),
    )


def list_facturas_filtradas(
    conn: sqlite3.Connection,
    *,
    mes: int | None,
    anio: int | None,
    cliente: str | None,
    cliente_eq: str | None = None,
    estatus_operativo: str | None,
    estatus_pago: str | None,
    alerta: str | None,
    q_numero: str | None,
    q_po: str | None,
    solo_activas: bool = True,
    limit: int = 500,
) -> list[dict[str, Any]]:
    wh: list[str] = ["1=1"]
    args: list[Any] = []
    if solo_activas:
        wh.append("es_factura_activa = 1")
    if mes is not None:
        wh.append("mes = ?")
        args.append(int(mes))
    if anio is not None:
        wh.append("anio = ?")
        args.append(int(anio))
    if cliente_eq:
        wh.append("cliente = ?")
        args.append(cliente_eq.strip())
    elif cliente:
        wh.append("UPPER(cliente) LIKE UPPER(?)")
        args.append(f"%{cliente.strip()}%")
    if estatus_operativo:
        wh.append("estatus_operativo = ?")
        args.append(estatus_operativo.strip())
    if estatus_pago:
        wh.append("estatus_pago = ?")
        args.append(estatus_pago.strip())
    if alerta:
        wh.append("alertas_json LIKE ?")
        args.append(f'%"{alerta.strip().upper()}"%')
    if q_numero:
        wh.append("numero_factura LIKE ?")
        args.append(f"%{q_numero.strip()}%")
    if q_po:
        wh.append("po_oc LIKE ?")
        args.append(f"%{q_po.strip()}%")
    sql = f"""
        SELECT * FROM facturacion_facturas
        WHERE {' AND '.join(wh)}
        ORDER BY anio DESC, mes DESC, cliente ASC, numero_factura ASC
        LIMIT ?
    """
    args.append(int(limit))
    rows = conn.execute(sql, args).fetchall()
    ids = [int(r["id"]) for r in rows]
    adjm = adjuntos_for_facturas(conn, ids)
    out = []
    for r in rows:
        d = _row_factura(r)
        d["_adjuntos"] = adjm.get(int(r["id"]), {"pdf": None, "xml": None})
        out.append(d)
    return out


def get_factura(conn: sqlite3.Connection, fid: int) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM facturacion_facturas WHERE id = ?", (fid,)).fetchone()
    if not r:
        return None
    d = _row_factura(r)
    d["_adjuntos"] = adjuntos_for_facturas(conn, [fid]).get(fid, {"pdf": None, "xml": None})
    return d


def find_factura_activa_por_numero_en_texto(
    conn: sqlite3.Connection, texto: str, *, anio: int | None = None, mes: int | None = None
) -> int | None:
    """Encuentra factura activa cuyo número aparece en texto (nombre archivo)."""
    t = str(texto or "").upper()
    wh = ["es_factura_activa = 1"]
    args: list[Any] = []
    if anio is not None:
        wh.append("anio = ?")
        args.append(int(anio))
    if mes is not None:
        wh.append("mes = ?")
        args.append(int(mes))
    rows = conn.execute(
        f"SELECT id, numero_factura FROM facturacion_facturas WHERE {' AND '.join(wh)}", args
    ).fetchall()
    best: int | None = None
    best_len = 0
    for r in rows:
        num = str(r["numero_factura"] or "").strip().upper()
        if len(num) < 3:
            continue
        if num in t and len(num) >= best_len:
            best = int(r["id"])
            best_len = len(num)
    return best


def insert_factura(
    conn: sqlite3.Connection,
    data: dict[str, Any],
    *,
    user_id: int | None,
    now: str,
) -> int:
    req_po = 1 if cliente_requiere_po_oc(str(data.get("cliente") or "")) else 0
    if "requiere_po_oc" in data and data["requiere_po_oc"] is not None:
        req_po = 1 if int(data["requiere_po_oc"]) else 0
    req_portal = 1 if int(data.get("requiere_portal") or 0) else 0
    alertas_manual = data.get("alertas") if isinstance(data.get("alertas"), list) else parse_alertas_json(
        data.get("alertas_json")
    )
    auto = compute_auto_alertas(
        cliente=str(data.get("cliente") or ""),
        po_oc=data.get("po_oc"),
        tiene_pdf=False,
        tiene_xml=False,
        estatus_operativo=str(data.get("estatus_operativo") or ""),
        numero_factura=str(data.get("numero_factura") or ""),
        manual_alertas=alertas_manual,
    )
    cur = conn.execute(
        """
        INSERT INTO facturacion_facturas (
            mes, anio, asistencia_mes, asistencia_anio, cliente, razon_social, planta_servicio, concepto_servicio,
            usuario_contacto, responsable_interno, numero_factura, po_oc, requiere_po_oc, requiere_portal,
            subtotal, iva, total, fecha_factura, fecha_vencimiento,
            estatus_operativo, estatus_pago, alertas_json, comentarios,
            factura_original_id, factura_reemplazada_por_id,
            refacturacion_motivo, refacturacion_fecha, refacturacion_por,
            es_factura_activa, fecha_creacion, fecha_actualizacion, actualizado_por, creado_por
        ) VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            int(data["mes"]),
            int(data["anio"]),
            data.get("asistencia_mes"),
            data.get("asistencia_anio"),
            str(data["cliente"]).strip(),
            (data.get("razon_social") or None) and str(data.get("razon_social")).strip() or None,
            (data.get("planta_servicio") or None) and str(data.get("planta_servicio")).strip() or None,
            (data.get("concepto_servicio") or None) and str(data.get("concepto_servicio")).strip() or None,
            (data.get("usuario_contacto") or None) and str(data.get("usuario_contacto")).strip() or None,
            (data.get("responsable_interno") or None) and str(data.get("responsable_interno")).strip() or None,
            str(data["numero_factura"]).strip(),
            (data.get("po_oc") or None) and str(data.get("po_oc")).strip() or None,
            req_po,
            req_portal,
            data.get("subtotal"),
            data.get("iva"),
            data.get("total"),
            data.get("fecha_factura"),
            data.get("fecha_vencimiento"),
            str(data["estatus_operativo"]).strip(),
            str(data.get("estatus_pago") or "PENDIENTE").strip(),
            dump_alertas_json(auto),
            (data.get("comentarios") or None) and str(data.get("comentarios")).strip() or None,
            data.get("factura_original_id"),
            data.get("factura_reemplazada_por_id"),
            data.get("refacturacion_motivo"),
            data.get("refacturacion_fecha"),
            data.get("refacturacion_por"),
            int(data.get("es_factura_activa", 1)),
            now,
            now,
            user_id,
            user_id,
        ),
    )
    fid = int(cur.lastrowid)
    log_evento(conn, factura_id=fid, tipo="CREAR", detalle={"numero": data.get("numero_factura")}, user_id=user_id, created_at=now)
    for ev in data.get("_extra_eventos") or []:
        if not isinstance(ev, dict):
            continue
        t = str(ev.get("tipo") or "").strip()
        if not t:
            continue
        det = ev.get("detalle")
        if not isinstance(det, dict):
            det = {} if det is None else {"info": det}
        log_evento(conn, factura_id=fid, tipo=t, detalle=det, user_id=user_id, created_at=now)
    return fid


def update_factura(conn: sqlite3.Connection, fid: int, data: dict[str, Any], *, user_id: int | None, now: str) -> bool:
    row = conn.execute("SELECT * FROM facturacion_facturas WHERE id = ?", (fid,)).fetchone()
    if not row:
        return False
    req_po = 1 if cliente_requiere_po_oc(str(data.get("cliente", row["cliente"]) or "")) else 0
    if data.get("requiere_po_oc") is not None:
        req_po = 1 if int(data["requiere_po_oc"]) else 0
    req_portal = int(data.get("requiere_portal", row["requiere_portal"]) or 0)
    alertas_in = data.get("alertas")
    if isinstance(alertas_in, list):
        manual = alertas_in
    else:
        manual = parse_alertas_json(row["alertas_json"])
    adj = adjuntos_for_facturas(conn, [fid]).get(fid, {})
    op_eff = str(data.get("estatus_operativo", row["estatus_operativo"]) or "")
    num_eff = str(data.get("numero_factura", row["numero_factura"]) or "")
    auto = compute_auto_alertas(
        cliente=str(data.get("cliente", row["cliente"])),
        po_oc=data.get("po_oc", row["po_oc"]),
        tiene_pdf=adj.get("pdf") is not None,
        tiene_xml=adj.get("xml") is not None,
        estatus_operativo=op_eff,
        numero_factura=num_eff,
        manual_alertas=manual,
    )
    conn.execute(
        """
        UPDATE facturacion_facturas SET
            mes = ?, anio = ?, asistencia_mes = ?, asistencia_anio = ?,
            cliente = ?, razon_social = ?, planta_servicio = ?, concepto_servicio = ?,
            usuario_contacto = ?, responsable_interno = ?,
            numero_factura = ?, po_oc = ?, requiere_po_oc = ?, requiere_portal = ?,
            subtotal = ?, iva = ?, total = ?,
            fecha_factura = ?, fecha_vencimiento = ?,
            estatus_operativo = ?, estatus_pago = ?,
            alertas_json = ?, comentarios = ?,
            fecha_actualizacion = ?, actualizado_por = ?
        WHERE id = ?
        """,
        (
            int(data.get("mes", row["mes"])),
            int(data.get("anio", row["anio"])),
            data.get("asistencia_mes", row["asistencia_mes"]),
            data.get("asistencia_anio", row["asistencia_anio"]),
            str(data.get("cliente", row["cliente"])).strip(),
            ((str(data["razon_social"]).strip() or None) if "razon_social" in data else row["razon_social"]),
            data.get("planta_servicio", row["planta_servicio"]),
            data.get("concepto_servicio", row["concepto_servicio"]),
            data.get("usuario_contacto", row["usuario_contacto"]),
            data.get("responsable_interno", row["responsable_interno"]),
            str(data.get("numero_factura", row["numero_factura"])).strip(),
            data.get("po_oc", row["po_oc"]),
            req_po,
            1 if req_portal else 0,
            data.get("subtotal", row["subtotal"]),
            data.get("iva", row["iva"]),
            data.get("total", row["total"]),
            data.get("fecha_factura", row["fecha_factura"]),
            data.get("fecha_vencimiento", row["fecha_vencimiento"]),
            str(data.get("estatus_operativo", row["estatus_operativo"])).strip(),
            str(data.get("estatus_pago", row["estatus_pago"])).strip(),
            dump_alertas_json(auto),
            data.get("comentarios", row["comentarios"]),
            now,
            user_id,
            fid,
        ),
    )
    log_evento(conn, factura_id=fid, tipo="EDITAR", detalle={"campos": list(data.keys())}, user_id=user_id, created_at=now)
    return True


def delete_factura_soft(conn: sqlite3.Connection, fid: int, *, user_id: int | None, now: str) -> bool:
    r = conn.execute("SELECT id FROM facturacion_facturas WHERE id = ? AND es_factura_activa = 1", (fid,)).fetchone()
    if not r:
        return False
    conn.execute(
        "UPDATE facturacion_facturas SET es_factura_activa = 0, fecha_actualizacion = ?, actualizado_por = ? WHERE id = ?",
        (now, user_id, fid),
    )
    log_evento(conn, factura_id=fid, tipo="ELIMINAR", detalle={}, user_id=user_id, created_at=now)
    return True


def refacturar(
    conn: sqlite3.Connection,
    old_id: int,
    nuevo: dict[str, Any],
    *,
    motivo: str,
    user_id: int | None,
    now: str,
) -> int | None:
    old = conn.execute("SELECT * FROM facturacion_facturas WHERE id = ?", (old_id,)).fetchone()
    if not old or not int(old["es_factura_activa"]):
        return None
    orig_id = int(old["factura_original_id"] or old["id"])
    n = dict(nuevo)
    n["factura_original_id"] = orig_id
    n["es_factura_activa"] = 1
    n["refacturacion_motivo"] = None
    n["refacturacion_fecha"] = None
    n["refacturacion_por"] = None
    new_id = insert_factura(conn, n, user_id=user_id, now=now)
    conn.execute(
        "UPDATE facturacion_facturas SET es_factura_activa = 0, factura_reemplazada_por_id = ?, fecha_actualizacion = ?, actualizado_por = ? WHERE id = ?",
        (new_id, now, user_id, old_id),
    )
    conn.execute(
        """
        UPDATE facturacion_facturas SET
            refacturacion_motivo = ?, refacturacion_fecha = ?, refacturacion_por = ?
        WHERE id = ?
        """,
        (motivo.strip(), now, user_id, new_id),
    )
    manual = parse_alertas_json(old["alertas_json"])
    manual = [a for a in manual if a != "REFACTURAR"]
    conn.execute(
        "UPDATE facturacion_facturas SET alertas_json = ?, fecha_actualizacion = ? WHERE id = ?",
        (dump_alertas_json(manual + ["REFACTURAR"]), now, old_id),
    )
    log_evento(
        conn,
        factura_id=old_id,
        tipo="REFACTURACION_SALIENTE",
        detalle={"nueva_factura_id": new_id, "motivo": motivo},
        user_id=user_id,
        created_at=now,
    )
    log_evento(
        conn,
        factura_id=new_id,
        tipo="REFACTURACION_ENTRANTE",
        detalle={"anterior_id": old_id, "motivo": motivo},
        user_id=user_id,
        created_at=now,
    )
    return new_id


def dashboard_stats(
    conn: sqlite3.Connection,
    *,
    mes: int,
    anio: int,
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT * FROM facturacion_facturas
        WHERE es_factura_activa = 1 AND mes = ? AND anio = ?
        """,
        (mes, anio),
    ).fetchall()
    return _stats_from_rows(rows)


def dashboard_stats_anual(conn: sqlite3.Connection, *, anio: int) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT * FROM facturacion_facturas
        WHERE es_factura_activa = 1 AND anio = ?
        """,
        (anio,),
    ).fetchall()
    return _stats_from_rows(rows)


def _stats_from_rows(rows: list[sqlite3.Row]) -> dict[str, Any]:
    total = len(rows)
    listo = sum(1 for r in rows if r["estatus_operativo"] == "LISTO")
    portal = sum(1 for r in rows if r["estatus_operativo"] == "PORTAL")
    pnr = sum(1 for r in rows if r["estatus_operativo"] == "PENDIENTE NR")
    pendientes = sum(1 for r in rows if r["estatus_operativo"] != "LISTO")
    pagadas = sum(1 for r in rows if r["estatus_pago"] == "PAGADO")
    refacts = sum(1 for r in rows if r["factura_original_id"])
    urgentes = 0
    errores = 0
    sin_po = 0
    for r in rows:
        als = parse_alertas_json(r["alertas_json"])
        if "URGENTE" in als:
            urgentes += 1
        if "ERROR" in als:
            errores += 1
        if "SIN PO/OC" in als:
            sin_po += 1
    atoradas = sum(
        1
        for r in rows
        if any(
            x in parse_alertas_json(r["alertas_json"])
            for x in ("URGENTE", "ERROR", "REFACTURAR", "FALTA COMPROBANTE")
        )
        or ("SIN PO/OC" in parse_alertas_json(r["alertas_json"]))
    )
    avance = (listo / total * 100.0) if total else 0.0
    por_cliente: dict[str, int] = {}
    por_op: dict[str, int] = {}
    for r in rows:
        c = str(r["cliente"] or "—")
        por_cliente[c] = por_cliente.get(c, 0) + 1
        op = str(r["estatus_operativo"] or "")
        por_op[op] = por_op.get(op, 0) + 1
    return {
        "total": total,
        "listo": listo,
        "pendientes": pendientes,
        "portal": portal,
        "pendiente_nr": pnr,
        "pagadas": pagadas,
        "refacturaciones": refacts,
        "urgentes": urgentes,
        "errores": errores,
        "sin_po_oc": sin_po,
        "atoradas": atoradas,
        "avance_pct": round(avance, 2),
        "por_cliente": por_cliente,
        "por_operativo": por_op,
    }


def list_huerfanos(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute("SELECT * FROM facturacion_archivos_huerfanos ORDER BY created_at DESC")
    return [dict(r) for r in cur.fetchall()]


def insert_huerfano(
    conn: sqlite3.Connection,
    *,
    stored_path: str,
    original_name: str,
    ext: str,
    file_hash: str | None,
    detected_numero: str | None,
    user_id: int,
    now: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO facturacion_archivos_huerfanos
        (stored_path, original_name, ext, file_hash, detected_numero, created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (stored_path, original_name, ext.lower(), file_hash, detected_numero, now, user_id),
    )
    return int(cur.lastrowid)


def delete_huerfano(conn: sqlite3.Connection, hid: int) -> bool:
    cur = conn.execute("DELETE FROM facturacion_archivos_huerfanos WHERE id = ?", (hid,))
    return cur.rowcount > 0


def get_huerfano(conn: sqlite3.Connection, hid: int) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM facturacion_archivos_huerfanos WHERE id = ?", (hid,)).fetchone()
    return dict(r) if r else None


def upsert_adjunto(
    conn: sqlite3.Connection,
    *,
    factura_id: int,
    tipo: str,
    stored_filename: str,
    file_path: str,
    file_hash: str | None,
    original_name: str,
    user_id: int | None,
    now: str,
) -> None:
    conn.execute(
        """
        INSERT INTO facturacion_adjuntos (factura_id, tipo, stored_filename, file_path, file_hash, original_name, created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(factura_id, tipo) DO UPDATE SET
            stored_filename = excluded.stored_filename,
            file_path = excluded.file_path,
            file_hash = excluded.file_hash,
            original_name = excluded.original_name,
            created_at = excluded.created_at,
            created_by = excluded.created_by
        """,
        (factura_id, tipo, stored_filename, file_path, file_hash, original_name, now, user_id),
    )
    refresh_archivo_alerta(conn, factura_id, now=now)
    log_evento(
        conn,
        factura_id=factura_id,
        tipo="ADJUNTO",
        detalle={"tipo": tipo, "archivo": original_name},
        user_id=user_id,
        created_at=now,
    )


def list_notas_credito(conn: sqlite3.Connection, limit: int = 200) -> list[dict[str, Any]]:
    cur = conn.execute(
        """
        SELECT n.*, f.numero_factura AS factura_numero
        FROM facturacion_notas_credito n
        LEFT JOIN facturacion_facturas f ON f.id = n.factura_id
        ORDER BY n.created_at DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    return [dict(r) for r in cur.fetchall()]


def insert_nota_credito(
    conn: sqlite3.Connection,
    data: dict[str, Any],
    *,
    user_id: int | None,
    now: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO facturacion_notas_credito
        (mes, anio, cliente, numero_nota, factura_id, monto, comentario, fecha, created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data.get("mes"),
            data.get("anio"),
            data.get("cliente"),
            data.get("numero_nota"),
            data.get("factura_id"),
            data.get("monto"),
            data.get("comentario"),
            str(data["fecha"]),
            now,
            user_id,
        ),
    )
    return int(cur.lastrowid)


def distinct_clientes(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute(
        """
        SELECT c FROM (
            SELECT DISTINCT TRIM(cliente) AS c FROM facturacion_facturas
            WHERE es_factura_activa = 1 AND cliente IS NOT NULL AND TRIM(cliente) != ''
            UNION
            SELECT DISTINCT TRIM(cliente_principal) AS c FROM facturacion_cliente_credito
            WHERE TRIM(cliente_principal) != ''
            UNION
            SELECT DISTINCT TRIM(cliente_principal) AS c FROM facturacion_razon_social_map
            WHERE TRIM(cliente_principal) != ''
        ) ORDER BY c ASC
        """
    )
    return [str(r[0]) for r in cur.fetchall() if r[0]]


def insert_import_log(
    conn: sqlite3.Connection,
    summary: dict[str, Any],
    *,
    user_id: int | None,
    now: str,
    original_filename: str | None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO facturacion_import_logs (created_at, created_by, original_filename, summary_json)
        VALUES (?, ?, ?, ?)
        """,
        (now, user_id, original_filename, json.dumps(summary, ensure_ascii=False, default=str)),
    )
    return int(cur.lastrowid)


def get_import_log(conn: sqlite3.Connection, log_id: int) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM facturacion_import_logs WHERE id = ?", (int(log_id),)).fetchone()
    if not r:
        return None
    d = dict(r)
    try:
        d["summary"] = json.loads(d.get("summary_json") or "{}")
    except json.JSONDecodeError:
        d["summary"] = {}
    return d


def get_latest_import_log(conn: sqlite3.Connection) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM facturacion_import_logs ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        return None
    return get_import_log(conn, int(r["id"]))


def list_razon_social_map(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute(
        "SELECT * FROM facturacion_razon_social_map ORDER BY cliente_principal ASC, razon_social ASC"
    )
    return [dict(x) for x in cur.fetchall()]


def upsert_razon_social_map(
    conn: sqlite3.Connection,
    *,
    razon_social: str,
    cliente_principal: str,
    now: str,
    row_id: int | None = None,
) -> None:
    rz = (razon_social or "").strip()
    cp = (cliente_principal or "").strip()
    if not rz or not cp:
        raise ValueError("razon_social y cliente_principal son obligatorios")
    if row_id:
        conn.execute(
            """
            UPDATE facturacion_razon_social_map
            SET razon_social = ?, cliente_principal = ?, updated_at = ?
            WHERE id = ?
            """,
            (rz, cp, now, int(row_id)),
        )
        return
    conn.execute(
        """
        INSERT INTO facturacion_razon_social_map (razon_social, cliente_principal, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(razon_social) DO UPDATE SET
            cliente_principal = excluded.cliente_principal,
            updated_at = excluded.updated_at
        """,
        (rz, cp, now, now),
    )


def delete_razon_social_map(conn: sqlite3.Connection, row_id: int) -> None:
    conn.execute("DELETE FROM facturacion_razon_social_map WHERE id = ?", (int(row_id),))


def list_cliente_credito(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute(
        "SELECT * FROM facturacion_cliente_credito ORDER BY cliente_principal ASC"
    )
    return [dict(x) for x in cur.fetchall()]


def upsert_cliente_credito(
    conn: sqlite3.Connection,
    *,
    cliente_principal: str,
    dias_credito: int,
    now: str,
) -> None:
    cp = (cliente_principal or "").strip()
    if not cp:
        raise ValueError("cliente_principal es obligatorio")
    d = max(0, int(dias_credito))
    conn.execute(
        """
        INSERT INTO facturacion_cliente_credito (cliente_principal, dias_credito, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(cliente_principal) DO UPDATE SET
            dias_credito = excluded.dias_credito,
            updated_at = excluded.updated_at
        """,
        (cp, d, now),
    )


def delete_cliente_credito(conn: sqlite3.Connection, cliente_principal: str) -> None:
    cp = (cliente_principal or "").strip()
    if cp:
        conn.execute("DELETE FROM facturacion_cliente_credito WHERE cliente_principal = ?", (cp,))


def apply_automatic_fechas_from_adjunto(
    conn: sqlite3.Connection,
    *,
    factura_id: int,
    file_bytes: bytes,
    ext: str,
    user_id: int | None,
    now: str,
) -> bool:
    """Si se detecta fecha de emisión en PDF/XML, actualiza factura y vencimiento según días de crédito."""
    from modules.facturacion.cliente_catalog import (
        add_days_to_iso_date,
        dias_credito_para_cliente,
        load_catalog_maps,
    )
    from modules.facturacion.doc_fechas import extract_fecha_emision_from_bytes

    fe = extract_fecha_emision_from_bytes(file_bytes, ext=str(ext))
    if not fe:
        return False
    row = conn.execute(
        "SELECT cliente, fecha_vencimiento FROM facturacion_facturas WHERE id = ?",
        (int(factura_id),),
    ).fetchone()
    if not row:
        return False
    maps = load_catalog_maps(conn)
    dias = dias_credito_para_cliente(maps, str(row["cliente"] or ""))
    if dias > 0:
        venc = add_days_to_iso_date(fe, dias)
    else:
        venc = row["fecha_vencimiento"]
    conn.execute(
        """
        UPDATE facturacion_facturas SET
            fecha_factura = ?,
            fecha_vencimiento = ?,
            fecha_actualizacion = ?,
            actualizado_por = ?
        WHERE id = ?
        """,
        (fe, venc, now, user_id, int(factura_id)),
    )
    log_evento(
        conn,
        factura_id=int(factura_id),
        tipo="FECHAS_DESDE_DOCUMENTO",
        detalle={"fecha_factura": fe, "fecha_vencimiento": venc, "ext": ext, "dias_credito": dias},
        user_id=user_id,
        created_at=now,
    )
    return True
