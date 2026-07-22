from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from typing import Any

from modules.exportacion_imss.exportacion_service import guardar_movimiento, mapear_headcount_a_movimiento, obtener_patrones
from modules.gestion_idse_sua.nominas import repository as repo
from modules.gestion_idse_sua.nominas.text_utils import normalize_upper


REQUIRED_MOV_FIELDS = (
    "tipo_movimiento",
    "rp",
    "fecha_movimiento",
    "nss",
    "curp",
    "apellido_paterno",
    "apellido_materno",
    "nombres",
    "sbc",
)


def _parse_hc_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    import json

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, list) and data:
        return data[0] if isinstance(data[0], dict) else None
    return data if isinstance(data, dict) else None


def convert_results_to_movements(
    conn: sqlite3.Connection,
    *,
    result_ids: list[int],
    overrides: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    overrides = overrides or {}
    patrones = obtener_patrones()
    converted: list[str] = []
    excluded: list[dict[str, Any]] = []

    for result_id in result_ids:
        row = conn.execute(
            """
            SELECT r.*, w.nombre_normalizado, m.hc_json, m.nss, m.rfc, m.curp
            FROM gis_nomina_results r
            LEFT JOIN gis_nomina_workers w ON w.id = r.worker_id
            LEFT JOIN gis_nomina_matches m ON m.worker_id = r.worker_id
            WHERE r.id = ?
            """,
            (result_id,),
        ).fetchone()
        if row is None:
            excluded.append({"result_id": result_id, "reason": "resultado_no_encontrado"})
            continue
        if str(row["conversion_status"]) == "converted" and row["movimiento_id"]:
            converted.append(str(row["movimiento_id"]))
            continue

        override = overrides.get(result_id, {})
        tipo = normalize_upper(override.get("tipo_movimiento") or row["tipo_sugerido"])
        if tipo not in {"ALTA", "BAJA"}:
            repo.mark_result_conversion(conn, result_id, status="excluded", exclusion_reason="Tipo no permitido.")
            excluded.append({"result_id": result_id, "reason": "tipo_no_permitido"})
            continue

        hc = _parse_hc_json(row["hc_json"])
        mapped = mapear_headcount_a_movimiento(hc) if hc else {}
        rp = normalize_upper(override.get("rp") or (hc or {}).get("patron", ""))[:11]
        rfc_patron = normalize_upper(override.get("rfc_patron") or patrones.get(rp, ""))
        payload = {
            "id": str(uuid.uuid4()),
            "tipo_movimiento": tipo,
            "rp": rp,
            "rfc_patron": rfc_patron,
            "fecha_movimiento": override.get("fecha_movimiento") or row["fecha_sugerida"] or "",
            "nss": override.get("nss") or row["nss"] or mapped.get("nss", ""),
            "rfc": override.get("rfc") or row["rfc"] or mapped.get("rfc"),
            "curp": override.get("curp") or row["curp"] or mapped.get("curp", ""),
            "apellido_paterno": override.get("apellido_paterno") or mapped.get("apellido_paterno", ""),
            "apellido_materno": override.get("apellido_materno") or mapped.get("apellido_materno", ""),
            "nombres": override.get("nombres") or mapped.get("nombres") or row["nombre_normalizado"] or row["hc_nombre"],
            "sbc": override.get("sbc") or mapped.get("sbc") or "0.00",
            "origen": "gis_comparativo_semanal",
        }
        faltantes = [k for k in REQUIRED_MOV_FIELDS if not str(payload.get(k) or "").strip()]
        if faltantes:
            reason = f"Datos incompletos: {', '.join(faltantes)}"
            repo.mark_result_conversion(conn, result_id, status="excluded", exclusion_reason=reason)
            excluded.append({"result_id": result_id, "reason": reason})
            continue

        mov = guardar_movimiento(payload)
        repo.mark_result_conversion(conn, result_id, status="converted", movimiento_id=mov["id"])
        converted.append(mov["id"])

    conn.commit()
    return {"converted_ids": converted, "excluded": excluded}
