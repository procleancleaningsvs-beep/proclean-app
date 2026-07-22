from __future__ import annotations

from typing import Any

from rapidfuzz import fuzz

from modules.comparativo import alias_service
from modules.exportacion_imss.exportacion_service import buscar_en_headcount, mapear_headcount_a_movimiento
from modules.gestion_idse_sua.nominas.planta_cliente_service import detect_planta_cliente_conflict
from modules.gestion_idse_sua.nominas.text_utils import json_dumps, normalize_name, normalize_upper


def _headcount_by_numero(headcount_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in headcount_rows:
        for key in ("numero_empleado", "codigo_contpaq", "num_empleado"):
            num = str(row.get(key) or "").strip()
            if num:
                out[num] = row
    return out


def _headcount_by_name(headcount_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in headcount_rows:
        name = normalize_name(row.get("nombre_completo"))
        if name:
            out.setdefault(name, []).append(row)
    return out


def _hc_key(row: dict[str, Any]) -> str:
    nss = str(row.get("nss") or "").strip()
    if nss:
        return f"nss:{nss}"
    name = normalize_name(row.get("nombre_completo"))
    return f"name:{name}"


def match_worker(
    worker: dict[str, Any],
    headcount_rows: list[dict[str, Any]],
    *,
    fuzzy_threshold: int = 88,
) -> dict[str, Any]:
    num = str(worker.get("num_empleado") or "").strip()
    nombre = normalize_name(worker.get("nombre_normalizado") or worker.get("nombre_original"))
    by_num = _headcount_by_numero(headcount_rows)
    by_name = _headcount_by_name(headcount_rows)

    if num and num in by_num:
        hc = by_num[num]
        return _build_match(hc, method="num_empleado", confidence=1.0, status="auto")

    alias = alias_service.obtener_alias(nombre) if nombre else None
    if alias:
        alias_norm = normalize_name(alias)
        options = by_name.get(alias_norm, [])
        if len(options) == 1:
            return _build_match(options[0], method="alias", confidence=0.98, status="confirmed")
        if len(options) > 1:
            return _homonym_result(nombre, options, method="alias")

    if nombre and nombre in by_name:
        options = by_name[nombre]
        if len(options) == 1:
            return _build_match(options[0], method="nombre_exacto", confidence=0.97, status="auto")
        return _homonym_result(nombre, options, method="nombre_exacto")

    best: tuple[float, dict[str, Any]] | None = None
    for hc in headcount_rows:
        hc_name = normalize_name(hc.get("nombre_completo"))
        if not hc_name or not nombre:
            continue
        score = float(fuzz.ratio(nombre, hc_name))
        if score >= fuzzy_threshold and (best is None or score > best[0]):
            best = (score, hc)
    if best:
        score, hc = best
        conflict = detect_planta_cliente_conflict(
            planta_cliente=str(worker.get("cliente_confirmado") or worker.get("cliente_sugerido") or ""),
            headcount_cliente=str(hc.get("cliente") or ""),
        )
        status = "review" if conflict else "suggested"
        return _build_match(hc, method="aproximado", confidence=score / 100.0, status=status)

    return {
        "match_method": "none",
        "confidence": 0.0,
        "status": "unmatched",
        "headcount_key": None,
        "nss": "",
        "rfc": "",
        "curp": "",
        "hc_nombre": "",
        "hc_json": None,
    }


def _homonym_result(nombre: str, options: list[dict[str, Any]], *, method: str) -> dict[str, Any]:
    return {
        "match_method": method,
        "confidence": 0.5,
        "status": "review",
        "headcount_key": None,
        "nss": "",
        "rfc": "",
        "curp": "",
        "hc_nombre": nombre,
        "hc_json": json_dumps(options[:5]),
        "homonym_options": options,
    }


def _build_match(
    hc: dict[str, Any],
    *,
    method: str,
    confidence: float,
    status: str,
) -> dict[str, Any]:
    mapped = mapear_headcount_a_movimiento(hc)
    return {
        "match_method": method,
        "confidence": confidence,
        "status": status,
        "headcount_key": _hc_key(hc),
        "nss": mapped.get("nss", ""),
        "rfc": mapped.get("rfc") or "",
        "curp": mapped.get("curp", ""),
        "hc_nombre": normalize_name(hc.get("nombre_completo")),
        "hc_json": json_dumps(hc),
        "hc_cliente": normalize_upper(hc.get("cliente")),
        "sbc": mapped.get("sbc", ""),
    }


def confirm_match(conn, worker_id: int, match: dict[str, Any]) -> None:
    from modules.gestion_idse_sua.nominas import repository as repo

    confirmed = dict(match)
    confirmed["status"] = "confirmed"
    repo.upsert_match(conn, worker_id, confirmed)


def manual_search(query: str, campo: str) -> dict[str, Any]:
    return buscar_en_headcount(query, campo)
