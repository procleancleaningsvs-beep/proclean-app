"""Historial agrupado por paciente (expediente) para Exámenes médicos."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any, Sequence

from modules.examenes_medicos.identifiers import normalize_nombre_key, normalize_patient_identity_key

_log = logging.getLogger(__name__)


def _first_int(row: Sequence[Any] | None) -> int:
    """Primer valor de fetchone()/fetchall() sin asumir sqlite3.Row (init_db usa tuplas)."""
    if row is None:
        return 0
    try:
        v = row[0]
    except (TypeError, IndexError, KeyError):
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _row_as_dict(row: Sequence[Any] | sqlite3.Row | None, columns: list[str]) -> dict[str, Any]:
    """Convierte una fila (tupla o Row) en dict usando el orden de columnas del SELECT."""
    if row is None:
        return {}
    if isinstance(row, sqlite3.Row):
        return {k: row[k] for k in columns}
    return {columns[i]: row[i] for i in range(min(len(columns), len(row)))}


def ensure_examenes_expediente_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS examenes_medicos_expediente (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            patient_key TEXT NOT NULL,
            cliente_numero TEXT,
            patient_display_name TEXT NOT NULL,
            imc_label TEXT,
            orina_pdf_relpath TEXT,
            orina_pdf_download_name TEXT,
            orina_docx_relpath TEXT,
            orina_docx_download_name TEXT,
            sangre_pdf_relpath TEXT,
            sangre_pdf_download_name TEXT,
            sangre_docx_relpath TEXT,
            sangre_docx_download_name TEXT,
            last_scope TEXT,
            last_format TEXT,
            last_export_at TEXT NOT NULL,
            last_user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            UNIQUE(user_id, patient_key)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_em_exp_user_date ON examenes_medicos_expediente "
        "(user_id, last_export_at DESC)"
    )
    _ensure_expediente_column(conn, "paciente_id", "TEXT")
    _ensure_expediente_column(conn, "orden", "TEXT")
    _ensure_expediente_column(conn, "folio", "TEXT")
    _ensure_expediente_column(conn, "fecha_nacimiento", "TEXT")
    _ensure_expediente_column(conn, "filename_base", "TEXT")
    _ensure_expediente_column(conn, "template_name", "TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_em_exp_orden_unique "
        "ON examenes_medicos_expediente (orden) WHERE orden IS NOT NULL"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_em_exp_folio_unique "
        "ON examenes_medicos_expediente (folio) WHERE folio IS NOT NULL"
    )


def _ensure_expediente_column(conn: sqlite3.Connection, name: str, ddl_type: str) -> None:
    cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(examenes_medicos_expediente)").fetchall()}
    if name not in cols:
        conn.execute(f"ALTER TABLE examenes_medicos_expediente ADD COLUMN {name} {ddl_type}")


def _canonical_display_name(master: dict[str, Any]) -> str:
    a = " ".join(str(master.get("apellidos") or "").split())
    n = " ".join(str(master.get("nombres") or "").split())
    s = f"{a} {n}".strip()
    return s if s else "Paciente"


def _imc_label_from_master(master: dict[str, Any]) -> str | None:
    from modules.examenes_medicos.validation import classify_imc, validate_positive_float

    p1 = validate_positive_float(master.get("peso_kg"), "Peso (kg)")
    p2 = validate_positive_float(master.get("estatura_m"), "Estatura (m)")
    peso, est = p1[0], p2[0]
    if p1[1] or p2[1] or peso is None or est is None or est > 2.6:
        return None
    imc = peso / (est**2)
    clas = classify_imc(imc)
    return f"{imc:.2f} ({clas})"


def upsert_examenes_expediente_merge(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    master: dict[str, Any],
    ident: dict[str, str],
    exam_type: str,
    last_scope: str,
    last_format: str,
    docx_relpath: str | None,
    pdf_relpath: str | None,
    docx_download_name: str | None,
    pdf_download_name: str | None,
    when_iso: str,
) -> int:
    """Fusiona una exportación (orina o sangre) en un único expediente por paciente."""
    n = str(master.get("nombres") or "")
    a = str(master.get("apellidos") or "")
    patient_key = normalize_nombre_key(n, a)
    display = _canonical_display_name(master)
    cliente = str(
        ident.get("cliente_numero") or master.get("cliente_numero") or ""
    ).strip() or None
    imc_lab = _imc_label_from_master(master)

    cur_sel = conn.execute(
        "SELECT * FROM examenes_medicos_expediente WHERE user_id = ? AND patient_key = ?",
        (user_id, patient_key),
    )
    exp_cols = [d[0] for d in (cur_sel.description or ())]
    row = cur_sel.fetchone()

    def pick_pdf_docx() -> tuple[str | None, str | None, str | None, str | None]:
        if last_format == "pdf":
            return pdf_relpath, pdf_download_name, None, None
        return None, None, docx_relpath, docx_download_name

    pdf_r, pdf_n, dx_r, dx_n = pick_pdf_docx()

    if row is None:
        od: dict[str, Any] = {
            "user_id": user_id,
            "patient_key": patient_key,
            "cliente_numero": cliente,
            "patient_display_name": display,
            "imc_label": imc_lab,
            "orina_pdf_relpath": None,
            "orina_pdf_download_name": None,
            "orina_docx_relpath": None,
            "orina_docx_download_name": None,
            "sangre_pdf_relpath": None,
            "sangre_pdf_download_name": None,
            "sangre_docx_relpath": None,
            "sangre_docx_download_name": None,
            "last_scope": last_scope,
            "last_format": last_format,
            "last_export_at": when_iso,
            "last_user_id": user_id,
            "created_at": when_iso,
            "updated_at": when_iso,
        }
        if exam_type == "orina":
            od["orina_pdf_relpath"] = pdf_r
            od["orina_pdf_download_name"] = pdf_n
            od["orina_docx_relpath"] = dx_r
            od["orina_docx_download_name"] = dx_n
        elif exam_type == "sangre":
            od["sangre_pdf_relpath"] = pdf_r
            od["sangre_pdf_download_name"] = pdf_n
            od["sangre_docx_relpath"] = dx_r
            od["sangre_docx_download_name"] = dx_n
        cols = ", ".join(od.keys())
        qs = ", ".join(["?"] * len(od))
        cur = conn.execute(f"INSERT INTO examenes_medicos_expediente ({cols}) VALUES ({qs})", tuple(od.values()))
        return int(cur.lastrowid)

    upd = _row_as_dict(row, exp_cols)
    rid = int(upd["id"])
    if cliente:
        upd["cliente_numero"] = cliente
    upd["patient_display_name"] = display
    if imc_lab:
        upd["imc_label"] = imc_lab
    upd["last_scope"] = last_scope
    upd["last_format"] = last_format
    upd["last_export_at"] = when_iso
    upd["last_user_id"] = user_id
    upd["updated_at"] = when_iso

    if exam_type == "orina":
        if pdf_r:
            upd["orina_pdf_relpath"], upd["orina_pdf_download_name"] = pdf_r, pdf_n
        if dx_r:
            upd["orina_docx_relpath"], upd["orina_docx_download_name"] = dx_r, dx_n
    elif exam_type == "sangre":
        if pdf_r:
            upd["sangre_pdf_relpath"], upd["sangre_pdf_download_name"] = pdf_r, pdf_n
        if dx_r:
            upd["sangre_docx_relpath"], upd["sangre_docx_download_name"] = dx_r, dx_n

    conn.execute(
        """
        UPDATE examenes_medicos_expediente SET
            cliente_numero = ?, patient_display_name = ?, imc_label = ?,
            orina_pdf_relpath = ?, orina_pdf_download_name = ?,
            orina_docx_relpath = ?, orina_docx_download_name = ?,
            sangre_pdf_relpath = ?, sangre_pdf_download_name = ?,
            sangre_docx_relpath = ?, sangre_docx_download_name = ?,
            last_scope = ?, last_format = ?, last_export_at = ?, last_user_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            upd["cliente_numero"],
            upd["patient_display_name"],
            upd["imc_label"],
            upd["orina_pdf_relpath"],
            upd["orina_pdf_download_name"],
            upd["orina_docx_relpath"],
            upd["orina_docx_download_name"],
            upd["sangre_pdf_relpath"],
            upd["sangre_pdf_download_name"],
            upd["sangre_docx_relpath"],
            upd["sangre_docx_download_name"],
            upd["last_scope"],
            upd["last_format"],
            upd["last_export_at"],
            upd["last_user_id"],
            upd["updated_at"],
            rid,
        ),
    )
    return rid


def migrate_legacy_historial_to_expediente(conn: sqlite3.Connection) -> None:
    """Una sola pasada: fusiona filas viejas de examenes_medicos_historial en expedientes.

    Compatible con conexiones sin ``row_factory`` (tuplas). Errores por fila se omiten;
    errores globales deben manejarse en el llamador si se desea no bloquear el arranque.
    """
    cnt_row = conn.execute("SELECT COUNT(*) FROM examenes_medicos_expediente").fetchone()
    if _first_int(cnt_row) > 0:
        return

    hist_cols = [
        "id",
        "user_id",
        "created_at",
        "exam_type",
        "patient_display_name",
        "payload_json",
        "docx_relpath",
        "pdf_relpath",
        "docx_download_name",
        "pdf_download_name",
    ]
    cur = conn.execute(
        "SELECT "
        + ", ".join(hist_cols)
        + " FROM examenes_medicos_historial ORDER BY id ASC"
    )
    rows = cur.fetchall()
    for r in rows:
        d = _row_as_dict(r, hist_cols)
        try:
            payload = json.loads(str(d.get("payload_json") or "{}"))
        except (json.JSONDecodeError, TypeError):
            payload = {}
        fm = payload.get("formulario_maestro") if isinstance(payload, dict) else None
        if not isinstance(fm, dict):
            fm = {}
        nombres = str(fm.get("nombres") or "")
        apellidos = str(fm.get("apellidos") or "")
        if nombres.strip() or apellidos.strip():
            key = normalize_nombre_key(nombres, apellidos)
        else:
            key = normalize_nombre_key("", str(d.get("patient_display_name") or "").strip())
        ident: dict[str, Any] = {}
        if isinstance(payload.get("identificadores"), dict):
            ident = payload["identificadores"]
        elif fm.get("cliente_numero"):
            ident = {"cliente_numero": str(fm.get("cliente_numero"))}
        exam_t = str(d.get("exam_type") or "")
        if exam_t == "imc":
            vals = payload.get("valores") if isinstance(payload, dict) else None
            if isinstance(vals, dict):
                imc = vals.get("imc")
                clas = vals.get("clasificacion")
                imc_lab = f"{imc} ({clas})" if imc is not None and clas else None
            else:
                imc_lab = None
            ex_row = conn.execute(
                "SELECT id FROM examenes_medicos_expediente WHERE user_id = ? AND patient_key = ?",
                (int(d["user_id"]), key),
            ).fetchone()
            if ex_row is not None:
                conn.execute(
                    "UPDATE examenes_medicos_expediente SET imc_label = COALESCE(?, imc_label), "
                    "updated_at = ? WHERE id = ?",
                    (imc_lab, str(d["created_at"]), int(ex_row[0])),
                )
            else:
                disp = str(d.get("patient_display_name") or _canonical_display_name(fm))
                conn.execute(
                    """
                    INSERT INTO examenes_medicos_expediente (
                        user_id, patient_key, cliente_numero, patient_display_name, imc_label,
                        last_scope, last_format, last_export_at, last_user_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'imc', '—', ?, ?, ?, ?)
                    """,
                    (
                        int(d["user_id"]),
                        key,
                        str(ident.get("cliente_numero") or "").strip() or None,
                        disp,
                        imc_lab,
                        str(d["created_at"]),
                        int(d["user_id"]),
                        str(d["created_at"]),
                        str(d["created_at"]),
                    ),
                )
            continue

        want = str(payload.get("formato_descarga") or "pdf")
        scope = str(payload.get("alcance") or "orina")
        try:
            upsert_examenes_expediente_merge(
                conn,
                user_id=int(d["user_id"]),
                master=fm if fm else {"nombres": nombres, "apellidos": apellidos},
                ident=ident,
                exam_type=exam_t,
                last_scope=scope,
                last_format=want,
                docx_relpath=str(d["docx_relpath"]) if d.get("docx_relpath") else None,
                pdf_relpath=str(d["pdf_relpath"]) if d.get("pdf_relpath") else None,
                docx_download_name=str(d["docx_download_name"]) if d.get("docx_download_name") else None,
                pdf_download_name=str(d["pdf_download_name"]) if d.get("pdf_download_name") else None,
                when_iso=str(d["created_at"]),
            )
        except Exception:
            _log.warning(
                "examenes_medicos: omitiendo fila historial id=%s en migración legacy (error al fusionar)",
                d.get("id"),
                exc_info=True,
            )


@dataclass
class ExpedienteRow:
    id: int
    user_id: int
    patient_key: str
    cliente_numero: str | None
    patient_display_name: str
    imc_label: str | None
    orina_pdf_relpath: str | None
    orina_pdf_download_name: str | None
    orina_docx_relpath: str | None
    orina_docx_download_name: str | None
    sangre_pdf_relpath: str | None
    sangre_pdf_download_name: str | None
    sangre_docx_relpath: str | None
    sangre_docx_download_name: str | None
    last_scope: str | None
    last_format: str | None
    last_export_at: str
    last_user_id: int
    created_at: str
    updated_at: str
    username: str | None = None
    paciente_id: str | None = None
    orden: str | None = None
    folio: str | None = None
    fecha_nacimiento: str | None = None
    filename_base: str | None = None
    template_name: str | None = None


def _exp_row(r: sqlite3.Row) -> ExpedienteRow:
    keys = set(r.keys())
    return ExpedienteRow(
        id=int(r["id"]),
        user_id=int(r["user_id"]),
        patient_key=str(r["patient_key"]),
        cliente_numero=str(r["cliente_numero"]) if r["cliente_numero"] else None,
        patient_display_name=str(r["patient_display_name"] or ""),
        imc_label=str(r["imc_label"]) if r["imc_label"] else None,
        orina_pdf_relpath=str(r["orina_pdf_relpath"]) if r["orina_pdf_relpath"] else None,
        orina_pdf_download_name=str(r["orina_pdf_download_name"]) if r["orina_pdf_download_name"] else None,
        orina_docx_relpath=str(r["orina_docx_relpath"]) if r["orina_docx_relpath"] else None,
        orina_docx_download_name=str(r["orina_docx_download_name"]) if r["orina_docx_download_name"] else None,
        sangre_pdf_relpath=str(r["sangre_pdf_relpath"]) if r["sangre_pdf_relpath"] else None,
        sangre_pdf_download_name=str(r["sangre_pdf_download_name"]) if r["sangre_pdf_download_name"] else None,
        sangre_docx_relpath=str(r["sangre_docx_relpath"]) if r["sangre_docx_relpath"] else None,
        sangre_docx_download_name=str(r["sangre_docx_download_name"]) if r["sangre_docx_download_name"] else None,
        last_scope=str(r["last_scope"]) if r["last_scope"] else None,
        last_format=str(r["last_format"]) if r["last_format"] else None,
        last_export_at=str(r["last_export_at"]),
        last_user_id=int(r["last_user_id"]),
        created_at=str(r["created_at"]),
        updated_at=str(r["updated_at"]),
        username=str(r["username"]) if "username" in keys and r["username"] is not None else None,
        paciente_id=str(r["paciente_id"]) if "paciente_id" in keys and r["paciente_id"] else None,
        orden=str(r["orden"]) if "orden" in keys and r["orden"] else None,
        folio=str(r["folio"]) if "folio" in keys and r["folio"] else None,
        fecha_nacimiento=str(r["fecha_nacimiento"]) if "fecha_nacimiento" in keys and r["fecha_nacimiento"] else None,
        filename_base=str(r["filename_base"]) if "filename_base" in keys and r["filename_base"] else None,
        template_name=str(r["template_name"]) if "template_name" in keys and r["template_name"] else None,
    )


def insert_unified_expediente(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    master: dict[str, Any],
    ident: dict[str, str],
    last_format: str,
    docx_relpath: str | None,
    pdf_relpath: str | None,
    docx_download_name: str | None,
    pdf_download_name: str | None,
    when_iso: str,
    template_name: str,
) -> int:
    patient_identity = normalize_patient_identity_key(
        str(master.get("nombres") or ""),
        str(master.get("apellidos") or ""),
        str(master.get("fecha_nacimiento") or ""),
    )
    orden = str(ident["orden"])
    patient_key = f"{patient_identity}|||{orden}"
    display = _canonical_display_name(master)
    imc_lab = _imc_label_from_master(master)
    cur = conn.execute(
        """
        INSERT INTO examenes_medicos_expediente (
            user_id, patient_key, cliente_numero, patient_display_name, imc_label,
            sangre_pdf_relpath, sangre_pdf_download_name,
            sangre_docx_relpath, sangre_docx_download_name,
            last_scope, last_format, last_export_at, last_user_id, created_at, updated_at,
            paciente_id, orden, folio, fecha_nacimiento, filename_base, template_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unificado', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            patient_key,
            ident.get("paciente_id"),
            display,
            imc_lab,
            pdf_relpath,
            pdf_download_name,
            docx_relpath,
            docx_download_name,
            last_format,
            when_iso,
            user_id,
            when_iso,
            when_iso,
            ident.get("paciente_id"),
            ident.get("orden"),
            ident.get("folio"),
            str(master.get("fecha_nacimiento") or "")[:10],
            ident.get("filename_base"),
            template_name,
        ),
    )
    return int(cur.lastrowid)


def update_unified_expediente_export(
    conn: sqlite3.Connection,
    *,
    expediente_id: int,
    user_id: int,
    last_format: str,
    docx_relpath: str | None,
    pdf_relpath: str | None,
    docx_download_name: str | None,
    pdf_download_name: str | None,
    when_iso: str,
    template_name: str,
) -> None:
    row = conn.execute(
        "SELECT id FROM examenes_medicos_expediente WHERE id = ? AND user_id = ?",
        (expediente_id, user_id),
    ).fetchone()
    if row is None:
        raise ValueError("Expediente no encontrado.")
    conn.execute(
        """
        UPDATE examenes_medicos_expediente SET
            sangre_pdf_relpath = COALESCE(?, sangre_pdf_relpath),
            sangre_pdf_download_name = COALESCE(?, sangre_pdf_download_name),
            sangre_docx_relpath = COALESCE(?, sangre_docx_relpath),
            sangre_docx_download_name = COALESCE(?, sangre_docx_download_name),
            last_scope = 'unificado',
            last_format = ?,
            last_export_at = ?,
            last_user_id = ?,
            updated_at = ?,
            template_name = ?
        WHERE id = ? AND user_id = ?
        """,
        (
            pdf_relpath,
            pdf_download_name,
            docx_relpath,
            docx_download_name,
            last_format,
            when_iso,
            user_id,
            when_iso,
            template_name,
            expediente_id,
            user_id,
        ),
    )


def list_examenes_expedientes(
    db_path: str, *, user_id: int | None = None, limit: int = 200
) -> list[ExpedienteRow]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        lim = max(1, min(500, int(limit)))
        if user_id is not None:
            rows = conn.execute(
                """
                SELECT e.*, u.username AS username
                FROM examenes_medicos_expediente e
                JOIN users u ON u.id = e.last_user_id
                WHERE e.user_id = ?
                ORDER BY datetime(e.last_export_at) DESC
                LIMIT ?
                """,
                (user_id, lim),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT e.*, u.username AS username
                FROM examenes_medicos_expediente e
                JOIN users u ON u.id = e.last_user_id
                ORDER BY datetime(e.last_export_at) DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
        return [_exp_row(r) for r in rows]
    finally:
        conn.close()


def get_examenes_expediente(db_path: str, expediente_id: int) -> ExpedienteRow | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute(
            """
            SELECT e.*, u.username AS username
            FROM examenes_medicos_expediente e
            JOIN users u ON u.id = e.last_user_id
            WHERE e.id = ?
            """,
            (expediente_id,),
        ).fetchone()
        return _exp_row(r) if r else None
    finally:
        conn.close()


def delete_examenes_expediente(conn: sqlite3.Connection, expediente_id: int) -> bool:
    cur = conn.execute("DELETE FROM examenes_medicos_expediente WHERE id = ?", (expediente_id,))
    return cur.rowcount > 0
