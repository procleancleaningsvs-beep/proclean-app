from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from modules.exportacion_imss.exportacion_service import guardar_movimiento, mapear_headcount_a_movimiento, obtener_patrones
from modules.gestion_idse_sua.nominas.text_utils import normalize_upper
from modules.gestion_idse_sua.reportes import repository as repo


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
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, list) and data:
        return data[0] if isinstance(data[0], dict) else None
    return data if isinstance(data, dict) else None


def convert_events_to_movements(
    conn: sqlite3.Connection,
    *,
    event_ids: list[int],
    overrides: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    overrides = overrides or {}
    patrones = obtener_patrones()
    converted: list[str] = []
    excluded: list[dict[str, Any]] = []

    for event_id in event_ids:
        row = conn.execute(
            """
            SELECT e.*, p.nss, p.rfc, p.curp, p.sbc, p.nombre_nomina, p.nombre_hc, p.afiliatorios_json
            FROM gis_monthly_report_events e
            JOIN gis_monthly_report_persons p ON p.id = e.person_id
            WHERE e.id = ?
            """,
            (event_id,),
        ).fetchone()
        if row is None:
            excluded.append({"event_id": event_id, "reason": "evento_no_encontrado"})
            continue
        event = dict(row)
        if str(event.get("estado") or "") == "convertido" and event.get("movimiento_id"):
            converted.append(str(event["movimiento_id"]))
            continue
        if str(event.get("estado") or "") != "confirmado":
            repo.mark_event_conversion(conn, event_id, status="incompleto", motivo="Evento no confirmado.")
            excluded.append({"event_id": event_id, "reason": "no_confirmado"})
            continue

        override = overrides.get(event_id, {})
        tipo = normalize_upper(
            override.get("tipo_movimiento")
            or event.get("event_type_confirmed")
            or event.get("event_type_suggested")
        )
        if tipo not in {"ALTA", "BAJA"}:
            repo.mark_event_conversion(conn, event_id, status="incompleto", motivo="Tipo no permitido.")
            excluded.append({"event_id": event_id, "reason": "tipo_no_permitido"})
            continue

        afiliatorios: dict[str, Any] = {}
        if event.get("afiliatorios_json"):
            try:
                parsed = json.loads(event["afiliatorios_json"])
                if isinstance(parsed, dict):
                    afiliatorios = parsed
            except json.JSONDecodeError:
                afiliatorios = {}
        mapped = mapear_headcount_a_movimiento(afiliatorios) if afiliatorios else {}
        rp = normalize_upper(override.get("rp") or afiliatorios.get("patron", ""))[:11]
        rfc_patron = normalize_upper(override.get("rfc_patron") or patrones.get(rp, ""))
        payload = {
            "id": str(uuid.uuid4()),
            "tipo_movimiento": tipo,
            "rp": rp,
            "rfc_patron": rfc_patron,
            "fecha_movimiento": override.get("fecha_movimiento")
            or event.get("fecha_confirmed")
            or event.get("fecha_suggested")
            or "",
            "nss": override.get("nss") or event.get("nss") or mapped.get("nss", ""),
            "rfc": override.get("rfc") or event.get("rfc") or mapped.get("rfc"),
            "curp": override.get("curp") or event.get("curp") or mapped.get("curp", ""),
            "apellido_paterno": override.get("apellido_paterno") or mapped.get("apellido_paterno", ""),
            "apellido_materno": override.get("apellido_materno") or mapped.get("apellido_materno", ""),
            "nombres": override.get("nombres") or mapped.get("nombres") or event.get("nombre_nomina") or "",
            "sbc": override.get("sbc") or event.get("sbc") or mapped.get("sbc") or "0.00",
            "origen": "gis_reporte_mensual",
            "alerta": (
                f"reporte_id={event.get('report_id')};persona_id={event.get('person_id')};evento_id={event_id}"
            ),
        }
        faltantes = [k for k in REQUIRED_MOV_FIELDS if not str(payload.get(k) or "").strip()]
        if faltantes:
            reason = f"Datos incompletos: {', '.join(faltantes)}"
            repo.mark_event_conversion(conn, event_id, status="incompleto", motivo=reason)
            excluded.append({"event_id": event_id, "reason": reason})
            continue

        mov = guardar_movimiento(payload)
        repo.mark_event_conversion(conn, event_id, status="convertido", movimiento_id=mov["id"])
        converted.append(mov["id"])

    conn.commit()
    return {"converted_ids": converted, "excluded": excluded}
