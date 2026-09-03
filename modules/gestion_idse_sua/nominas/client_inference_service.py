from __future__ import annotations

import json
import sqlite3
from difflib import SequenceMatcher
from io import BytesIO
from typing import Any

from openpyxl import load_workbook

from modules.gestion_idse_sua.nominas.match_service import match_worker
from modules.gestion_idse_sua.nominas.planta_cliente_service import suggest_cliente_for_planta
from modules.gestion_idse_sua.nominas.text_utils import normalize_upper
from modules.gestion_idse_sua.nominas.worker_extractor import extract_workers


def _known_clients(headcount_rows: list[dict[str, Any]]) -> list[str]:
    clients = {normalize_upper(row.get("cliente")) for row in headcount_rows if row.get("cliente")}
    return sorted(c for c in clients if c)


def _close_clients(source: str, known_clients: list[str], *, limit: int = 5) -> list[str]:
    source_norm = normalize_upper(source)
    scored = [
        (SequenceMatcher(None, source_norm, client).ratio(), client)
        for client in known_clients
        if client and client != source_norm
    ]
    return [client for score, client in sorted(scored, reverse=True) if score >= 0.35][:limit]


def _client_in_text(text: str, known_clients: list[str]) -> tuple[str, float, str]:
    upper = normalize_upper(text)
    if not upper:
        return "", 0.0, ""
    for client in sorted(known_clients, key=len, reverse=True):
        if client and client in upper:
            return client, 0.55, "filename" if ".xlsx" in text.lower() else "sheet_name"
    return "", 0.0, ""


def _hc_cliente_from_match(match: dict[str, Any] | None) -> tuple[str, float]:
    if not match:
        return "", 0.0
    direct = normalize_upper(match.get("hc_cliente"))
    if direct:
        return direct, 0.85
    raw = match.get("hc_json")
    if not raw:
        return "", 0.0
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "", 0.0
    if isinstance(data, list):
        data = data[0] if data else {}
    if isinstance(data, dict):
        cliente = normalize_upper(data.get("cliente"))
        if cliente:
            return cliente, 0.85
    return "", 0.0


def infer_worker_client(
    conn: sqlite3.Connection,
    *,
    worker: dict[str, Any],
    match: dict[str, Any] | None,
    headcount_rows: list[dict[str, Any]],
    filename: str = "",
    sheet_name: str = "",
) -> dict[str, Any]:
    confirmed = normalize_upper(worker.get("cliente_confirmado"))
    if confirmed:
        return {
            "cliente": confirmed,
            "source": "confirmed",
            "confidence": 1.0,
            "requires_review": False,
        }

    known = _known_clients(headcount_rows)
    payroll_client = normalize_upper(worker.get("cliente_sugerido"))
    if payroll_client and str(worker.get("suggestion_source") or "") == "payroll":
        if payroll_client in known:
            return {
                "cliente": payroll_client,
                "source": "payroll_exact",
                "confidence": 1.0,
                "requires_review": False,
                "suggestions": [],
            }
        return {
            "cliente": payroll_client,
            "source": "payroll_unknown",
            "confidence": 0.0,
            "requires_review": True,
            "suggestions": _close_clients(payroll_client, known),
        }

    candidates: list[tuple[str, float, str]] = []

    planta = str(worker.get("planta_normalizada") or worker.get("planta_original") or "")
    planta_sug = suggest_cliente_for_planta(conn, planta, headcount_rows)
    if planta_sug.get("cliente"):
        candidates.append(
            (
                normalize_upper(planta_sug["cliente"]),
                float(planta_sug.get("confidence") or 0),
                str(planta_sug.get("source") or "planta"),
            )
        )

    hc_cliente, hc_conf = _hc_cliente_from_match(match)
    if hc_cliente:
        candidates.append((hc_cliente, hc_conf, "headcount_match"))

    fn_client, fn_conf, _ = _client_in_text(filename, known)
    if fn_client:
        candidates.append((fn_client, fn_conf, "filename"))

    sh_client, sh_conf, _ = _client_in_text(sheet_name, known)
    if sh_client:
        candidates.append((sh_client, sh_conf, "sheet_name"))

    if worker.get("cliente_sugerido"):
        candidates.append((normalize_upper(worker["cliente_sugerido"]), 0.5, "import_suggestion"))

    if not candidates:
        return {
            "cliente": "",
            "source": "unknown",
            "confidence": 0.0,
            "requires_review": True,
        }

    cliente, confidence, source = max(candidates, key=lambda item: item[1])
    unique_clients = {c[0] for c in candidates if c[0]}
    return {
        "cliente": cliente,
        "source": source,
        "confidence": round(confidence, 2),
        "requires_review": len(unique_clients) > 1 or confidence < 0.7,
        "alternatives": [
            {"cliente": c, "confidence": conf, "source": src}
            for c, conf, src in sorted(candidates, key=lambda item: -item[1])
            if c != cliente
        ],
    }


def summarize_period_clients(worker_inferences: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    sources: dict[str, set[str]] = {}
    confidences: dict[str, list[float]] = {}
    review = 0
    for item in worker_inferences:
        cliente = normalize_upper(item.get("cliente"))
        if not cliente:
            review += 1
            continue
        counts[cliente] = counts.get(cliente, 0) + 1
        sources.setdefault(cliente, set()).add(str(item.get("source") or ""))
        confidences.setdefault(cliente, []).append(float(item.get("confidence") or 0))
        if item.get("requires_review"):
            review += 1

    primary = ""
    if counts:
        primary = max(counts.items(), key=lambda kv: kv[1])[0]

    return {
        "primary_cliente": primary,
        "counts": counts,
        "sources": {k: sorted(v) for k, v in sources.items()},
        "confidence": {
            key: round(sum(values) / len(values), 2) for key, values in confidences.items() if values
        },
        "contradictions": len(counts) > 1,
        "requires_review": review > 0 or len(counts) > 1,
        "review_count": review,
    }


def infer_period_clients(
    conn: sqlite3.Connection,
    *,
    period_id: int,
    workers: list[dict[str, Any]],
    matches: dict[int, dict[str, Any] | None],
    headcount_rows: list[dict[str, Any]],
    filename: str,
    sheet_name: str,
) -> dict[str, Any]:
    per_worker: list[dict[str, Any]] = []
    for worker in workers:
        wid = int(worker["id"])
        inference = infer_worker_client(
            conn,
            worker=worker,
            match=matches.get(wid),
            headcount_rows=headcount_rows,
            filename=filename,
            sheet_name=sheet_name,
        )
        per_worker.append({"worker_id": wid, **inference})

    summary = summarize_period_clients(per_worker)
    unresolved: dict[str, dict[str, Any]] = {}
    worker_by_id = {int(worker["id"]): worker for worker in workers}
    known_clients = _known_clients(headcount_rows)
    for item in per_worker:
        worker = worker_by_id[int(item["worker_id"])]
        if normalize_upper(worker.get("cliente_confirmado")):
            continue
        source_client = normalize_upper(item.get("cliente"))
        key = source_client or "__missing__"
        group = unresolved.setdefault(
            key,
            {
                "source_client": source_client,
                "label": source_client or "Sin cliente informado",
                "worker_ids": [],
                "suggestions": item.get("suggestions")
                or _close_clients(source_client, known_clients),
            },
        )
        group["worker_ids"].append(int(item["worker_id"]))
    summary["unresolved_groups"] = sorted(unresolved.values(), key=lambda item: item["label"])
    return {"workers": per_worker, "summary": summary}


def preview_sheet_clients(
    conn: sqlite3.Connection,
    *,
    file_bytes: bytes,
    sheet: dict[str, Any],
    headcount_rows: list[dict[str, Any]],
    filename: str,
) -> dict[str, Any]:
    """Extract just enough row data to infer clients before period confirmation."""
    wb = load_workbook(BytesIO(file_bytes), data_only=True)
    try:
        ws = wb.worksheets[int(sheet["sheet_index"])]
        payload = extract_workers(
            ws,
            sheet_name=str(sheet["sheet_name"]),
            sheet_index=int(sheet["sheet_index"]),
            is_hidden=bool(sheet.get("is_hidden")),
        )
    finally:
        wb.close()

    inferred_workers: list[dict[str, Any]] = []
    plants: dict[str, set[str]] = {}
    for worker in payload["workers"]:
        match = match_worker(worker, headcount_rows)
        inference = infer_worker_client(
            conn,
            worker=worker,
            match=match,
            headcount_rows=headcount_rows,
            filename=filename,
            sheet_name=str(sheet["sheet_name"]),
        )
        cliente = normalize_upper(inference.get("cliente"))
        planta = str(worker.get("planta_normalizada") or worker.get("planta_original") or "")
        if cliente and planta:
            plants.setdefault(cliente, set()).add(planta)
        inferred_workers.append(
            {
                "row_number": int(worker["row_number"]),
                "nombre": worker.get("nombre_normalizado") or worker.get("nombre_original") or "",
                "planta": planta,
                "match_status": match.get("status") or "unmatched",
                **inference,
            }
        )

    summary = summarize_period_clients(inferred_workers)
    summary["plants"] = {cliente: sorted(values) for cliente, values in plants.items()}
    summary["pending_count"] = sum(1 for row in inferred_workers if not row.get("cliente"))
    return {"workers": inferred_workers, "summary": summary, "discarded": payload.get("discarded") or []}
