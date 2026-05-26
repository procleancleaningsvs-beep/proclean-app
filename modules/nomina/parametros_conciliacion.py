"""Bandeja operativa de conciliación — Microfase 2."""
from __future__ import annotations

import json
import sqlite3
from difflib import SequenceMatcher
from typing import Any

from modules.nomina.config import (
    WARN_DUPLICADO_CONTPAQ,
    WARN_DUPLICADO_NOMINA,
    WARN_EMPLEADO_ACTIVO_HC_BAJA_CONTPAQ,
    WARN_EMPLEADO_NOMINA_INACTIVO_HC,
    WARN_HEADCOUNT_ACTIVO_SIN_SALARIO,
    WARN_HEADCOUNT_ACTIVO_SIN_VALOR_HE,
    WARN_MATCH_DUDOSO_NOMBRE,
    WARN_NOMBRE_SIMILAR_NSS_DISTINTO,
    WARN_NSS_IGUAL_NOMBRE_DISTINTO,
    WARN_NOMINA_SIN_MATCH_HEADCOUNT,
    WARN_CONTPAQ_SIN_MATCH_HEADCOUNT,
)
from modules.nomina.parametros_audit import add_parametro_audit_event, get_parametro_audit_events
from modules.nomina.parametros_consolidado import (
    RECORD_EXTERNAL_CONTPAQ,
    RECORD_EXTERNAL_NOMINA,
    RECORD_HEADCOUNT_CANONICAL,
    append_conciliation_warnings,
    apply_manual_headcount_link,
    build_consolidado_view,
    build_legacy_parametros_view,
    classify_record_kind,
    filter_active_headcount,
)
from modules.nomina.parametros_match import (
    _norm_name,
    build_headcount_index,
    match_to_headcount,
)
from modules.nomina.vacaciones_util import resolve_status_headcount, sanitize_display_value

_CONFIDENT_HC_MATCH = {"exact_nss", "exact_name", "manual_link", "headcount_canonical"}

# Estados visibles de conciliación
ESTADO_COMPLETO = "Completo"
ESTADO_PENDIENTE_SALARIO = "Pendiente salario"
ESTADO_PENDIENTE_HE = "Pendiente valor HE"
ESTADO_PENDIENTE_VINCULO = "Pendiente vínculo"
ESTADO_MATCH_DUDOSO = "Match dudoso"
ESTADO_CONFLICTO = "Conflicto NSS/nombre"
ESTADO_DUPLICADO = "Duplicado"
ESTADO_IGNORADO = "Ignorado manualmente"
ESTADO_LEGACY = "Legacy"
ESTADO_EXTERNO = "Externo"
ESTADO_RESUELTO = "Resuelto"

_FILTER_MAP = {
    "todos": "todos_pendientes",
    "todos_pendientes": "todos_pendientes",
    "sin_vinculo": "sin_vinculo",
    "match_dudoso": "match_dudoso",
    "sin_salario": "sin_salario",
    "sin_he": "sin_he",
    "conflicto": "conflicto_nss",
    "conflicto_nss": "conflicto_nss",
    "duplicados": "duplicados",
    "inactivo_baja": "inactivo_baja",
    "ignorados": "warnings_ignorados",
    "warnings_ignorados": "warnings_ignorados",
    "resueltos": "resueltos",
}


def warning_code(raw: str) -> str:
    return str(raw or "").split(":")[0].strip()


def get_ignored_warnings_meta(editable_json: dict[str, Any] | None) -> list[dict[str, Any]]:
    ignored = (editable_json or {}).get("ignored_warnings") or []
    return [i for i in ignored if not i.get("reactivated_at")]


def get_ignored_codes(editable_json: dict[str, Any] | None) -> set[str]:
    return {str(i.get("code") or "") for i in get_ignored_warnings_meta(editable_json)}


def get_active_warnings(row: dict[str, Any]) -> list[str]:
    ignored = get_ignored_codes(row.get("editable_json") or {})
    return [w for w in (row.get("warnings") or []) if warning_code(w) not in ignored]


def get_ignored_warnings_display(row: dict[str, Any]) -> list[dict[str, Any]]:
    return get_ignored_warnings_meta(row.get("editable_json") or {})


def _record_tipo_label(row: dict[str, Any]) -> str:
    if row.get("is_legacy_view"):
        return "Legacy"
    if row.get("is_canonical"):
        return "Headcount"
    kind = str(row.get("record_kind") or "")
    if kind == RECORD_EXTERNAL_NOMINA:
        return "Nómina externa"
    if kind == RECORD_EXTERNAL_CONTPAQ:
        return "CONTPAQ externo"
    if row.get("is_external"):
        return "Externo"
    return "Headcount"


def derive_estado_conciliacion(row: dict[str, Any]) -> str:
    if row.get("is_legacy_view"):
        return ESTADO_LEGACY
    active = get_active_warnings(row)
    ignored = get_ignored_warnings_meta(row.get("editable_json") or {})
    ms = str(row.get("headcount_match_status") or "")

    if row.get("is_external") or ms in {"no_match_headcount", "pending_review"}:
        if not active and ignored:
            return ESTADO_IGNORADO
        return ESTADO_EXTERNO if row.get("is_external") else ESTADO_PENDIENTE_VINCULO

    if any(warning_code(w) == WARN_DUPLICADO_NOMINA for w in active) or any(
        warning_code(w) == WARN_DUPLICADO_CONTPAQ for w in active
    ):
        return ESTADO_DUPLICADO
    if any(
        warning_code(w) in {WARN_NSS_IGUAL_NOMBRE_DISTINTO, WARN_NOMBRE_SIMILAR_NSS_DISTINTO} for w in active
    ):
        return ESTADO_CONFLICTO
    if ms in {"probable_match", "multiple_candidates"} or any(
        warning_code(w) == WARN_MATCH_DUDOSO_NOMBRE for w in active
    ):
        return ESTADO_MATCH_DUDOSO
    if any(warning_code(w) == WARN_HEADCOUNT_ACTIVO_SIN_SALARIO for w in active):
        return ESTADO_PENDIENTE_SALARIO
    if any(warning_code(w) == WARN_HEADCOUNT_ACTIVO_SIN_VALOR_HE for w in active):
        return ESTADO_PENDIENTE_HE
    if ms in {"inactive_headcount"} or any(
        warning_code(w) in {WARN_EMPLEADO_NOMINA_INACTIVO_HC, WARN_EMPLEADO_ACTIVO_HC_BAJA_CONTPAQ} for w in active
    ):
        return ESTADO_PENDIENTE_VINCULO
    if not active and ignored:
        return ESTADO_IGNORADO
    if not active and ms in _CONFIDENT_HC_MATCH | {"headcount_canonical"}:
        return ESTADO_COMPLETO if row.get("is_canonical") else ESTADO_RESUELTO
    if active:
        return ESTADO_PENDIENTE_VINCULO
    return ESTADO_RESUELTO


def _primary_warning(row: dict[str, Any]) -> str:
    active = get_active_warnings(row)
    if active:
        return active[0]
    ignored = get_ignored_warnings_meta(row.get("editable_json") or {})
    if ignored:
        return f"{ignored[-1].get('code')} (ignorado)"
    return "—"


def _problem_source(row: dict[str, Any]) -> str:
    estado = derive_estado_conciliacion(row)
    if estado == ESTADO_EXTERNO:
        kind = str(row.get("record_kind") or "")
        if kind == RECORD_EXTERNAL_NOMINA:
            return "Nómina sin match Headcount"
        if kind == RECORD_EXTERNAL_CONTPAQ:
            return "CONTPAQ sin match Headcount"
        return "Sin vínculo Headcount"
    if estado == ESTADO_MATCH_DUDOSO:
        return "Match automático dudoso"
    if estado == ESTADO_CONFLICTO:
        return "Conflicto NSS/nombre"
    if estado == ESTADO_DUPLICADO:
        return "Duplicado en importación"
    if estado in {ESTADO_PENDIENTE_SALARIO, ESTADO_PENDIENTE_HE}:
        return "Headcount activo incompleto"
    if estado == ESTADO_LEGACY:
        return "Headcount no disponible"
    return estado


def _enrich_inbox_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["tipo_registro"] = _record_tipo_label(row)
    out["estado_conciliacion"] = derive_estado_conciliacion(row)
    out["warning_principal"] = _primary_warning(row)
    out["active_warnings"] = get_active_warnings(row)
    out["active_warning_count"] = len(out["active_warnings"])
    out["ignored_warnings"] = get_ignored_warnings_display(row)
    out["fuente_problema"] = _problem_source(row)
    return out


def _matches_filter(row: dict[str, Any], filt: str) -> bool:
    estado = row.get("estado_conciliacion") or derive_estado_conciliacion(row)
    active = get_active_warnings(row)
    codes = {warning_code(w) for w in active}
    ms = str(row.get("headcount_match_status") or "")

    if filt in {"", "todos_pendientes"}:
        return bool(active) or row.get("is_external") or ms in {
            "no_match_headcount",
            "probable_match",
            "multiple_candidates",
            "pending_review",
            "inactive_headcount",
        }
    if filt == "sin_vinculo":
        return row.get("is_external") or ms == "no_match_headcount" or any(
            warning_code(w) in {WARN_NOMINA_SIN_MATCH_HEADCOUNT, WARN_CONTPAQ_SIN_MATCH_HEADCOUNT} for w in active
        )
    if filt == "match_dudoso":
        return ms in {"probable_match", "multiple_candidates"} or WARN_MATCH_DUDOSO_NOMBRE in codes
    if filt == "sin_salario":
        return WARN_HEADCOUNT_ACTIVO_SIN_SALARIO in codes
    if filt == "sin_he":
        return WARN_HEADCOUNT_ACTIVO_SIN_VALOR_HE in codes
    if filt == "conflicto_nss":
        return bool(codes & {WARN_NSS_IGUAL_NOMBRE_DISTINTO, WARN_NOMBRE_SIMILAR_NSS_DISTINTO})
    if filt == "duplicados":
        return bool(codes & {WARN_DUPLICADO_NOMINA, WARN_DUPLICADO_CONTPAQ})
    if filt == "inactivo_baja":
        return ms == "inactive_headcount" or bool(
            codes & {WARN_EMPLEADO_NOMINA_INACTIVO_HC, WARN_EMPLEADO_ACTIVO_HC_BAJA_CONTPAQ}
        )
    if filt == "warnings_ignorados":
        return bool(get_ignored_warnings_meta(row.get("editable_json") or {}))
    if filt == "resueltos":
        return not active and estado in {ESTADO_COMPLETO, ESTADO_RESUELTO, ESTADO_IGNORADO}
    return True


def _matches_search(row: dict[str, Any], q: str) -> bool:
    if not q:
        return True
    qn = _norm_name(q)
    ql = q.strip().lower()
    fields = [
        row.get("nombre"),
        row.get("nss"),
        row.get("cliente"),
        row.get("planta"),
        row.get("numero_empleado"),
        row.get("codigo_contpaq"),
    ]
    for f in fields:
        fv = str(f or "")
        if ql in fv.lower() or (qn and qn in _norm_name(fv)):
            return True
    return False


def build_conciliacion_inbox(
    db_path: str,
    headcount_rows: list[dict[str, Any]],
    *,
    headcount_unavailable: bool = False,
    filtro: str = "todos_pendientes",
    search: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    filt = _FILTER_MAP.get((filtro or "").strip().lower(), filtro or "todos_pendientes")
    if headcount_unavailable:
        base = build_legacy_parametros_view(db_path, limit=5000)
    else:
        base = build_consolidado_view(db_path, headcount_rows, include_external=True, limit=5000)
    rows = [_enrich_inbox_row(r) for r in base]
    rows = [r for r in rows if _matches_filter(r, filt)]
    rows = [r for r in rows if _matches_search(r, search)]
    rows.sort(key=lambda r: (r.get("estado_conciliacion") == ESTADO_COMPLETO, r.get("nombre") or ""))
    return rows[: int(limit)]


def suggest_headcount_matches(
    row: dict[str, Any],
    headcount_rows: list[dict[str, Any]],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    nombre = _norm_name(row.get("nombre"))
    nss = sanitize_display_value(row.get("nss"))
    cliente = _norm_name(row.get("cliente"))
    planta = _norm_name(row.get("planta"))
    tokens = [t for t in nombre.split() if len(t) >= 3]

    suggestions: list[dict[str, Any]] = []
    for hc in filter_active_headcount(headcount_rows):
        hc_nombre = _norm_name(hc.get("nombre_completo"))
        hc_nss = sanitize_display_value(hc.get("nss"))
        hc_cliente = _norm_name(hc.get("cliente"))
        hc_planta = _norm_name(hc.get("patron"))
        reasons: list[str] = []
        score = 0.0

        if nss and hc_nss and nss == hc_nss:
            reasons.append("NSS exacto")
            score = max(score, 1.0)
        sim = SequenceMatcher(None, nombre, hc_nombre).ratio() if nombre and hc_nombre else 0.0
        hc_tokens = hc_nombre.split() if hc_nombre else []
        if nombre and hc_nombre and nombre != hc_nombre and all(t in hc_tokens for t in tokens if t):
            reasons.append("Nombre contenido en Headcount")
            score = max(score, 0.82 + min(len(tokens), 3) * 0.04)
        if sim >= 0.75:
            reasons.append("Nombre similar")
            score = max(score, sim)
        token_hits = sum(1 for t in tokens if t in hc_tokens)
        if token_hits >= 2:
            reasons.append("Tokens coincidentes")
            score = max(score, 0.7 + token_hits * 0.05)
        if cliente and hc_cliente and cliente == hc_cliente:
            reasons.append("Mismo cliente")
            score += 0.08
        if planta and hc_planta and planta == hc_planta:
            reasons.append("Misma planta")
            score += 0.15
        elif planta and hc_planta and planta != hc_planta and sim >= 0.85:
            score -= 0.12

        if score < 0.65 and not reasons:
            continue

        if score >= 0.92:
            etiqueta = "Alta"
        elif score >= 0.78:
            etiqueta = "Media"
        else:
            etiqueta = "Baja"

        suggestions.append(
            {
                "nombre_completo": hc.get("nombre_completo"),
                "nss": hc_nss,
                "cliente": hc.get("cliente"),
                "planta": hc.get("patron"),
                "puesto": hc.get("puesto"),
                "score": round(min(score, 1.0), 3),
                "etiqueta": etiqueta,
                "razones": reasons,
            }
        )

    suggestions.sort(key=lambda s: (-s["score"], s.get("nombre_completo") or ""))
    return suggestions[:limit]


def _save_param_row(
    conn: sqlite3.Connection,
    row_id: int,
    *,
    updates: dict[str, Any],
    now_iso: str,
    user_id: int | None = None,
) -> None:
    fields = []
    params: list[Any] = []
    for key in (
        "headcount_match_status",
        "contpaq_match_status",
        "nomina_match_status",
        "record_kind",
        "warnings_json",
        "editable_json",
        "nss",
        "cliente",
        "nombre",
        "nombre_normalizado",
    ):
        if key in updates:
            val = updates[key]
            if key in {"warnings_json", "editable_json"} and isinstance(val, (list, dict)):
                val = json.dumps(val, ensure_ascii=False)
            fields.append(f"{key} = ?")
            params.append(val)
    if user_id is not None:
        fields.append("updated_by = ?")
        params.append(user_id)
    fields.append("updated_at = ?")
    params.append(now_iso)
    params.append(int(row_id))
    conn.execute(
        f"UPDATE nomina_empleado_parametros SET {', '.join(fields)} WHERE id = ?",
        tuple(params),
    )


def _hc_row_for_param(row: dict[str, Any], headcount_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    ed = row.get("editable_json") or {}
    nss = sanitize_display_value(ed.get("manual_headcount_nss")) or sanitize_display_value(row.get("nss"))
    if nss:
        for hc in headcount_rows:
            if sanitize_display_value(hc.get("nss")) == nss:
                return hc
    nombre = _norm_name(row.get("nombre"))
    cliente = _norm_name(row.get("cliente"))
    for hc in headcount_rows:
        if _norm_name(hc.get("nombre_completo")) == nombre and _norm_name(hc.get("cliente")) == cliente:
            return hc
    return None


def rematch_parametro_row(
    db_path: str,
    row_id: int,
    headcount_rows: list[dict[str, Any]],
    *,
    user_id: int | None,
    now_iso: str,
) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        raw = conn.execute(
            "SELECT * FROM nomina_empleado_parametros WHERE id = ? AND COALESCE(is_active,1)=1",
            (int(row_id),),
        ).fetchone()
        if raw is None:
            return {"ok": False, "error": "Registro no encontrado"}
        row = dict(raw)
        row["warnings"] = json.loads(row.get("warnings_json") or "[]")
        row["editable_json"] = json.loads(row.get("editable_json") or "{}")

        ed = dict(row["editable_json"])
        manual_nss = sanitize_display_value(ed.get("manual_headcount_nss"))
        hc_index = build_headcount_index(headcount_rows)

        if manual_nss:
            ms, hc, score = "manual_link", None, 1.0
            for hc_c in headcount_rows:
                if sanitize_display_value(hc_c.get("nss")) == manual_nss:
                    hc = hc_c
                    break
        else:
            source = "NOMINA_ACTUAL" if row.get("nomina_match_status") == "imported" else "CONTPAQ"
            ms, hc, score = match_to_headcount(
                nombre=row.get("nombre"),
                nss=row.get("nss"),
                cliente=row.get("cliente"),
                index=hc_index,
                numero_empleado=row.get("numero_empleado"),
                codigo_contpaq=row.get("codigo_contpaq"),
            )
            if ms in {"probable_match", "multiple_candidates"}:
                ms = "pending_review"

        kind = classify_record_kind(
            source="NOMINA_ACTUAL" if row.get("nomina_match_status") == "imported" else "CONTPAQ",
            headcount_match_status=ms if not manual_nss else "manual_link",
        )
        if manual_nss:
            kind = RECORD_HEADCOUNT_CANONICAL

        row["headcount_match_status"] = ms if not manual_nss else "manual_link"
        row["record_kind"] = kind
        append_conciliation_warnings(row, hc_row=hc)
        ed = add_parametro_audit_event(
            ed,
            action="recalcular_fila",
            user_id=user_id,
            now_iso=now_iso,
            detail={"match_status": row["headcount_match_status"], "score": score},
        )
        _save_param_row(
            conn,
            int(row_id),
            updates={
                "headcount_match_status": row["headcount_match_status"],
                "record_kind": row["record_kind"],
                "warnings_json": row.get("warnings") or [],
                "editable_json": ed,
            },
            now_iso=now_iso,
            user_id=user_id,
        )
        conn.commit()
        return {"ok": True, "match_status": row["headcount_match_status"], "record_kind": kind}
    finally:
        conn.close()


def post_import_rematch_controlled(
    db_path: str,
    *,
    import_id: int,
    headcount_rows: list[dict[str, Any]],
    now_iso: str,
) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    summary = {
        "exactos": 0,
        "manual_link": 0,
        "probables_revision": 0,
        "externos_sin_vinculo": 0,
        "duplicados": 0,
    }
    try:
        rows = conn.execute(
            """SELECT * FROM nomina_empleado_parametros
               WHERE COALESCE(is_active,1)=1 AND last_import_id = ?""",
            (int(import_id),),
        ).fetchall()
        hc_index = build_headcount_index(headcount_rows)
        for raw in rows:
            row = dict(raw)
            ed = json.loads(row.get("editable_json") or "{}")
            warnings = json.loads(row.get("warnings_json") or "[]")
            if ed.get("manual_headcount_nss"):
                summary["manual_link"] += 1
                continue
            ms, hc, _ = match_to_headcount(
                nombre=row.get("nombre"),
                nss=row.get("nss"),
                cliente=row.get("cliente"),
                index=hc_index,
                numero_empleado=row.get("numero_empleado"),
                codigo_contpaq=row.get("codigo_contpaq"),
            )
            source = "NOMINA_ACTUAL" if row.get("nomina_match_status") == "imported" else "CONTPAQ"
            if ms in {"exact_nss", "exact_name"}:
                kind = RECORD_HEADCOUNT_CANONICAL
                summary["exactos"] += 1
            elif ms in {"probable_match", "multiple_candidates", "inactive_headcount"}:
                ms = "pending_review"
                kind = classify_record_kind(source=source, headcount_match_status=ms)
                summary["probables_revision"] += 1
            elif ms == "no_match_headcount":
                kind = classify_record_kind(source=source, headcount_match_status=ms)
                summary["externos_sin_vinculo"] += 1
            else:
                kind = classify_record_kind(source=source, headcount_match_status=ms)

            row_dict = {**row, "warnings": warnings, "editable_json": ed}
            row_dict["headcount_match_status"] = ms
            row_dict["record_kind"] = kind
            append_conciliation_warnings(row_dict, hc_row=hc)
            if any(
                warning_code(w) in {WARN_DUPLICADO_NOMINA, WARN_DUPLICADO_CONTPAQ}
                for w in (row_dict.get("warnings") or [])
            ):
                summary["duplicados"] += 1

            conn.execute(
                """UPDATE nomina_empleado_parametros SET
                   headcount_match_status = ?, record_kind = ?, warnings_json = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    ms,
                    kind,
                    json.dumps(row_dict.get("warnings") or [], ensure_ascii=False),
                    now_iso,
                    int(row["id"]),
                ),
            )
        conn.commit()
        return summary
    finally:
        conn.close()


def ignore_parametro_warning(
    db_path: str,
    row_id: int,
    *,
    warning_codes: list[str],
    motivo: str,
    user_id: int | None,
    now_iso: str,
) -> bool:
    if not motivo.strip() or not warning_codes:
        return False
    conn = sqlite3.connect(db_path)
    try:
        raw = conn.execute(
            "SELECT warnings_json, editable_json FROM nomina_empleado_parametros WHERE id = ?",
            (int(row_id),),
        ).fetchone()
        if raw is None:
            return False
        warnings = json.loads(raw[0] or "[]")
        ed = json.loads(raw[1] or "{}")
        ignored = list(ed.get("ignored_warnings") or [])
        for code in warning_codes:
            ignored.append(
                {
                    "code": code,
                    "motivo": motivo.strip(),
                    "ignored_by": user_id,
                    "ignored_at": now_iso,
                }
            )
        ed["ignored_warnings"] = ignored
        ed = add_parametro_audit_event(
            ed,
            action="ignorar_warning",
            user_id=user_id,
            now_iso=now_iso,
            detail={"codes": warning_codes, "motivo": motivo.strip()},
        )
        conn.execute(
            "UPDATE nomina_empleado_parametros SET editable_json = ?, updated_by = ?, updated_at = ? WHERE id = ?",
            (json.dumps(ed, ensure_ascii=False), user_id, now_iso, int(row_id)),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def reactivate_parametro_warning(
    db_path: str,
    row_id: int,
    *,
    warning_code_str: str,
    user_id: int | None,
    now_iso: str,
) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        raw = conn.execute(
            "SELECT editable_json FROM nomina_empleado_parametros WHERE id = ?",
            (int(row_id),),
        ).fetchone()
        if raw is None:
            return False
        ed = json.loads(raw[0] or "{}")
        ignored = list(ed.get("ignored_warnings") or [])
        updated = False
        for item in ignored:
            if str(item.get("code") or "") == warning_code_str and not item.get("reactivated_at"):
                item["reactivated_at"] = now_iso
                item["reactivated_by"] = user_id
                updated = True
        if not updated:
            return False
        ed["ignored_warnings"] = ignored
        ed = add_parametro_audit_event(
            ed,
            action="reactivar_warning",
            user_id=user_id,
            now_iso=now_iso,
            detail={"code": warning_code_str},
        )
        conn.execute(
            "UPDATE nomina_empleado_parametros SET editable_json = ?, updated_by = ?, updated_at = ? WHERE id = ?",
            (json.dumps(ed, ensure_ascii=False), user_id, now_iso, int(row_id)),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def correct_manual_headcount_link(
    db_path: str,
    row_id: int,
    *,
    new_headcount_nss: str,
    new_headcount_nombre: str,
    new_headcount_cliente: str,
    linked_by: int | None,
    now_iso: str,
) -> bool:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        source = conn.execute(
            "SELECT * FROM nomina_empleado_parametros WHERE id = ?",
            (int(row_id),),
        ).fetchone()
        if source is None:
            return False
        source_dict = dict(source)
        source_dict["warnings"] = json.loads(source_dict.get("warnings_json") or "[]")
        source_dict["editable_json"] = json.loads(source_dict.get("editable_json") or "{}")
        old_nss = sanitize_display_value(source_dict["editable_json"].get("manual_headcount_nss"))

        merge_source = source_dict
        source_active = source_dict.get("is_active")
        if source_active is None:
            source_active = 1
        if not old_nss and int(source_active) == 0:
            linked_canonical = conn.execute(
                """SELECT * FROM nomina_empleado_parametros
                   WHERE COALESCE(is_active,1)=1 AND headcount_match_status = 'manual_link'
                   ORDER BY id ASC""",
            ).fetchall()
            for cand in linked_canonical:
                cand_ed = json.loads(cand["editable_json"] or "{}")
                if int(cand_ed.get("linked_from_row_id") or 0) == int(row_id):
                    old_nss = sanitize_display_value(cand["nss"])
                    merge_source = source_dict
                    break
        elif not old_nss and str(source_dict.get("headcount_match_status") or "") == "manual_link":
            old_nss = sanitize_display_value(source_dict.get("nss"))
            linked_ext_id = source_dict["editable_json"].get("linked_from_row_id")
            if linked_ext_id:
                ext = conn.execute(
                    "SELECT * FROM nomina_empleado_parametros WHERE id = ?",
                    (int(linked_ext_id),),
                ).fetchone()
                if ext is not None:
                    merge_source = dict(ext)
                    merge_source["warnings"] = json.loads(merge_source.get("warnings_json") or "[]")
                    merge_source["editable_json"] = json.loads(merge_source.get("editable_json") or "{}")

        if old_nss and old_nss != new_headcount_nss:
            old_target = conn.execute(
                """SELECT * FROM nomina_empleado_parametros
                   WHERE COALESCE(is_active,1)=1 AND nss = ? AND id != ?
                   ORDER BY id ASC LIMIT 1""",
                (old_nss, int(row_id)),
            ).fetchone()
            if old_target is not None:
                old_d = dict(old_target)
                old_ed = json.loads(old_d.get("editable_json") or "{}")
                old_warnings = json.loads(old_d.get("warnings_json") or "[]")
                note = "Vínculo manual corregido: revisar enriquecimiento previo."
                if note not in old_warnings:
                    old_warnings.append(note)
                old_ed = add_parametro_audit_event(
                    old_ed,
                    action="vinculo_corregido_origen",
                    user_id=linked_by,
                    now_iso=now_iso,
                    detail={"old_nss": old_nss, "new_nss": new_headcount_nss, "from_row_id": int(row_id)},
                )
                conn.execute(
                    """UPDATE nomina_empleado_parametros SET
                       warnings_json = ?, editable_json = ?, headcount_match_status = 'pending_review', updated_at = ?
                       WHERE id = ?""",
                    (
                        json.dumps(old_warnings, ensure_ascii=False),
                        json.dumps(old_ed, ensure_ascii=False),
                        now_iso,
                        int(old_target["id"]),
                    ),
                )
        conn.commit()
    finally:
        conn.close()

    link_row_id = int(row_id)
    merge_active = merge_source.get("is_active")
    if merge_active is None:
        merge_active = 1
    if int(merge_active) == 0:
        link_row_id = int(merge_source["id"])
        conn_prep = sqlite3.connect(db_path)
        try:
            conn_prep.execute(
                "UPDATE nomina_empleado_parametros SET is_active = 1, updated_at = ? WHERE id = ?",
                (now_iso, link_row_id),
            )
            conn_prep.commit()
        finally:
            conn_prep.close()

    ok = apply_manual_headcount_link(
        db_path,
        link_row_id,
        headcount_nss=new_headcount_nss,
        headcount_nombre=new_headcount_nombre,
        headcount_cliente=new_headcount_cliente,
        linked_by=linked_by,
        now_iso=now_iso,
    )
    if ok:
        conn2 = sqlite3.connect(db_path)
        try:
            raw = conn2.execute("SELECT editable_json FROM nomina_empleado_parametros WHERE id = ?", (int(row_id),)).fetchone()
            if raw:
                ed = add_parametro_audit_event(
                    json.loads(raw[0] or "{}"),
                    action="corregir_vinculo",
                    user_id=linked_by,
                    now_iso=now_iso,
                    detail={"new_nss": new_headcount_nss, "old_nss": old_nss},
                )
                conn2.execute(
                    "UPDATE nomina_empleado_parametros SET editable_json = ? WHERE id = ?",
                    (json.dumps(ed, ensure_ascii=False), int(row_id)),
                )
                conn2.commit()
        finally:
            conn2.close()
    return ok


def get_parametros_import(db_path: str, import_id: int) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM nomina_parametros_imports WHERE id = ?", (int(import_id),)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["raw_json"] = json.loads(d.get("raw_json") or "{}")
        return d
    finally:
        conn.close()


def build_parametro_detail(
    db_path: str,
    row_id: int,
    headcount_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        raw = conn.execute(
            "SELECT * FROM nomina_empleado_parametros WHERE id = ?", (int(row_id),)
        ).fetchone()
        if raw is None:
            return None
        row = dict(raw)
        row["warnings"] = json.loads(row.get("warnings_json") or "[]")
        row["editable_json"] = json.loads(row.get("editable_json") or "{}")
    finally:
        conn.close()

    kind = str(row.get("record_kind") or "")
    ms = str(row.get("headcount_match_status") or "")
    row["is_canonical"] = kind == RECORD_HEADCOUNT_CANONICAL and ms in _CONFIDENT_HC_MATCH
    row["is_external"] = kind in {RECORD_EXTERNAL_NOMINA, RECORD_EXTERNAL_CONTPAQ} or ms in {
        "no_match_headcount",
        "probable_match",
        "multiple_candidates",
        "pending_review",
        "inactive_headcount",
    }

    hc = _hc_row_for_param(row, headcount_rows)
    imp = None
    if row.get("last_import_id"):
        imp = get_parametros_import(db_path, int(row["last_import_id"]))

    ed = row.get("editable_json") or {}
    contpaq_extra = ed.get("contpaq_extra") or {}

    detail = {
        "id": row.get("id"),
        "estado_conciliacion": derive_estado_conciliacion(row),
        "tipo_registro": _record_tipo_label(row),
        "headcount": {
            "nombre": hc.get("nombre_completo") if hc else row.get("nombre"),
            "nss": sanitize_display_value(hc.get("nss") if hc else row.get("nss")),
            "cliente": hc.get("cliente") if hc else row.get("cliente"),
            "planta": hc.get("patron") if hc else row.get("planta"),
            "puesto": hc.get("puesto") if hc else row.get("puesto"),
            "fecha_ingreso": hc.get("fecha_ingreso") if hc else None,
            "estado": resolve_status_headcount(hc) if hc else "—",
        },
        "nomina": {
            "nombre": row.get("nombre"),
            "numero_empleado": row.get("numero_empleado"),
            "salario_operativo": row.get("salario_operativo"),
            "valor_x_he": row.get("valor_x_he"),
            "banco": row.get("banco"),
            "cuenta": row.get("cuenta"),
            "cliente": row.get("cliente"),
            "archivo": row.get("fuente_salario_operativo") or row.get("fuente_valor_x_he"),
            "importacion_fecha": imp.get("created_at") if imp else None,
        },
        "contpaq": {
            "nombre": row.get("nombre"),
            "codigo": row.get("codigo_contpaq"),
            "nss": row.get("nss"),
            "estatus": contpaq_extra.get("estatus"),
            "registro_patronal": contpaq_extra.get("registro_patronal"),
            "zona_salario": row.get("zona_salario_raw"),
            "fecha_alta": contpaq_extra.get("fecha_alta"),
            "fecha_baja": contpaq_extra.get("fecha_baja"),
            "archivo": row.get("fuente_numero_empleado") or row.get("fuente_nss"),
            "importacion_fecha": imp.get("created_at") if imp else None,
        },
        "conciliacion": {
            "warnings_activos": get_active_warnings(row),
            "warnings_ignorados": get_ignored_warnings_display(row),
            "vinculo_manual_nss": ed.get("manual_headcount_nss"),
            "vinculo_manual_at": ed.get("manual_headcount_link_at"),
            "vinculo_manual_by": ed.get("manual_headcount_link_by"),
            "headcount_match_status": row.get("headcount_match_status"),
            "contpaq_match_status": row.get("contpaq_match_status"),
            "nomina_match_status": row.get("nomina_match_status"),
            "updated_at": row.get("updated_at"),
            "audit_events": get_parametro_audit_events(ed),
        },
        "sugerencias": suggest_headcount_matches(row, headcount_rows, limit=5),
    }
    return detail
