"""Consolidado canónico de parámetros base desde Headcount activo.

Microfase 4.0+: Headcount es la fuente de verdad para empleados activos.
Nómina y CONTPAQ enriquecen; registros externos quedan marcados para revisión.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from modules.nomina.config import (
    WARN_CONTPAQ_SIN_MATCH_HEADCOUNT,
    WARN_EMPLEADO_ACTIVO_HC_BAJA_CONTPAQ,
    WARN_EMPLEADO_NOMINA_INACTIVO_HC,
    WARN_HEADCOUNT_ACTIVO_SIN_SALARIO,
    WARN_HEADCOUNT_ACTIVO_SIN_VALOR_HE,
    WARN_MATCH_DUDOSO_NOMBRE,
    WARN_NOMINA_SIN_MATCH_HEADCOUNT,
)
from modules.nomina.parametros_match import HeadcountIndex, _norm_name, build_headcount_index, match_to_headcount
from modules.nomina.vacaciones_util import resolve_status_headcount, sanitize_display_value

RECORD_HEADCOUNT_CANONICAL = "headcount_canonical"
RECORD_EXTERNAL_NOMINA = "external_nomina"
RECORD_EXTERNAL_CONTPAQ = "external_contpaq"
RECORD_LEGACY = "import"

_PENDING_HC_STATUSES = {
    "no_match_headcount",
    "pending_headcount_unavailable",
    "probable_match",
    "multiple_candidates",
    "pending_review",
    "inactive_headcount",
}
_PENDING_CONTPAQ_STATUSES = {"no_match_contpaq", "pending_review", "probable_match"}
_CONFIDENT_HC_MATCH = {"exact_nss", "exact_name", "manual_link"}


def is_headcount_active(hc_row: dict[str, Any] | None) -> bool:
    return resolve_status_headcount(hc_row) == "ACTIVO"


def filter_active_headcount(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if is_headcount_active(r)]


def headcount_key(hc_row: dict[str, Any]) -> str:
    nss = sanitize_display_value(hc_row.get("nss"))
    if nss:
        return f"nss:{nss}"
    nombre = _norm_name(hc_row.get("nombre_completo"))
    cliente = _norm_name(hc_row.get("cliente"))
    return f"name:{nombre}|{cliente}"


def classify_record_kind(*, source: str, headcount_match_status: str | None) -> str:
    ms = str(headcount_match_status or "")
    if ms in _CONFIDENT_HC_MATCH:
        return RECORD_HEADCOUNT_CANONICAL
    if ms == "inactive_headcount":
        return RECORD_EXTERNAL_NOMINA if source == "NOMINA_ACTUAL" else RECORD_EXTERNAL_CONTPAQ
    if source == "NOMINA_ACTUAL":
        return RECORD_EXTERNAL_NOMINA
    if source == "CONTPAQ":
        return RECORD_EXTERNAL_CONTPAQ
    if ms in {"exact_nss", "exact_name", "probable_match"}:
        return RECORD_HEADCOUNT_CANONICAL
    return RECORD_LEGACY


def append_conciliation_warnings(row: dict[str, Any], *, hc_row: dict[str, Any] | None = None) -> None:
    warnings: list[str] = list(row.get("warnings") or [])
    ms = str(row.get("headcount_match_status") or "")
    record_kind = str(row.get("record_kind") or "")

    if record_kind == RECORD_EXTERNAL_NOMINA and WARN_NOMINA_SIN_MATCH_HEADCOUNT not in warnings:
        warnings.append(WARN_NOMINA_SIN_MATCH_HEADCOUNT)
    if record_kind == RECORD_EXTERNAL_CONTPAQ and WARN_CONTPAQ_SIN_MATCH_HEADCOUNT not in warnings:
        warnings.append(WARN_CONTPAQ_SIN_MATCH_HEADCOUNT)
    if ms in {"probable_match", "multiple_candidates"} and WARN_MATCH_DUDOSO_NOMBRE not in warnings:
        warnings.append(WARN_MATCH_DUDOSO_NOMBRE)

    if hc_row and is_headcount_active(hc_row):
        sal = row.get("salario_operativo")
        if sal is None or (isinstance(sal, (int, float)) and float(sal) <= 0):
            if WARN_HEADCOUNT_ACTIVO_SIN_SALARIO not in warnings:
                warnings.append(WARN_HEADCOUNT_ACTIVO_SIN_SALARIO)
        he = row.get("valor_x_he")
        if he is None or (isinstance(he, (int, float)) and float(he) <= 0):
            if WARN_HEADCOUNT_ACTIVO_SIN_VALOR_HE not in warnings:
                warnings.append(WARN_HEADCOUNT_ACTIVO_SIN_VALOR_HE)

    if hc_row and not is_headcount_active(hc_row):
        if row.get("nomina_match_status") == "imported" and WARN_EMPLEADO_NOMINA_INACTIVO_HC not in warnings:
            warnings.append(WARN_EMPLEADO_NOMINA_INACTIVO_HC)

    contpaq_extra = (row.get("editable_json") or {}).get("contpaq_extra") or {}
    estatus_c = str(contpaq_extra.get("estatus") or "").strip().upper()
    if hc_row and is_headcount_active(hc_row) and estatus_c == "B":
        if WARN_EMPLEADO_ACTIVO_HC_BAJA_CONTPAQ not in warnings:
            warnings.append(WARN_EMPLEADO_ACTIVO_HC_BAJA_CONTPAQ)

    row["warnings"] = warnings


def _load_active_param_rows(db_path: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT * FROM nomina_empleado_parametros
            WHERE COALESCE(is_active, 1) = 1
            ORDER BY nombre ASC
            """
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            d["warnings"] = json.loads(d.get("warnings_json") or "[]")
            d["editable_json"] = json.loads(d.get("editable_json") or "{}")
            out.append(d)
        return out
    finally:
        conn.close()


def _index_param_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_key: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        ed = row.get("editable_json") or {}
        manual_nss = sanitize_display_value(ed.get("manual_headcount_nss"))
        nss = manual_nss or sanitize_display_value(row.get("nss"))
        if nss:
            by_key.setdefault(f"nss:{nss}", []).append(row)
            if manual_nss:
                by_key.setdefault(f"manual:{manual_nss}", []).append(row)
        nombre = str(row.get("nombre_normalizado") or _norm_name(row.get("nombre")))
        cliente = _norm_name(row.get("cliente"))
        if nombre:
            by_key.setdefault(f"name:{nombre}|{cliente}", []).append(row)
    return by_key


def _pick_best_param(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    priority = {
        RECORD_HEADCOUNT_CANONICAL: 0,
        RECORD_LEGACY: 1,
        RECORD_EXTERNAL_NOMINA: 2,
        RECORD_EXTERNAL_CONTPAQ: 3,
    }

    def score(r: dict[str, Any]) -> tuple[int, int]:
        kind = str(r.get("record_kind") or RECORD_LEGACY)
        has_sal = 0 if r.get("salario_operativo") else 1
        return (priority.get(kind, 9), has_sal)

    return sorted(candidates, key=score)[0]


def _merge_param_into_canonical(hc_row: dict[str, Any], param: dict[str, Any] | None) -> dict[str, Any]:
    hc_nss = sanitize_display_value(hc_row.get("nss"))
    hc_nombre = sanitize_display_value(hc_row.get("nombre_completo"))
    status_hc = resolve_status_headcount(hc_row)
    base: dict[str, Any] = {
        "id": param.get("id") if param else None,
        "nombre": hc_nombre,
        "nombre_normalizado": _norm_name(hc_nombre),
        "nss": hc_nss or (param.get("nss") if param else None),
        "numero_empleado": param.get("numero_empleado") if param else None,
        "codigo_contpaq": param.get("codigo_contpaq") if param else None,
        "cliente": sanitize_display_value(hc_row.get("cliente")) or (param.get("cliente") if param else None),
        "planta": param.get("planta") if param else sanitize_display_value(hc_row.get("patron")),
        "puesto": param.get("puesto") if param else sanitize_display_value(hc_row.get("puesto")),
        "banco": param.get("banco") if param else None,
        "cuenta": param.get("cuenta") if param else None,
        "salario_operativo": param.get("salario_operativo") if param else None,
        "valor_x_he": param.get("valor_x_he") if param else None,
        "localidad": param.get("localidad") if param else None,
        "localidad_normalizada": param.get("localidad_normalizada") if param else None,
        "es_frontera": param.get("es_frontera") if param else None,
        "salario_minimo_usado": param.get("salario_minimo_usado") if param else None,
        "exento_he_usado": param.get("exento_he_usado") if param else None,
        "headcount_match_status": (param.get("headcount_match_status") if param else "headcount_canonical"),
        "contpaq_match_status": param.get("contpaq_match_status") if param else None,
        "nomina_match_status": param.get("nomina_match_status") if param else None,
        "warnings": list(param.get("warnings") or []) if param else [],
        "editable_json": dict(param.get("editable_json") or {}) if param else {},
        "record_kind": RECORD_HEADCOUNT_CANONICAL,
        "status_headcount": status_hc,
        "is_canonical": True,
        "is_external": False,
    }
    if param and str(param.get("headcount_match_status") or "") == "manual_link":
        base["headcount_match_status"] = "manual_link"
    append_conciliation_warnings(base, hc_row=hc_row)
    return base


def _find_param_candidates_for_hc(
    hc: dict[str, Any],
    indexed: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    key = headcount_key(hc)
    candidates = list(indexed.get(key, []))
    nss = sanitize_display_value(hc.get("nss"))
    if nss:
        candidates.extend(indexed.get(f"nss:{nss}", []))
    nombre = _norm_name(hc.get("nombre_completo"))
    cliente_hc = _norm_name(hc.get("cliente"))
    if nombre:
        candidates.extend(indexed.get(f"name:{nombre}|{cliente_hc}", []))
    unique: list[dict[str, Any]] = []
    seen_ids: set[int | None] = set()
    for c in candidates:
        cid = c.get("id")
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        unique.append(c)
    return unique


def _merge_param_fields(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """Fusiona campos de enriquecimiento desde registro externo hacia canónico."""
    merged = dict(target)
    merge_keys = (
        "numero_empleado", "codigo_contpaq", "planta", "puesto", "banco", "cuenta",
        "salario_operativo", "valor_x_he", "localidad", "localidad_normalizada",
        "es_frontera", "salario_minimo_usado", "exento_he_usado", "zona_salario_raw",
        "fuente_salario_operativo", "fuente_valor_x_he", "fuente_numero_empleado", "fuente_nss",
        "contpaq_match_status", "nomina_match_status",
    )
    for key in merge_keys:
        src_val = source.get(key)
        if src_val not in (None, ""):
            if merged.get(key) in (None, ""):
                merged[key] = src_val
    src_warnings = list(source.get("warnings") or [])
    tgt_warnings = list(merged.get("warnings") or [])
    for w in src_warnings:
        if w not in tgt_warnings:
            tgt_warnings.append(w)
    merged["warnings"] = tgt_warnings
    src_ed = dict(source.get("editable_json") or {})
    tgt_ed = dict(merged.get("editable_json") or {})
    for k, v in src_ed.items():
        if k not in tgt_ed and k not in {"source_filename"}:
            tgt_ed[k] = v
    merged["editable_json"] = tgt_ed
    return merged


def build_legacy_parametros_view(
    db_path: str,
    *,
    cliente: str | None = None,
    match_status_any: list[str] | None = None,
    only_missing_salary: bool = False,
    only_missing_valor_he: bool = False,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    """Vista de respaldo cuando Headcount no está disponible (no representa activos reales)."""
    rows = _load_active_param_rows(db_path)
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        kind = str(item.get("record_kind") or RECORD_LEGACY)
        ms = str(item.get("headcount_match_status") or "")
        item["is_canonical"] = kind == RECORD_HEADCOUNT_CANONICAL and ms in _CONFIDENT_HC_MATCH
        item["is_external"] = kind in {RECORD_EXTERNAL_NOMINA, RECORD_EXTERNAL_CONTPAQ} or ms in _PENDING_HC_STATUSES
        item["is_legacy_view"] = True
        item["status_headcount"] = "N/D (Headcount no disponible)"
        append_conciliation_warnings(item)
        out.append(item)
    if cliente:
        c_low = cliente.strip().lower()
        out = [r for r in out if c_low in str(r.get("cliente") or "").strip().lower()]
    if only_missing_salary:
        out = [r for r in out if r.get("salario_operativo") is None or float(r.get("salario_operativo") or 0) <= 0]
    if only_missing_valor_he:
        out = [r for r in out if r.get("valor_x_he") is None or float(r.get("valor_x_he") or 0) <= 0]
    if match_status_any:
        statuses = set(match_status_any)
        out = [
            r for r in out
            if (
                str(r.get("headcount_match_status") or "") in statuses
                or str(r.get("contpaq_match_status") or "") in statuses
                or str(r.get("nomina_match_status") or "") in statuses
                or bool(r.get("warnings"))
                or r.get("is_external")
            )
        ]
    return out[: int(limit)]


def build_consolidado_view(
    db_path: str,
    headcount_rows: list[dict[str, Any]],
    *,
    cliente: str | None = None,
    match_status_any: list[str] | None = None,
    only_missing_salary: bool = False,
    only_missing_valor_he: bool = False,
    include_external: bool = True,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    active_hc = filter_active_headcount(headcount_rows)
    param_rows = _load_active_param_rows(db_path)
    indexed = _index_param_rows(param_rows)
    used_param_ids: set[int] = set()
    consolidated: list[dict[str, Any]] = []

    for hc in active_hc:
        unique_candidates = _find_param_candidates_for_hc(hc, indexed)
        param = _pick_best_param(unique_candidates)
        if param and param.get("id") is not None:
            used_param_ids.add(int(param["id"]))
        row = _merge_param_into_canonical(hc, param)
        consolidated.append(row)

    if include_external:
        for param in param_rows:
            pid = param.get("id")
            if pid is not None and int(pid) in used_param_ids:
                continue
            kind = str(param.get("record_kind") or RECORD_LEGACY)
            ms = str(param.get("headcount_match_status") or "")
            is_external = kind in {RECORD_EXTERNAL_NOMINA, RECORD_EXTERNAL_CONTPAQ} or ms in _PENDING_HC_STATUSES
            if not is_external and kind == RECORD_HEADCOUNT_CANONICAL:
                continue
            ext = dict(param)
            ext["status_headcount"] = "—"
            ext["is_canonical"] = False
            ext["is_external"] = True
            append_conciliation_warnings(ext)
            consolidated.append(ext)

    if cliente:
        c_low = cliente.strip().lower()
        consolidated = [
            r for r in consolidated
            if c_low in str(r.get("cliente") or "").strip().lower()
        ]
    if only_missing_salary:
        consolidated = [
            r for r in consolidated
            if r.get("salario_operativo") is None or float(r.get("salario_operativo") or 0) <= 0
        ]
    if only_missing_valor_he:
        consolidated = [
            r for r in consolidated
            if r.get("valor_x_he") is None or float(r.get("valor_x_he") or 0) <= 0
        ]
    if match_status_any:
        statuses = set(match_status_any)
        consolidated = [
            r for r in consolidated
            if (
                str(r.get("headcount_match_status") or "") in statuses
                or str(r.get("contpaq_match_status") or "") in statuses
                or str(r.get("nomina_match_status") or "") in statuses
                or bool(r.get("warnings"))
                or r.get("is_external")
            )
        ]

    return consolidated[: int(limit)]


def compute_parametros_stats(
    db_path: str,
    headcount_rows: list[dict[str, Any]],
) -> dict[str, int]:
    active_hc = filter_active_headcount(headcount_rows)
    view = build_consolidado_view(
        db_path,
        headcount_rows,
        include_external=True,
        limit=100000,
    )
    canonical = [r for r in view if r.get("is_canonical")]
    external = [r for r in view if r.get("is_external")]

    conn = sqlite3.connect(db_path)
    try:
        nomina_import_rows = int(
            conn.execute(
                """SELECT COALESCE(SUM(total_rows), 0) FROM nomina_parametros_imports
                   WHERE tipo_importacion = 'NOMINA_ACTUAL'"""
            ).fetchone()[0]
        )
        contpaq_import_rows = int(
            conn.execute(
                """SELECT COALESCE(SUM(total_rows), 0) FROM nomina_parametros_imports
                   WHERE tipo_importacion = 'CONTPAQ'"""
            ).fetchone()[0]
        )
        manual_links = int(
            conn.execute(
                """SELECT COUNT(*) FROM nomina_empleado_parametros
                   WHERE COALESCE(is_active, 1) = 1
                     AND editable_json LIKE '%manual_headcount_nss%'"""
            ).fetchone()[0]
        )
    finally:
        conn.close()

    def _missing_sal(rows: list[dict]) -> int:
        return sum(
            1 for r in rows
            if r.get("salario_operativo") is None or float(r.get("salario_operativo") or 0) <= 0
        )

    def _missing_he(rows: list[dict]) -> int:
        return sum(
            1 for r in rows
            if r.get("valor_x_he") is None or float(r.get("valor_x_he") or 0) <= 0
        )

    def _with_nomina(rows: list[dict]) -> int:
        return sum(1 for r in rows if r.get("nomina_match_status") == "imported")

    def _with_contpaq(rows: list[dict]) -> int:
        return sum(1 for r in rows if r.get("contpaq_match_status") == "imported")

    def _pending(rows: list[dict]) -> int:
        from modules.nomina.parametros_conciliacion import get_active_warnings

        return sum(
            1 for r in rows
            if (
                str(r.get("headcount_match_status") or "") in _PENDING_HC_STATUSES
                or str(r.get("contpaq_match_status") or "") in _PENDING_CONTPAQ_STATUSES
                or bool(get_active_warnings(r))
            )
        )

    return {
        "activos_headcount": len(active_hc),
        "stats_mode": "headcount",
        "con_nomina_vinculada": _with_nomina(canonical),
        "con_contpaq_vinculado": _with_contpaq(canonical),
        "missing_salario_operativo": _missing_sal(canonical),
        "missing_valor_x_he": _missing_he(canonical),
        "pendientes_revision": _pending(canonical) + len(external),
        "registros_externos_sin_vinculo": len(external),
        "registros_nomina_importados": nomina_import_rows,
        "registros_contpaq_importados": contpaq_import_rows,
        "vinculos_manuales": manual_links,
        # Compat dashboard legacy keys
        "total_empleados": len(active_hc),
    }


def preview_depuracion_parametros(db_path: str) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        nomina_rows = conn.execute(
            """SELECT COUNT(*) FROM nomina_empleado_parametros
               WHERE COALESCE(is_active, 1) = 1
                 AND (nomina_match_status = 'imported' OR record_kind = ?)""",
            (RECORD_EXTERNAL_NOMINA,),
        ).fetchone()[0]
        contpaq_rows = conn.execute(
            """SELECT COUNT(*) FROM nomina_empleado_parametros
               WHERE COALESCE(is_active, 1) = 1
                 AND (contpaq_match_status = 'imported' OR record_kind = ?)""",
            (RECORD_EXTERNAL_CONTPAQ,),
        ).fetchone()[0]
        canonical_rows = conn.execute(
            """SELECT COUNT(*) FROM nomina_empleado_parametros
               WHERE COALESCE(is_active, 1) = 1 AND record_kind = ?""",
            (RECORD_HEADCOUNT_CANONICAL,),
        ).fetchone()[0]
        manual_links = conn.execute(
            """SELECT COUNT(*) FROM nomina_empleado_parametros
               WHERE COALESCE(is_active, 1) = 1
                 AND editable_json LIKE '%manual_headcount_nss%'"""
        ).fetchone()[0]
        total_active = conn.execute(
            "SELECT COUNT(*) FROM nomina_empleado_parametros WHERE COALESCE(is_active, 1) = 1"
        ).fetchone()[0]
        imports_nomina = conn.execute(
            "SELECT COUNT(*) FROM nomina_parametros_imports WHERE tipo_importacion = 'NOMINA_ACTUAL'"
        ).fetchone()[0]
        imports_contpaq = conn.execute(
            "SELECT COUNT(*) FROM nomina_parametros_imports WHERE tipo_importacion = 'CONTPAQ'"
        ).fetchone()[0]
        return {
            "registros_nomina_afectados": int(nomina_rows or 0),
            "registros_contpaq_afectados": int(contpaq_rows or 0),
            "registros_canonicos": int(canonical_rows or 0),
            "vinculos_manuales": int(manual_links or 0),
            "total_parametros_activos": int(total_active or 0),
            "historial_imports_nomina": int(imports_nomina or 0),
            "historial_imports_contpaq": int(imports_contpaq or 0),
        }
    finally:
        conn.close()


def limpiar_importaciones_nomina(db_path: str, *, now_iso: str) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        cur_ext = conn.execute(
            """UPDATE nomina_empleado_parametros SET is_active = 0, updated_at = ?
               WHERE record_kind = ? AND COALESCE(is_active, 1) = 1""",
            (now_iso, RECORD_EXTERNAL_NOMINA),
        )
        cur_clear = conn.execute(
            """
            UPDATE nomina_empleado_parametros SET
                salario_operativo = NULL,
                valor_x_he = NULL,
                banco = NULL,
                cuenta = NULL,
                planta = CASE WHEN record_kind = ? THEN planta ELSE NULL END,
                localidad = NULL,
                localidad_normalizada = NULL,
                es_frontera = NULL,
                salario_minimo_usado = NULL,
                exento_he_usado = NULL,
                fuente_salario_operativo = NULL,
                fuente_valor_x_he = NULL,
                nomina_match_status = NULL,
                updated_at = ?
            WHERE COALESCE(is_active, 1) = 1
              AND (nomina_match_status = 'imported' OR record_kind = ?)
            """,
            (RECORD_HEADCOUNT_CANONICAL, now_iso, RECORD_EXTERNAL_NOMINA),
        )
        conn.commit()
        return {
            "desactivados_externos": int(cur_ext.rowcount),
            "limpiados_campos_nomina": int(cur_clear.rowcount),
        }
    finally:
        conn.close()


def limpiar_importaciones_contpaq(db_path: str, *, now_iso: str) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        cur_ext = conn.execute(
            """UPDATE nomina_empleado_parametros SET is_active = 0, updated_at = ?
               WHERE record_kind = ? AND COALESCE(is_active, 1) = 1""",
            (now_iso, RECORD_EXTERNAL_CONTPAQ),
        )
        cur_clear = conn.execute(
            """
            UPDATE nomina_empleado_parametros SET
                codigo_contpaq = NULL,
                numero_empleado = CASE
                    WHEN nomina_match_status = 'imported' THEN numero_empleado
                    ELSE NULL
                END,
                zona_salario_raw = NULL,
                contpaq_match_status = NULL,
                fuente_numero_empleado = NULL,
                updated_at = ?
            WHERE COALESCE(is_active, 1) = 1
              AND (contpaq_match_status = 'imported' OR record_kind = ?)
            """,
            (now_iso, RECORD_EXTERNAL_CONTPAQ),
        )
        conn.commit()
        return {
            "desactivados_externos": int(cur_ext.rowcount),
            "limpiados_campos_contpaq": int(cur_clear.rowcount),
        }
    finally:
        conn.close()


def borrar_vinculos_manuales(db_path: str, *, now_iso: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """SELECT id, editable_json FROM nomina_empleado_parametros
               WHERE COALESCE(is_active, 1) = 1 AND editable_json LIKE '%manual_headcount_nss%'"""
        ).fetchall()
        count = 0
        for rid, ed_raw in rows:
            ed = json.loads(ed_raw or "{}")
            ed.pop("manual_headcount_nss", None)
            ed.pop("manual_headcount_link_at", None)
            ed.pop("manual_headcount_link_by", None)
            if str(ed.get("headcount_match_status_override")) == "manual_link":
                ed.pop("headcount_match_status_override", None)
            conn.execute(
                """UPDATE nomina_empleado_parametros SET
                   editable_json = ?, headcount_match_status = CASE
                     WHEN headcount_match_status = 'manual_link' THEN 'pending_review'
                     ELSE headcount_match_status
                   END,
                   updated_at = ?
                   WHERE id = ?""",
                (json.dumps(ed, ensure_ascii=False), now_iso, int(rid)),
            )
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()


def rebuild_consolidado_parametros(
    db_path: str,
    headcount_rows: list[dict[str, Any]],
    *,
    now_iso: str,
    import_id: int | None = None,
) -> dict[str, int]:
    """Regenera filas canónicas desde Headcount activo y re-enlaza parámetros existentes."""
    active_hc = filter_active_headcount(headcount_rows)
    hc_index = build_headcount_index(headcount_rows)
    param_rows = _load_active_param_rows(db_path)
    indexed = _index_param_rows(param_rows)

    conn = sqlite3.connect(db_path)
    inserted = updated = 0
    try:
        for hc in active_hc:
            unique_candidates = _find_param_candidates_for_hc(hc, indexed)
            param = _pick_best_param(unique_candidates)
            hc_nss = sanitize_display_value(hc.get("nss"))
            hc_nombre = sanitize_display_value(hc.get("nombre_completo"))
            hc_cliente = sanitize_display_value(hc.get("cliente"))

            if param:
                merged_warnings = list(param.get("warnings") or [])
                append_conciliation_warnings(param, hc_row=hc)
                conn.execute(
                    """
                    UPDATE nomina_empleado_parametros SET
                        nombre = ?, nombre_normalizado = ?, nss = COALESCE(?, nss),
                        cliente = COALESCE(?, cliente), puesto = COALESCE(puesto, ?),
                        record_kind = ?, headcount_match_status = COALESCE(headcount_match_status, 'headcount_canonical'),
                        warnings_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        hc_nombre,
                        _norm_name(hc_nombre),
                        hc_nss or None,
                        hc_cliente or None,
                        sanitize_display_value(hc.get("puesto")) or None,
                        RECORD_HEADCOUNT_CANONICAL,
                        json.dumps(param.get("warnings") or merged_warnings, ensure_ascii=False),
                        now_iso,
                        int(param["id"]),
                    ),
                )
                updated += 1
            else:
                conn.execute(
                    """
                    INSERT INTO nomina_empleado_parametros (
                        nombre, nombre_normalizado, nss, cliente, puesto,
                        headcount_match_status, record_kind, warnings_json, editable_json,
                        last_import_id, created_at, updated_at, is_active
                    ) VALUES (?, ?, ?, ?, ?, 'headcount_canonical', ?, '[]', '{}', ?, ?, ?, 1)
                    """,
                    (
                        hc_nombre,
                        _norm_name(hc_nombre),
                        hc_nss or None,
                        hc_cliente or None,
                        sanitize_display_value(hc.get("puesto")) or None,
                        RECORD_HEADCOUNT_CANONICAL,
                        import_id,
                        now_iso,
                        now_iso,
                    ),
                )
                inserted += 1

        # Re-evaluar externos sin match claro
        for param in param_rows:
            if str(param.get("record_kind") or "") == RECORD_HEADCOUNT_CANONICAL:
                continue
            ms, _, _ = match_to_headcount(
                nombre=param.get("nombre"),
                nss=param.get("nss"),
                cliente=param.get("cliente"),
                index=hc_index,
            )
            ed = dict(param.get("editable_json") or {})
            if ed.get("manual_headcount_nss"):
                ms = "manual_link"
            kind = classify_record_kind(
                source="NOMINA_ACTUAL" if param.get("nomina_match_status") == "imported" else "CONTPAQ",
                headcount_match_status=ms,
            )
            conn.execute(
                """UPDATE nomina_empleado_parametros SET
                   headcount_match_status = ?, record_kind = ?, updated_at = ?
                   WHERE id = ?""",
                (ms, kind, now_iso, int(param["id"])),
            )
        conn.commit()
        return {"insertados": inserted, "actualizados": updated, "activos_headcount": len(active_hc)}
    finally:
        conn.close()


def search_active_headcount(
    headcount_rows: list[dict[str, Any]],
    query: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    q_norm = _norm_name(query)
    q_tokens = [t for t in q_norm.split() if len(t) >= 2]
    if not q_norm:
        return filter_active_headcount(headcount_rows)[:limit]
    out: list[dict[str, Any]] = []
    for hc in filter_active_headcount(headcount_rows):
        nombre = _norm_name(hc.get("nombre_completo"))
        nss = sanitize_display_value(hc.get("nss"))
        cliente = _norm_name(hc.get("cliente"))
        planta = _norm_name(hc.get("patron"))
        nombre_tokens = nombre.split()
        matched = (
            q_norm in nombre
            or q_norm in nss
            or q_norm in cliente
            or q_norm in planta
            or any(tok in nombre_tokens or tok in nombre for tok in q_tokens)
        )
        if matched:
            out.append(hc)
        if len(out) >= limit:
            break
    return out


def apply_manual_headcount_link(
    db_path: str,
    row_id: int,
    *,
    headcount_nss: str,
    headcount_nombre: str,
    headcount_cliente: str,
    linked_by: int | None,
    now_iso: str,
) -> bool:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        source = conn.execute(
            "SELECT * FROM nomina_empleado_parametros WHERE id = ? AND COALESCE(is_active,1)=1",
            (int(row_id),),
        ).fetchone()
        if source is None:
            return False
        source_dict = dict(source)
        source_dict["warnings"] = json.loads(source_dict.get("warnings_json") or "[]")
        source_dict["editable_json"] = json.loads(source_dict.get("editable_json") or "{}")

        target = conn.execute(
            """SELECT * FROM nomina_empleado_parametros
               WHERE COALESCE(is_active, 1) = 1 AND nss = ? AND id != ?
               ORDER BY CASE record_kind WHEN ? THEN 0 ELSE 1 END, id ASC
               LIMIT 1""",
            (headcount_nss, int(row_id), RECORD_HEADCOUNT_CANONICAL),
        ).fetchone()

        if target is not None:
            target_dict = dict(target)
            target_dict["warnings"] = json.loads(target_dict.get("warnings_json") or "[]")
            target_dict["editable_json"] = json.loads(target_dict.get("editable_json") or "{}")
            merged = _merge_param_fields(target_dict, source_dict)
            ed = dict(merged.get("editable_json") or {})
            ed["manual_headcount_nss"] = headcount_nss
            ed["manual_headcount_link_at"] = now_iso
            ed["manual_headcount_link_by"] = linked_by
            ed["linked_from_row_id"] = int(row_id)
            merged["editable_json"] = ed
            merged["headcount_match_status"] = "manual_link"
            merged["record_kind"] = RECORD_HEADCOUNT_CANONICAL
            merged["nombre"] = headcount_nombre or merged.get("nombre")
            merged["nombre_normalizado"] = _norm_name(headcount_nombre or merged.get("nombre"))
            merged["cliente"] = headcount_cliente or merged.get("cliente")
            merged["nss"] = headcount_nss or merged.get("nss")
            append_conciliation_warnings(merged)

            conn.execute(
                """
                UPDATE nomina_empleado_parametros SET
                    nombre = ?, nombre_normalizado = ?, nss = ?, cliente = ?,
                    numero_empleado = ?, codigo_contpaq = ?, planta = ?, puesto = ?,
                    banco = ?, cuenta = ?, salario_operativo = ?, valor_x_he = ?,
                    localidad = ?, localidad_normalizada = ?, es_frontera = ?,
                    salario_minimo_usado = ?, exento_he_usado = ?, zona_salario_raw = ?,
                    fuente_salario_operativo = ?, fuente_valor_x_he = ?,
                    fuente_numero_empleado = ?, fuente_nss = ?,
                    contpaq_match_status = ?, nomina_match_status = ?,
                    headcount_match_status = ?, record_kind = ?,
                    warnings_json = ?, editable_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    merged.get("nombre"),
                    merged.get("nombre_normalizado"),
                    merged.get("nss"),
                    merged.get("cliente"),
                    merged.get("numero_empleado"),
                    merged.get("codigo_contpaq"),
                    merged.get("planta"),
                    merged.get("puesto"),
                    merged.get("banco"),
                    merged.get("cuenta"),
                    merged.get("salario_operativo"),
                    merged.get("valor_x_he"),
                    merged.get("localidad"),
                    merged.get("localidad_normalizada"),
                    merged.get("es_frontera"),
                    merged.get("salario_minimo_usado"),
                    merged.get("exento_he_usado"),
                    merged.get("zona_salario_raw"),
                    merged.get("fuente_salario_operativo"),
                    merged.get("fuente_valor_x_he"),
                    merged.get("fuente_numero_empleado"),
                    merged.get("fuente_nss"),
                    merged.get("contpaq_match_status"),
                    merged.get("nomina_match_status"),
                    "manual_link",
                    RECORD_HEADCOUNT_CANONICAL,
                    json.dumps(merged.get("warnings") or [], ensure_ascii=False),
                    json.dumps(merged.get("editable_json") or {}, ensure_ascii=False),
                    now_iso,
                    int(target["id"]),
                ),
            )
            conn.execute(
                "UPDATE nomina_empleado_parametros SET is_active = 0, updated_at = ? WHERE id = ?",
                (now_iso, int(row_id)),
            )
        else:
            ed = dict(source_dict.get("editable_json") or {})
            ed["manual_headcount_nss"] = headcount_nss
            ed["manual_headcount_link_at"] = now_iso
            ed["manual_headcount_link_by"] = linked_by
            conn.execute(
                """
                UPDATE nomina_empleado_parametros SET
                    nombre = ?, nombre_normalizado = ?, nss = COALESCE(?, nss),
                    cliente = COALESCE(?, cliente),
                    headcount_match_status = 'manual_link',
                    record_kind = ?,
                    editable_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    headcount_nombre or source_dict.get("nombre"),
                    _norm_name(headcount_nombre or source_dict.get("nombre")),
                    headcount_nss or None,
                    headcount_cliente or None,
                    RECORD_HEADCOUNT_CANONICAL,
                    json.dumps(ed, ensure_ascii=False),
                    now_iso,
                    int(row_id),
                ),
            )
        conn.commit()
        return True
    finally:
        conn.close()
