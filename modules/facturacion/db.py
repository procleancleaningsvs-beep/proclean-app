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

# Esquema v2: seguimiento pre-factura (numero_factura opcional) + es_pre_factura
DDL_FACTURAS_V2 = """
CREATE TABLE facturacion_facturas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mes INTEGER NOT NULL,
            anio INTEGER NOT NULL,
            asistencia_mes INTEGER,
            asistencia_anio INTEGER,
            cliente TEXT NOT NULL,
            razon_social TEXT,
            planta_servicio TEXT,
            concepto_servicio TEXT,
            usuario_contacto TEXT,
            responsable_interno TEXT,
            numero_factura TEXT,
            es_pre_factura INTEGER NOT NULL DEFAULT 0,
            plantilla_linea_id INTEGER,
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


def ensure_facturacion_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        DDL_FACTURAS_V2.replace("CREATE TABLE facturacion_facturas", "CREATE TABLE IF NOT EXISTS facturacion_facturas")
    )
    conn.execute("DROP INDEX IF EXISTS uq_facturacion_factura_activa")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_facturacion_factura_activa
        ON facturacion_facturas (numero_factura, cliente, mes, anio)
        WHERE es_factura_activa = 1
          AND numero_factura IS NOT NULL
          AND TRIM(numero_factura) != ''
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
        CREATE TABLE IF NOT EXISTS facturacion_correo_cliente_map (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL CHECK (tipo IN ('EMAIL', 'DOMINIO')),
            valor TEXT NOT NULL,
            cliente_principal TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (tipo, valor)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_facturacion_correo_map_valor
        ON facturacion_correo_cliente_map (tipo, valor)
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS facturacion_cliente_plantilla (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente TEXT NOT NULL,
            orden INTEGER NOT NULL DEFAULT 0,
            clasificacion TEXT,
            planta_servicio TEXT,
            usuario_contacto TEXT,
            razon_social TEXT,
            responsable_interno TEXT,
            requiere_portal INTEGER NOT NULL DEFAULT 0,
            notas_internas TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_facturacion_plantilla_cliente
        ON facturacion_cliente_plantilla(cliente)
        """
    )
    _migrate_facturacion_columns(conn)
    _migrate_facturacion_preinvoice_schema(conn)
    conn.execute("DROP INDEX IF EXISTS uq_facturacion_plantilla_mes")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_facturacion_plantilla_mes
        ON facturacion_facturas (mes, anio, plantilla_linea_id)
        WHERE es_factura_activa = 1 AND plantilla_linea_id IS NOT NULL
        """
    )


def _table_column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate_facturacion_columns(conn: sqlite3.Connection) -> None:
    cols = _table_column_names(conn, "facturacion_facturas")
    if "razon_social" not in cols:
        conn.execute("ALTER TABLE facturacion_facturas ADD COLUMN razon_social TEXT")
    if "plantilla_linea_id" not in cols:
        conn.execute("ALTER TABLE facturacion_facturas ADD COLUMN plantilla_linea_id INTEGER")


def _factura_numero_is_not_null(conn: sqlite3.Connection) -> bool:
    for r in conn.execute("PRAGMA table_info(facturacion_facturas)").fetchall():
        if str(r[1]) == "numero_factura":
            return int(r[3]) == 1
    return False


def _rebuild_facturas_preinvoice(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        for idx in (
            "uq_facturacion_factura_activa",
            "idx_facturacion_facturas_mes_anio",
            "idx_facturacion_facturas_cliente",
        ):
            conn.execute(f"DROP INDEX IF EXISTS {idx}")
        conn.execute("ALTER TABLE facturacion_facturas RENAME TO _facturas_old")
        conn.execute(DDL_FACTURAS_V2)
        old_rows = conn.execute("SELECT * FROM _facturas_old").fetchall()
        old_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(_facturas_old)").fetchall()}
        for r in old_rows:
            d = dict(r)
            raw_num = d.get("numero_factura")
            num = None if raw_num is None or str(raw_num).strip() == "" else str(raw_num).strip()
            es_pre = 1 if num is None else 0
            rz = d.get("razon_social") if "razon_social" in old_cols else None
            conn.execute(
                """
                INSERT INTO facturacion_facturas (
                    id, mes, anio, asistencia_mes, asistencia_anio, cliente, razon_social, planta_servicio, concepto_servicio,
                    usuario_contacto, responsable_interno, numero_factura, es_pre_factura, plantilla_linea_id, po_oc, requiere_po_oc, requiere_portal,
                    subtotal, iva, total, fecha_factura, fecha_vencimiento,
                    estatus_operativo, estatus_pago, alertas_json, comentarios,
                    factura_original_id, factura_reemplazada_por_id,
                    refacturacion_motivo, refacturacion_fecha, refacturacion_por,
                    es_factura_activa, fecha_creacion, fecha_actualizacion, actualizado_por, creado_por
                ) VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
                """,
                (
                    int(d["id"]),
                    int(d["mes"]),
                    int(d["anio"]),
                    d.get("asistencia_mes"),
                    d.get("asistencia_anio"),
                    str(d["cliente"]).strip(),
                    (str(rz).strip() if rz else None) or None,
                    d.get("planta_servicio"),
                    d.get("concepto_servicio"),
                    d.get("usuario_contacto"),
                    d.get("responsable_interno"),
                    num,
                    es_pre,
                    d.get("plantilla_linea_id") if "plantilla_linea_id" in old_cols else None,
                    d.get("po_oc"),
                    int(d.get("requiere_po_oc") or 0),
                    int(d.get("requiere_portal") or 0),
                    d.get("subtotal"),
                    d.get("iva"),
                    d.get("total"),
                    d.get("fecha_factura"),
                    d.get("fecha_vencimiento"),
                    str(d["estatus_operativo"]).strip(),
                    str(d.get("estatus_pago") or "PENDIENTE").strip(),
                    d.get("alertas_json") or "[]",
                    d.get("comentarios"),
                    d.get("factura_original_id"),
                    d.get("factura_reemplazada_por_id"),
                    d.get("refacturacion_motivo"),
                    d.get("refacturacion_fecha"),
                    d.get("refacturacion_por"),
                    int(d.get("es_factura_activa", 1)),
                    str(d["fecha_creacion"]),
                    str(d["fecha_actualizacion"]),
                    d.get("actualizado_por"),
                    d.get("creado_por"),
                ),
            )
        conn.execute("DROP TABLE _facturas_old")
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_facturacion_factura_activa
            ON facturacion_facturas (numero_factura, cliente, mes, anio)
            WHERE es_factura_activa = 1
              AND numero_factura IS NOT NULL
              AND TRIM(numero_factura) != ''
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
        mx = conn.execute("SELECT MAX(id) FROM facturacion_facturas").fetchone()[0]
        if mx is not None:
            try:
                conn.execute("DELETE FROM sqlite_sequence WHERE name = 'facturacion_facturas'")
                conn.execute(
                    "INSERT INTO sqlite_sequence (name, seq) VALUES ('facturacion_facturas', ?)",
                    (int(mx),),
                )
            except sqlite3.OperationalError:
                pass
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _migrate_facturacion_preinvoice_schema(conn: sqlite3.Connection) -> None:
    cols = _table_column_names(conn, "facturacion_facturas")
    if _factura_numero_is_not_null(conn):
        _rebuild_facturas_preinvoice(conn)
        cols = _table_column_names(conn, "facturacion_facturas")
    if "es_pre_factura" not in cols:
        conn.execute(
            "ALTER TABLE facturacion_facturas ADD COLUMN es_pre_factura INTEGER NOT NULL DEFAULT 0"
        )
        conn.execute(
            """
            UPDATE facturacion_facturas SET es_pre_factura = CASE
                WHEN numero_factura IS NULL OR TRIM(numero_factura) = '' THEN 1 ELSE 0 END
            """
        )


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
    manual = [
        a
        for a in parse_alertas_json(row["alertas_json"])
        if a not in ("ARCHIVO FALTANTE", "SIN PO/OC", "SIN NÚMERO FACTURA")
    ]
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
    solo_pre_factura: bool = False,
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
    if solo_pre_factura:
        wh.append("(numero_factura IS NULL OR TRIM(numero_factura) = '')")
    if q_numero:
        wh.append("numero_factura LIKE ?")
        args.append(f"%{q_numero.strip()}%")
    if q_po:
        wh.append("po_oc LIKE ?")
        args.append(f"%{q_po.strip()}%")
    sql = f"""
        SELECT * FROM facturacion_facturas
        WHERE {' AND '.join(wh)}
        ORDER BY anio DESC, mes DESC, cliente ASC,
          CASE WHEN numero_factura IS NULL OR TRIM(numero_factura) = '' THEN 0 ELSE 1 END ASC,
          COALESCE(numero_factura, '') ASC
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
        f"SELECT id, numero_factura FROM facturacion_facturas WHERE {' AND '.join(wh)} "
        "AND numero_factura IS NOT NULL AND TRIM(numero_factura) != ''",
        args,
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
    raw_num = data.get("numero_factura")
    if raw_num is None or str(raw_num).strip() == "":
        num_val: str | None = None
    else:
        num_val = str(raw_num).strip()
    if "es_pre_factura" in data:
        es_pre = int(data["es_pre_factura"])
    else:
        es_pre = 1 if num_val is None else 0
    alertas_manual = data.get("alertas") if isinstance(data.get("alertas"), list) else parse_alertas_json(
        data.get("alertas_json")
    )
    auto = compute_auto_alertas(
        cliente=str(data.get("cliente") or ""),
        po_oc=data.get("po_oc"),
        tiene_pdf=False,
        tiene_xml=False,
        estatus_operativo=str(data.get("estatus_operativo") or ""),
        numero_factura=num_val,
        manual_alertas=alertas_manual,
    )
    cur = conn.execute(
        """
        INSERT INTO facturacion_facturas (
            mes, anio, asistencia_mes, asistencia_anio, cliente, razon_social, planta_servicio, concepto_servicio,
            usuario_contacto, responsable_interno, numero_factura, es_pre_factura, plantilla_linea_id, po_oc, requiere_po_oc, requiere_portal,
            subtotal, iva, total, fecha_factura, fecha_vencimiento,
            estatus_operativo, estatus_pago, alertas_json, comentarios,
            factura_original_id, factura_reemplazada_por_id,
            refacturacion_motivo, refacturacion_fecha, refacturacion_por,
            es_factura_activa, fecha_creacion, fecha_actualizacion, actualizado_por, creado_por
        ) VALUES (
            """
        + ",".join(["?"] * 35)
        + """
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
            num_val,
            es_pre,
            data.get("plantilla_linea_id"),
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
    if "numero_factura" in data:
        rv = data.get("numero_factura")
        new_num = None if rv is None or str(rv).strip() == "" else str(rv).strip()
    else:
        new_num = row["numero_factura"]
    if "es_pre_factura" in data:
        es_pre = int(data["es_pre_factura"])
    else:
        es_pre = 1 if new_num is None or str(new_num).strip() == "" else 0
    auto = compute_auto_alertas(
        cliente=str(data.get("cliente", row["cliente"])),
        po_oc=data.get("po_oc", row["po_oc"]),
        tiene_pdf=adj.get("pdf") is not None,
        tiene_xml=adj.get("xml") is not None,
        estatus_operativo=op_eff,
        numero_factura=new_num,
        manual_alertas=manual,
    )
    conn.execute(
        """
        UPDATE facturacion_facturas SET
            mes = ?, anio = ?, asistencia_mes = ?, asistencia_anio = ?,
            cliente = ?, razon_social = ?, planta_servicio = ?, concepto_servicio = ?,
            usuario_contacto = ?, responsable_interno = ?,
            numero_factura = ?, es_pre_factura = ?, po_oc = ?, requiere_po_oc = ?, requiere_portal = ?,
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
            new_num,
            es_pre,
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
    sin_factura = sum(
        1
        for r in rows
        if r["numero_factura"] is None or str(r["numero_factura"]).strip() == ""
    )
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
        "sin_factura_emitida": sin_factura,
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


def list_cliente_plantillas(conn: sqlite3.Connection, *, cliente: str | None = None) -> list[dict[str, Any]]:
    if cliente and str(cliente).strip():
        cur = conn.execute(
            """
            SELECT * FROM facturacion_cliente_plantilla
            WHERE TRIM(cliente) = TRIM(?)
            ORDER BY orden ASC, id ASC
            """,
            (str(cliente).strip(),),
        )
    else:
        cur = conn.execute(
            """
            SELECT * FROM facturacion_cliente_plantilla
            ORDER BY cliente ASC, orden ASC, id ASC
            """
        )
    return [dict(r) for r in cur.fetchall()]


def insert_cliente_plantilla(
    conn: sqlite3.Connection,
    *,
    cliente: str,
    orden: int,
    clasificacion: str | None,
    planta_servicio: str | None,
    usuario_contacto: str | None,
    razon_social: str | None,
    responsable_interno: str | None,
    requiere_portal: int,
    notas_internas: str | None,
    now: str,
) -> int:
    cli = (cliente or "").strip()
    if not cli:
        raise ValueError("cliente es obligatorio")
    cur = conn.execute(
        """
        INSERT INTO facturacion_cliente_plantilla (
            cliente, orden, clasificacion, planta_servicio, usuario_contacto,
            razon_social, responsable_interno, requiere_portal, notas_internas,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cli,
            int(orden),
            (clasificacion or "").strip() or None,
            (planta_servicio or "").strip() or None,
            (usuario_contacto or "").strip() or None,
            (razon_social or "").strip() or None,
            (responsable_interno or "").strip() or None,
            1 if int(requiere_portal or 0) else 0,
            (notas_internas or "").strip() or None,
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def update_cliente_plantilla(
    conn: sqlite3.Connection,
    row_id: int,
    *,
    cliente: str,
    orden: int,
    clasificacion: str | None,
    planta_servicio: str | None,
    usuario_contacto: str | None,
    razon_social: str | None,
    responsable_interno: str | None,
    requiere_portal: int,
    notas_internas: str | None,
    now: str,
) -> None:
    cli = (cliente or "").strip()
    if not cli:
        raise ValueError("cliente es obligatorio")
    conn.execute(
        """
        UPDATE facturacion_cliente_plantilla SET
            cliente = ?, orden = ?, clasificacion = ?, planta_servicio = ?, usuario_contacto = ?,
            razon_social = ?, responsable_interno = ?, requiere_portal = ?, notas_internas = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            cli,
            int(orden),
            (clasificacion or "").strip() or None,
            (planta_servicio or "").strip() or None,
            (usuario_contacto or "").strip() or None,
            (razon_social or "").strip() or None,
            (responsable_interno or "").strip() or None,
            1 if int(requiere_portal or 0) else 0,
            (notas_internas or "").strip() or None,
            now,
            int(row_id),
        ),
    )


def delete_cliente_plantilla(conn: sqlite3.Connection, row_id: int) -> bool:
    in_use = conn.execute(
        "SELECT 1 FROM facturacion_facturas WHERE plantilla_linea_id = ? AND es_factura_activa = 1 LIMIT 1",
        (int(row_id),),
    ).fetchone()
    if in_use:
        return False
    conn.execute("DELETE FROM facturacion_cliente_plantilla WHERE id = ?", (int(row_id),))
    return True


def fabricar_esqueleto_desde_plantillas(
    conn: sqlite3.Connection,
    *,
    mes: int,
    anio: int,
    cliente: str | None,
    user_id: int | None,
    now: str,
) -> dict[str, Any]:
    """Crea filas pre-factura para el periodo según plantillas; idempotente por plantilla_linea_id."""
    rows = list_cliente_plantillas(conn, cliente=cliente)
    inserted = 0
    skipped = 0
    for p in rows:
        pid = int(p["id"])
        exists = conn.execute(
            """
            SELECT 1 FROM facturacion_facturas
            WHERE es_factura_activa = 1 AND mes = ? AND anio = ? AND plantilla_linea_id = ?
            """,
            (int(mes), int(anio), pid),
        ).fetchone()
        if exists:
            skipped += 1
            continue
        cli = str(p["cliente"] or "").strip()
        clas = (str(p["clasificacion"]).strip() if p.get("clasificacion") else None) or None
        data: dict[str, Any] = {
            "mes": int(mes),
            "anio": int(anio),
            "cliente": cli,
            "razon_social": (str(p["razon_social"]).strip() if p.get("razon_social") else None) or None,
            "planta_servicio": (str(p["planta_servicio"]).strip() if p.get("planta_servicio") else None) or None,
            "concepto_servicio": clas,
            "usuario_contacto": (str(p["usuario_contacto"]).strip() if p.get("usuario_contacto") else None) or None,
            "responsable_interno": (str(p["responsable_interno"]).strip() if p.get("responsable_interno") else None) or None,
            "numero_factura": None,
            "es_pre_factura": 1,
            "plantilla_linea_id": pid,
            "po_oc": None,
            "requiere_portal": int(p.get("requiere_portal") or 0),
            "subtotal": None,
            "iva": None,
            "total": None,
            "fecha_factura": None,
            "fecha_vencimiento": None,
            "estatus_operativo": "PENDIENTE FACTURA",
            "estatus_pago": "PENDIENTE",
            "comentarios": None,
            "alertas": [],
            "_extra_eventos": [
                {"tipo": "ESQUELETO_PLANTILLA", "detalle": {"plantilla_id": pid, "mes": mes, "anio": anio}}
            ],
        }
        insert_factura(conn, data, user_id=user_id, now=now)
        inserted += 1
    return {
        "inserted": inserted,
        "skipped_y_existia": skipped,
        "plantillas_encontradas": len(rows),
    }


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
            UNION
            SELECT DISTINCT TRIM(cliente) AS c FROM facturacion_cliente_plantilla
            WHERE TRIM(cliente) != ''
            UNION
            SELECT DISTINCT TRIM(cliente_principal) AS c FROM facturacion_correo_cliente_map
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


def list_correo_cliente_map(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute(
        """
        SELECT * FROM facturacion_correo_cliente_map
        ORDER BY tipo ASC, valor ASC
        """
    )
    return [dict(r) for r in cur.fetchall()]


def upsert_correo_cliente_map(
    conn: sqlite3.Connection,
    *,
    tipo: str,
    valor: str,
    cliente_principal: str,
    now: str,
    row_id: int | None = None,
) -> None:
    from modules.facturacion.config import CLIENTE_POR_CLASIFICAR

    t = str(tipo or "").strip().upper()
    if t not in ("EMAIL", "DOMINIO"):
        raise ValueError("tipo debe ser EMAIL o DOMINIO")
    v = str(valor or "").strip().lower()
    cp = str(cliente_principal or "").strip()
    if not v or not cp or cp.upper() == CLIENTE_POR_CLASIFICAR.upper():
        raise ValueError("valor y cliente_principal son obligatorios")
    if t == "DOMINIO" and "@" in v:
        raise ValueError("DOMINIO no debe incluir @")
    if row_id:
        conn.execute(
            """
            UPDATE facturacion_correo_cliente_map
            SET tipo = ?, valor = ?, cliente_principal = ?, updated_at = ?
            WHERE id = ?
            """,
            (t, v, cp, now, int(row_id)),
        )
        return
    conn.execute(
        """
        INSERT INTO facturacion_correo_cliente_map (tipo, valor, cliente_principal, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(tipo, valor) DO UPDATE SET
            cliente_principal = excluded.cliente_principal,
            updated_at = excluded.updated_at
        """,
        (t, v, cp, now, now),
    )


def delete_correo_cliente_map(conn: sqlite3.Connection, row_id: int) -> None:
    conn.execute("DELETE FROM facturacion_correo_cliente_map WHERE id = ?", (int(row_id),))


def record_correo_learned_from_import(
    conn: sqlite3.Connection,
    *,
    email: str | None,
    cliente_principal: str,
    now: str,
) -> None:
    """Aprende correo exacto → cliente si no hay conflicto con otro cliente ya guardado."""
    from modules.facturacion.config import CLIENTE_POR_CLASIFICAR

    e = (email or "").strip().lower()
    if "@" not in e:
        return
    cp = (cliente_principal or "").strip()
    if not cp or cp.upper() == CLIENTE_POR_CLASIFICAR.upper():
        return
    row = conn.execute(
        """
        SELECT cliente_principal FROM facturacion_correo_cliente_map
        WHERE tipo = 'EMAIL' AND valor = ?
        """,
        (e,),
    ).fetchone()
    if row:
        prev = str(row["cliente_principal"] or "").strip()
        if prev and prev.upper() != cp.upper():
            return
    upsert_correo_cliente_map(conn, tipo="EMAIL", valor=e, cliente_principal=cp, now=now)


def enriquecer_factura_desde_adjunto(
    conn: sqlite3.Connection,
    *,
    factura_id: int,
    file_bytes: bytes,
    ext: str,
    user_id: int | None,
    now: str,
) -> bool:
    """
    Tras subir PDF/XML: completa campos vacíos (número, montos, fechas) desde el documento.
    """
    from modules.facturacion.cliente_catalog import (
        add_days_to_iso_date,
        dias_credito_para_cliente,
        load_catalog_maps,
    )
    from modules.facturacion.doc_cfdi import extract_datos_desde_adjunto_bytes
    from modules.facturacion.doc_fechas import extract_fecha_emision_from_bytes

    row = conn.execute("SELECT * FROM facturacion_facturas WHERE id = ?", (int(factura_id),)).fetchone()
    if not row:
        return False
    r = dict(row)
    parsed = extract_datos_desde_adjunto_bytes(file_bytes, ext=str(ext))
    fe = parsed.get("fecha")
    if not fe:
        fe = extract_fecha_emision_from_bytes(file_bytes, ext=str(ext))

    def _empty(col: str) -> bool:
        v = r.get(col)
        return v is None or str(v).strip() == ""

    upd: dict[str, Any] = {}
    if parsed.get("numero") and _empty("numero_factura"):
        upd["numero_factura"] = str(parsed["numero"]).strip()
        upd["es_pre_factura"] = 0
    for fld, key in (("subtotal", "subtotal"), ("iva", "iva"), ("total", "total")):
        if parsed.get(key) is not None and _empty(fld):
            try:
                upd[fld] = float(parsed[key])
            except (TypeError, ValueError):
                pass
    if fe:
        fe_iso = str(fe).strip()[:10]
        if len(fe_iso) >= 10 and _empty("fecha_factura"):
            upd["fecha_factura"] = fe_iso

    fe_eff = upd.get("fecha_factura") or r.get("fecha_factura")
    if fe_eff and _empty("fecha_vencimiento"):
        maps = load_catalog_maps(conn)
        dias = dias_credito_para_cliente(maps, str(r.get("cliente") or ""))
        if dias > 0:
            venc = add_days_to_iso_date(str(fe_eff)[:10], dias)
            if venc:
                upd["fecha_vencimiento"] = venc

    if not upd:
        return False
    keys = list(upd.keys())
    set_sql = ", ".join(f"{k} = ?" for k in keys)
    vals = [upd[k] for k in keys] + [now, user_id, int(factura_id)]
    conn.execute(
        f"UPDATE facturacion_facturas SET {set_sql}, fecha_actualizacion = ?, actualizado_por = ? WHERE id = ?",
        vals,
    )
    log_evento(
        conn,
        factura_id=int(factura_id),
        tipo="DATOS_DESDE_DOCUMENTO",
        detalle={"ext": ext, "campos": upd},
        user_id=user_id,
        created_at=now,
    )
    refresh_archivo_alerta(conn, int(factura_id), now=now)
    return True


def apply_automatic_fechas_from_adjunto(
    conn: sqlite3.Connection,
    *,
    factura_id: int,
    file_bytes: bytes,
    ext: str,
    user_id: int | None,
    now: str,
) -> bool:
    """Compatibilidad: delega en enriquecer_factura_desde_adjunto."""
    return enriquecer_factura_desde_adjunto(
        conn,
        factura_id=factura_id,
        file_bytes=file_bytes,
        ext=ext,
        user_id=user_id,
        now=now,
    )
