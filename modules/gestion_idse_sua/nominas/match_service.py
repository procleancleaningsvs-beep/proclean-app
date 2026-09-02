from __future__ import annotations

import json
from typing import Any

from rapidfuzz import fuzz

from modules.comparativo import alias_service
from modules.exportacion_imss.exportacion_service import mapear_headcount_a_movimiento
from modules.gestion_idse_sua.nominas.text_utils import json_dumps, normalize_name, normalize_upper


STRONG_IDENTIFIERS = (
    ("nss", ("nss",), ("nss",)),
    ("curp", ("curp",), ("curp",)),
    ("rfc", ("rfc", "rfc_homoclave"), ("rfc_homoclave", "rfc")),
    (
        "num_empleado",
        ("num_empleado", "numero_empleado", "codigo_contpaq"),
        ("numero_empleado", "codigo_contpaq", "num_empleado"),
    ),
)


def _row_json(worker: dict[str, Any]) -> dict[str, Any]:
    raw = worker.get("row_json")
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _first_value(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return ""


def _worker_value(worker: dict[str, Any], keys: tuple[str, ...]) -> Any:
    direct = _first_value(worker, keys)
    return direct if direct not in (None, "") else _first_value(_row_json(worker), keys)


def _normalize_identifier(value: Any) -> str:
    return "".join(ch for ch in normalize_upper(value) if ch.isalnum())


def _headcount_by_name(headcount_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in headcount_rows:
        name = normalize_name(row.get("nombre_completo"))
        if name:
            out.setdefault(name, []).append(row)
    return out


def _hc_key(row: dict[str, Any]) -> str:
    for field, keys in (
        ("nss", ("nss",)),
        ("curp", ("curp",)),
        ("rfc", ("rfc_homoclave", "rfc")),
        ("empleado", ("numero_empleado", "codigo_contpaq", "num_empleado")),
    ):
        value = _normalize_identifier(_first_value(row, keys))
        if value:
            return f"{field}:{value}"
    name = normalize_name(row.get("nombre_completo"))
    client = normalize_upper(row.get("cliente"))
    location = normalize_upper(row.get("ubicacion") or row.get("planta"))
    if name:
        return f"name:{name}|client:{client}|location:{location}"
    source_id = str(row.get("headcount_id") or "").strip()
    return f"headcount:{source_id}" if source_id else "headcount:unknown"


def _candidate_payload(row: dict[str, Any], reason: str) -> dict[str, Any]:
    fields = {
        "headcount_key": _hc_key(row),
        "nombre_completo": normalize_name(row.get("nombre_completo")),
        "cliente": normalize_upper(row.get("cliente")),
        "ubicacion": str(row.get("ubicacion") or row.get("planta") or "").strip(),
        "numero_empleado": str(
            row.get("numero_empleado") or row.get("codigo_contpaq") or row.get("num_empleado") or ""
        ).strip(),
        "nss": str(row.get("nss") or "").strip(),
        "rfc": normalize_upper(row.get("rfc_homoclave") or row.get("rfc")),
        "curp": normalize_upper(row.get("curp")),
        "puesto": str(row.get("puesto") or "").strip(),
        "status_operacion": normalize_upper(row.get("status_operacion")),
        "status_imss": normalize_upper(row.get("status_imss")),
        "candidate_reason": reason,
    }
    return {key: value for key, value in fields.items() if value not in (None, "")}


def build_review_match(
    candidates: list[dict[str, Any]],
    *,
    method: str,
    reason: str,
    confidence: float = 0.5,
) -> dict[str, Any]:
    payloads = [_candidate_payload(candidate, reason) for candidate in candidates[:5]]
    return {
        "match_method": method,
        "confidence": confidence,
        "status": "review",
        "headcount_key": None,
        "nss": "",
        "rfc": "",
        "curp": "",
        "hc_nombre": "",
        "hc_json": json_dumps(payloads),
        "homonym_options": candidates[:5],
    }


def _strong_identifier_match(
    worker: dict[str, Any],
    headcount_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    provided: list[tuple[str, str, tuple[str, ...]]] = []
    hits: dict[int, tuple[dict[str, Any], set[str]]] = {}
    for method, worker_keys, headcount_keys in STRONG_IDENTIFIERS:
        worker_value = _normalize_identifier(_worker_value(worker, worker_keys))
        if not worker_value:
            continue
        provided.append((method, worker_value, headcount_keys))
        for index, row in enumerate(headcount_rows):
            hc_value = _normalize_identifier(_first_value(row, headcount_keys))
            if hc_value != worker_value:
                continue
            if index not in hits:
                hits[index] = (row, set())
            hits[index][1].add(method)

    if not hits:
        return None
    candidates = [row for row, _ in hits.values()]
    if len(hits) > 1:
        return build_review_match(
            candidates,
            method="identificadores_en_conflicto",
            reason="Identificadores fuertes apuntan a personas distintas",
            confidence=1.0,
        )

    row, methods = next(iter(hits.values()))
    contradictions = []
    for method, worker_value, headcount_keys in provided:
        hc_value = _normalize_identifier(_first_value(row, headcount_keys))
        if hc_value and hc_value != worker_value:
            contradictions.append(method)
    if contradictions:
        return build_review_match(
            [row],
            method="identificadores_en_conflicto",
            reason=f"Contradicción en identificador: {', '.join(sorted(contradictions))}",
            confidence=1.0,
        )
    method = sorted(methods, key=lambda item: [field[0] for field in STRONG_IDENTIFIERS].index(item))[0]
    return _build_match(row, method=method, confidence=1.0, status="auto")


def _exact_identity_match(
    worker: dict[str, Any],
    row: dict[str, Any],
    *,
    method: str,
    confidence: float,
    status: str,
) -> dict[str, Any]:
    contradictions = []
    for identifier, worker_keys, headcount_keys in STRONG_IDENTIFIERS:
        worker_value = _normalize_identifier(_worker_value(worker, worker_keys))
        hc_value = _normalize_identifier(_first_value(row, headcount_keys))
        if worker_value and hc_value and worker_value != hc_value:
            contradictions.append(identifier)
    if contradictions:
        return build_review_match(
            [row],
            method="identificadores_en_conflicto",
            reason=f"Nombre coincidente, pero contradicción en: {', '.join(sorted(contradictions))}",
            confidence=1.0,
        )
    return _build_match(row, method=method, confidence=confidence, status=status)


def _name_candidate(
    worker: dict[str, Any],
    headcount: dict[str, Any],
    *,
    fuzzy_threshold: int,
) -> tuple[float, str] | None:
    worker_name = normalize_name(
        worker.get("nombre_normalizado") or worker.get("nombre_original")
    )
    headcount_name = normalize_name(headcount.get("nombre_completo"))
    worker_tokens = set(worker_name.split())
    headcount_tokens = set(headcount_name.split())
    overlap = worker_tokens & headcount_tokens
    if not worker_tokens or not headcount_tokens:
        return None

    if worker_tokens == headcount_tokens:
        return 1.0, "Mismos componentes del nombre en orden distinto"
    if worker_tokens <= headcount_tokens or headcount_tokens <= worker_tokens:
        return 0.96, "Nombre incompleto compatible"

    token_score = max(
        float(fuzz.token_set_ratio(worker_name, headcount_name)),
        float(fuzz.token_sort_ratio(worker_name, headcount_name)),
        float(fuzz.ratio(worker_name, headcount_name)),
    )
    worker_list = worker_name.split()
    headcount_list = headcount_name.split()
    approximate_pairs = 0
    remaining = list(headcount_list)
    for token in worker_list:
        if not remaining:
            break
        best_index = max(
            range(len(remaining)), key=lambda index: fuzz.ratio(token, remaining[index])
        )
        best_score = fuzz.ratio(token, remaining[best_index])
        if float(best_score) >= 80.0:
            approximate_pairs += 1
            remaining.pop(best_index)

    worker_client = normalize_upper(
        worker.get("cliente_confirmado") or worker.get("cliente_sugerido")
    )
    headcount_client = normalize_upper(headcount.get("cliente"))
    worker_location = normalize_upper(
        worker.get("planta_normalizada") or worker.get("planta_original")
    )
    headcount_location = normalize_upper(
        headcount.get("ubicacion") or headcount.get("planta")
    )
    same_client = bool(worker_client and worker_client == headcount_client)
    same_location = bool(worker_location and worker_location == headcount_location)
    same_first_name = bool(worker_list and headcount_list and worker_list[0] == headcount_list[0])

    reasonable = any(
        (
            token_score >= float(fuzzy_threshold),
            len(overlap) >= 2 and token_score >= 70.0,
            len(overlap) >= 2 and same_first_name and (same_client or same_location),
            approximate_pairs >= 2 and (same_client or same_location),
        )
    )
    if not reasonable:
        return None

    confidence = min(
        0.99,
        (token_score / 100.0)
        + (0.04 if same_client else 0.0)
        + (0.04 if same_location else 0.0),
    )
    context = []
    if same_client:
        context.append("mismo cliente")
    if same_location:
        context.append("misma ubicación")
    suffix = f"; {', '.join(context)}" if context else ""
    return confidence, f"Nombre compatible ({round(token_score)}%){suffix}"


def match_worker(
    worker: dict[str, Any],
    headcount_rows: list[dict[str, Any]],
    *,
    fuzzy_threshold: int = 88,
    cliente: str | None = None,
    include_candidates: bool = True,
    excluded_headcount_keys: set[str] | None = None,
) -> dict[str, Any]:
    nombre = normalize_name(worker.get("nombre_normalizado") or worker.get("nombre_original"))
    excluded = excluded_headcount_keys or set()
    available_rows = [row for row in headcount_rows if _hc_key(row) not in excluded]
    by_name = _headcount_by_name(available_rows)

    if nombre and nombre in by_name:
        options = by_name[nombre]
        if len(options) == 1:
            exact_match = _exact_identity_match(
                worker,
                options[0],
                method="nombre_exacto",
                confidence=0.97,
                status="auto",
            )
            if exact_match.get("status") == "review":
                identifier_evidence = _strong_identifier_match(worker, available_rows)
                if identifier_evidence and identifier_evidence.get("status") == "review":
                    return identifier_evidence
            return exact_match
        identifier_evidence = _strong_identifier_match(worker, options)
        if identifier_evidence:
            return identifier_evidence
        return _homonym_result(nombre, options, method="nombre_exacto")

    alias = alias_service.obtener_alias(nombre) if nombre else None
    if alias:
        alias_norm = normalize_name(alias)
        options = by_name.get(alias_norm, [])
        if len(options) == 1:
            return _exact_identity_match(
                worker,
                options[0],
                method="alias",
                confidence=0.98,
                status="confirmed",
            )
        if len(options) > 1:
            return _homonym_result(nombre, options, method="alias")

    strong_match = _strong_identifier_match(worker, available_rows)
    if strong_match:
        return strong_match

    if not include_candidates:
        return _unmatched_result()

    candidates: list[tuple[float, str, dict[str, Any]]] = []
    for hc in available_rows:
        hc_name = normalize_name(hc.get("nombre_completo"))
        if not hc_name or not nombre:
            continue
        candidate = _name_candidate(worker, hc, fuzzy_threshold=fuzzy_threshold)
        if candidate:
            confidence, reason = candidate
            candidates.append((confidence, reason, hc))
    if candidates:
        candidates.sort(key=lambda item: (-item[0], normalize_name(item[2].get("nombre_completo"))))
        payloads = [
            _candidate_payload(row, reason)
            for confidence, reason, row in candidates[:5]
        ]
        return {
            "match_method": "candidato_nombre",
            "confidence": candidates[0][0],
            "status": "review",
            "headcount_key": None,
            "nss": "",
            "rfc": "",
            "curp": "",
            "hc_nombre": "",
            "hc_json": json_dumps(payloads),
            "homonym_options": [row for _, _, row in candidates[:5]],
        }

    return _unmatched_result()


def _unmatched_result() -> dict[str, Any]:
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
    return build_review_match(
        options,
        method=method,
        reason="Nombre exacto compartido por más de una persona",
        confidence=0.5,
    )


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


def load_full_headcount(*, db_path: str | None = None) -> list[dict[str, Any]]:
    from modules.headcount.snapshot_service import (
        get_headcount_rows_from_snapshot,
        resolve_headcount_db_path,
    )

    path = resolve_headcount_db_path(db_path)
    return get_headcount_rows_from_snapshot(path, activos_only=False)["records"]


def match_headcount_keys(match: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    direct = str(match.get("headcount_key") or "").strip()
    if direct:
        keys.add(direct)
    raw = match.get("hc_json")
    if not raw:
        return keys
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return keys
    options = payload if isinstance(payload, list) else [payload]
    for option in options:
        if not isinstance(option, dict):
            continue
        key = str(option.get("headcount_key") or "").strip() or _hc_key(option)
        if key:
            keys.add(key)
    return keys


def manual_search(
    query: str,
    campo: str,
    *,
    headcount_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    campo_obj = str(campo or "").strip().casefold()
    if campo_obj not in {"nss", "rfc_homoclave", "curp", "nombre_completo"}:
        raise ValueError("campo inválido para búsqueda.")
    rows = headcount_rows if headcount_rows is not None else load_full_headcount()
    if campo_obj == "nombre_completo":
        target = normalize_name(query)
        matches = [
            row
            for row in rows
            if target and target in normalize_name(row.get("nombre_completo"))
        ]
    else:
        target = _normalize_identifier(query)
        matches = [
            row
            for row in rows
            if target and target in _normalize_identifier(row.get(campo_obj))
        ]
    if not matches:
        return {"encontrado": False}
    if len(matches) == 1:
        return {"encontrado": True, "duplicado": False, "datos": matches[0]}
    return {"encontrado": True, "duplicado": True, "opciones": matches[:50]}
