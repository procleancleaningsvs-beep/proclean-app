from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from modules.comparativo.headcount_service import obtener_activos
from modules.gestion_idse_sua.nominas import repository as repo
from modules.gestion_idse_sua.nominas.match_service import match_worker
from modules.gestion_idse_sua.nominas.planta_cliente_service import (
    detect_planta_cliente_conflict,
    period_cut_warnings,
)
from modules.gestion_idse_sua.nominas.text_utils import json_dumps, normalize_name, normalize_upper


def _hc_cliente(match: dict[str, Any]) -> str:
    direct = normalize_upper(match.get("hc_cliente"))
    if direct:
        return direct
    raw = match.get("hc_json")
    if not raw:
        return ""
    import json

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if isinstance(data, list):
        data = data[0] if data else {}
    if isinstance(data, dict):
        return normalize_upper(data.get("cliente"))
    return ""


def _first_attendance_dates(conn: sqlite3.Connection, period_id: int) -> dict[int, str]:
    first_by_worker: dict[int, str] = {}
    for row in repo.list_attendance_for_period(conn, period_id):
        if str(row.get("code_normalized") or "").upper() != "A":
            continue
        if str(row.get("interpretation_status") or "ok") not in {"ok", "corrected"}:
            continue
        worker_id = int(row["worker_id"])
        if worker_id in first_by_worker:
            continue
        try:
            first_by_worker[worker_id] = datetime.strptime(
                str(row.get("fecha_iso") or ""), "%Y-%m-%d"
            ).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return first_by_worker


RESULTADO_LABELS = {
    "coincidencia": ("Coincidencia", "azul"),
    "posible_alta": ("Posible alta", "verde"),
    "posible_baja": ("Posible baja", "rojo"),
    "revision": ("Revisión", "amarillo"),
}


def enrich_workers(conn: sqlite3.Connection, period_id: int, headcount_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = headcount_rows if headcount_rows is not None else obtener_activos()
    workers = repo.list_workers(conn, period_id)
    enriched = 0
    for worker in workers:
        worker_client = normalize_upper(
            worker.get("cliente_confirmado") or worker.get("cliente_sugerido")
        )
        match = match_worker(worker, rows, cliente=worker_client or None)
        if worker.get("cliente_confirmado") or worker.get("cliente_sugerido"):
            if detect_planta_cliente_conflict(
                planta_cliente=str(worker.get("cliente_confirmado") or worker.get("cliente_sugerido")),
                headcount_cliente=str(match.get("hc_cliente") or ""),
            ):
                match["status"] = "review"
        repo.upsert_match(conn, int(worker["id"]), match)
        if match.get("status") not in {"unmatched", None}:
            enriched += 1
    conn.commit()
    return {"workers": len(workers), "enriched": enriched}


def run_comparative(
    conn: sqlite3.Connection,
    *,
    period_id: int,
    cliente: str,
    generated_by: str | None,
    headcount_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cliente_norm = normalize_upper(cliente)
    if not cliente_norm:
        raise ValueError("Cliente obligatorio para comparar.")

    hc_rows = headcount_rows if headcount_rows is not None else obtener_activos(cliente=cliente_norm)
    workers = [
        worker
        for worker in repo.list_workers(conn, period_id)
        if normalize_upper(worker.get("cliente_confirmado")) == cliente_norm
    ]
    if not workers:
        raise ValueError(
            f"No hay trabajadores con cliente confirmado para comparar contra {cliente_norm}."
        )

    warnings: list[str] = []
    period = conn.execute("SELECT * FROM gis_nomina_periods WHERE id = ?", (period_id,)).fetchone()
    if period and period["fecha_inicio"] and period["fecha_fin"]:
        warnings.extend(
            period_cut_warnings(
                conn,
                cliente_norm,
                str(period["fecha_inicio"]),
                str(period["fecha_fin"]),
            )
        )

    matched_hc_names: set[str] = set()
    nomina_matched_names: set[str] = set()
    first_attendance_by_worker = _first_attendance_dates(conn, period_id)

    comparative_id = repo.create_comparative(
        conn,
        period_id=period_id,
        cliente=cliente_norm,
        generated_by=generated_by,
        warnings=warnings,
    )

    totals = {"nomina": len(workers), "coincidencias": 0, "altas": 0, "bajas": 0, "revisiones": 0, "sin_match": 0}

    for worker in workers:
        wid = int(worker["id"])
        match = repo.get_match(conn, wid) or {}
        persisted_status = str(match.get("status") or "")
        persisted_client = _hc_cliente(match)
        if not (
            persisted_status in {"confirmed", "manual"}
            and persisted_client == cliente_norm
        ):
            match = match_worker(
                worker,
                hc_rows,
                cliente=cliente_norm,
            )
            repo.upsert_match(conn, wid, match)
        status = str(match.get("status") or "unmatched")
        hc_name = normalize_name(match.get("hc_nombre"))
        hc_cliente = _hc_cliente(match)
        nombre = normalize_name(worker.get("nombre_normalizado"))

        if status in {"suggested", "review"}:
            tipo = "Revisión"
            sem = "amarillo"
            totals["revisiones"] += 1
            observaciones = f"Match {match.get('match_method')} pendiente de confirmación."
            tipo_mov = ""
        elif status in {"auto", "confirmed", "manual"} and hc_name and (not hc_cliente or hc_cliente == cliente_norm):
            tipo = "Coincidencia"
            sem = "azul"
            totals["coincidencias"] += 1
            observaciones = ""
            tipo_mov = ""
            matched_hc_names.add(hc_name)
            nomina_matched_names.add(hc_name or nombre)
        elif status == "unmatched" or not hc_name:
            tipo = "Posible alta"
            sem = "verde"
            totals["altas"] += 1
            totals["sin_match"] += 1
            observaciones = "Presente en nómina sin match confirmado en Headcount."
            tipo_mov = "ALTA"
            if wid not in first_attendance_by_worker:
                observaciones += " Sin asistencia A en el periodo; fecha pendiente de revisión manual."
        else:
            tipo = "Revisión"
            sem = "amarillo"
            totals["revisiones"] += 1
            observaciones = "Conflicto de cliente/planta o match ambiguo."
            tipo_mov = ""

        repo.insert_result(
            conn,
            comparative_id,
            {
                "worker_id": wid,
                "resultado": tipo,
                "semaforo": sem,
                "tipo_sugerido": tipo_mov or tipo,
                "fecha_sugerida": first_attendance_by_worker.get(wid, "") if tipo_mov == "ALTA" else "",
                "decision_final": tipo,
                "observaciones": observaciones,
            },
        )

    hc_names_cliente = {
        normalize_name(r.get("nombre_completo"))
        for r in hc_rows
        if normalize_upper(r.get("cliente")) == cliente_norm and normalize_name(r.get("nombre_completo"))
    }
    bajas = sorted(hc_names_cliente - matched_hc_names)

    period_inicio = str(period["fecha_inicio"]) if period else ""
    period_fin = str(period["fecha_fin"]) if period else ""

    for nombre in bajas:
        totals["bajas"] += 1
        repo.insert_result(
            conn,
            comparative_id,
            {
                "worker_id": None,
                "headcount_only": True,
                "hc_nombre": nombre,
                "resultado": "Posible baja",
                "semaforo": "rojo",
                "tipo_sugerido": "BAJA",
                "fecha_sugerida": period_fin,
                "decision_final": "Posible baja",
                "observaciones": "Activo en Headcount, ausente en nómina.",
            },
        )

    conn.commit()
    return {
        "comparative_id": comparative_id,
        "warnings": warnings,
        "totals": totals,
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, int]:
    out = {
        "nomina": 0,
        "coincidencias": 0,
        "altas": 0,
        "bajas": 0,
        "revisiones": 0,
        "sin_match": 0,
    }
    for row in results:
        res = str(row.get("resultado") or "")
        if res == "Coincidencia":
            out["coincidencias"] += 1
            out["nomina"] += 1
        elif res == "Posible alta":
            out["altas"] += 1
        elif res == "Posible baja":
            out["bajas"] += 1
        elif res == "Revisión":
            out["revisiones"] += 1
            if str(row.get("match_status") or row.get("status") or "") == "unmatched":
                out["sin_match"] += 1
            out["nomina"] += 1 if row.get("worker_id") else 0
    return out
