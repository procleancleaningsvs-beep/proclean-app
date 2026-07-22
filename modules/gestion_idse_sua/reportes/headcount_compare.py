from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from modules.comparativo.headcount_service import obtener_activos
from modules.gestion_idse_sua.nominas.text_utils import normalize_upper
from modules.gestion_idse_sua.reportes import repository as repo


def compare_report_to_headcount(
    conn: sqlite3.Connection,
    report_id: int,
) -> dict[str, Any]:
    report = repo.get_report(conn, report_id)
    if report is None:
        raise ValueError("Reporte no encontrado.")
    persons = repo.list_report_persons(conn, report_id)
    today = date.today()
    report_month = date(int(report["anio"]), int(report["mes"]), 1)
    historical_warning = report_month < date(today.year, today.month, 1)

    try:
        activos = obtener_activos()
    except Exception as exc:
        return {
            "ok": False,
            "historical_warning": historical_warning,
            "error": str(exc),
            "differences": [],
        }

    hc_by_nss = {
        normalize_upper(row.get("nss") or ""): row
        for row in activos
        if row.get("nss")
    }
    differences: list[dict[str, Any]] = []
    for person in persons:
        nss = normalize_upper(person.get("nss") or "")
        if not nss:
            differences.append(
                {
                    "person_id": person["id"],
                    "nombre": person.get("nombre_nomina"),
                    "tipo": "sin_nss",
                    "detalle": "Persona confirmada sin NSS para comparar.",
                }
            )
            continue
        hc = hc_by_nss.get(nss)
        if not hc:
            differences.append(
                {
                    "person_id": person["id"],
                    "nombre": person.get("nombre_nomina"),
                    "tipo": "no_en_headcount",
                    "detalle": "NSS no encontrado en Headcount actual.",
                }
            )
    return {
        "ok": True,
        "historical_warning": historical_warning,
        "persons_checked": len(persons),
        "differences": differences,
    }
