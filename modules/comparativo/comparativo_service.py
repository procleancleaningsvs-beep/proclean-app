from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any

from openpyxl import load_workbook

from modules.comparativo.headcount_service import buscar_trabajador

DATA_DIR = os.environ.get("DATA_DIR", "./data")
COMPARATIVOS_DIR = os.path.join(DATA_DIR, "comparativos")


def _normalize_spaces(value: str) -> str:
    return " ".join((value or "").split())


def _normalize_name(value: Any) -> str:
    return _normalize_spaces(str(value or "").upper().strip())


def _parse_ddmmyyyy(value: str) -> datetime | None:
    s = str(value or "").strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[:10], fmt)
        except ValueError:
            continue
    return None


def parsear_nomina(file) -> list[str]:
    try:
        workbook = load_workbook(file, data_only=True)
        sheet = workbook.active
        match_row = None
        match_col = None

        for row in sheet.iter_rows():
            for cell in row:
                text = _normalize_spaces(str(cell.value or "").strip().upper())
                if text == "NOMBRE DE EMPLEADO":
                    match_row = cell.row
                    match_col = cell.column
                    break
            if match_row is not None:
                break

        if match_row is None or match_col is None:
            raise ValueError("No se encontró la celda 'NOMBRE DE EMPLEADO' en la nómina.")

        nombres: list[str] = []
        row_idx = match_row + 1
        while row_idx <= sheet.max_row:
            value = sheet.cell(row=row_idx, column=match_col).value
            if value is None or str(value).strip() == "":
                break
            if isinstance(value, (int, float)):
                row_idx += 1
                continue
            nombre = _normalize_name(value)
            if nombre:
                nombres.append(nombre)
            row_idx += 1
        return nombres
    except Exception as exc:
        raise ValueError(f"No se pudo parsear la nómina: {exc}") from exc


def comparar_listas(lista_nomina: list[str], lista_activos: list[str]) -> dict[str, Any]:
    try:
        nomina_set = {_normalize_name(n) for n in lista_nomina if _normalize_name(n)}
        activos_set = {_normalize_name(n) for n in lista_activos if _normalize_name(n)}
        altas = sorted(nomina_set - activos_set)
        bajas = sorted(activos_set - nomina_set)
        permanencias = sorted(nomina_set & activos_set)
        return {
            "altas": altas,
            "bajas": bajas,
            "permanencias": permanencias,
            "total_nomina": len(nomina_set),
            "total_activos": len(activos_set),
        }
    except Exception as exc:
        raise ValueError(f"No se pudo comparar listas: {exc}") from exc


def guardar_comparativo_semanal(
    resultado: dict[str, Any],
    cliente: str,
    periodo_inicio: str,
    periodo_fin: str,
    fecha_baja_asumida: str,
) -> dict[str, Any]:
    try:
        os.makedirs(COMPARATIVOS_DIR, exist_ok=True)
        try:
            os.makedirs("/app/data/comparativos", exist_ok=True)
        except OSError:
            pass
        comparativo = {
            "id": str(uuid.uuid4()),
            "cliente": str(cliente or "").strip(),
            "periodo_inicio": str(periodo_inicio or "").strip(),
            "periodo_fin": str(periodo_fin or "").strip(),
            "fecha_baja_asumida": str(fecha_baja_asumida or "").strip(),
            "fecha_generacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "altas": list(resultado.get("altas", [])),
            "bajas": list(resultado.get("bajas", [])),
            "permanencias": list(resultado.get("permanencias", [])),
            "total_nomina": int(resultado.get("total_nomina", 0)),
            "total_activos": int(resultado.get("total_activos", 0)),
        }
        safe_cliente = _normalize_spaces(str(cliente or "general")).replace(" ", "_").replace("/", "-")
        safe_inicio = str(periodo_inicio or "").replace("/", "-")
        safe_fin = str(periodo_fin or "").replace("/", "-")
        filename = f"{safe_cliente}_{safe_inicio}_{safe_fin}.json"
        file_path = os.path.join(COMPARATIVOS_DIR, filename)
        with open(file_path, "w", encoding="utf-8") as fh:
            json.dump(comparativo, fh, ensure_ascii=False, indent=2)
        return comparativo
    except Exception as exc:
        raise ValueError(f"No se pudo guardar comparativo semanal: {exc}") from exc


def obtener_historial(cliente: str | None = None) -> list[dict[str, Any]]:
    try:
        os.makedirs(COMPARATIVOS_DIR, exist_ok=True)
        items: list[dict[str, Any]] = []
        for name in os.listdir(COMPARATIVOS_DIR):
            if not name.lower().endswith(".json"):
                continue
            path = os.path.join(COMPARATIVOS_DIR, name)
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                continue
            if cliente and str(data.get("cliente", "")).strip().casefold() != str(cliente).strip().casefold():
                continue
            items.append(data)
        items.sort(key=lambda x: str(x.get("fecha_generacion", "")), reverse=True)
        return items
    except Exception as exc:
        raise ValueError(f"No se pudo obtener historial de comparativos: {exc}") from exc


def generar_reporte_mensual(cliente: str, mes: int, anio: int) -> dict[str, Any]:
    try:
        semanas: list[dict[str, Any]] = []
        for comp in obtener_historial(cliente):
            inicio = _parse_ddmmyyyy(comp.get("periodo_inicio", ""))
            fin = _parse_ddmmyyyy(comp.get("periodo_fin", ""))
            if (inicio and inicio.month == int(mes) and inicio.year == int(anio)) or (
                fin and fin.month == int(mes) and fin.year == int(anio)
            ):
                semanas.append(comp)

        altas_map: dict[str, str] = {}
        bajas_map: dict[str, str] = {}
        personal_activo_set: set[str] = set()
        altas_detalle: list[dict[str, Any]] = []
        bajas_detalle: list[dict[str, Any]] = []

        for comp in semanas:
            fecha_alta = str(comp.get("periodo_inicio", "")).strip()
            fecha_baja = str(comp.get("fecha_baja_asumida", "")).strip()
            for nombre in comp.get("altas", []):
                n = _normalize_name(nombre)
                if not n:
                    continue
                personal_activo_set.add(n)
                old = _parse_ddmmyyyy(altas_map.get(n, ""))
                current = _parse_ddmmyyyy(fecha_alta)
                if old is None or (current is not None and current < old):
                    altas_map[n] = fecha_alta
            for nombre in comp.get("bajas", []):
                n = _normalize_name(nombre)
                if not n:
                    continue
                personal_activo_set.add(n)
                old = _parse_ddmmyyyy(bajas_map.get(n, ""))
                current = _parse_ddmmyyyy(fecha_baja)
                if old is None or (current is not None and current > old):
                    bajas_map[n] = fecha_baja
            for nombre in comp.get("permanencias", []):
                n = _normalize_name(nombre)
                if n:
                    personal_activo_set.add(n)

        for nombre, fecha in sorted(altas_map.items()):
            altas_detalle.append({"nombre": nombre, "fecha_alta": fecha, "trabajador": buscar_trabajador(nombre)})
        for nombre, fecha in sorted(bajas_map.items()):
            bajas_detalle.append({"nombre": nombre, "fecha_baja": fecha, "trabajador": buscar_trabajador(nombre)})

        return {
            "cliente": cliente,
            "mes": int(mes),
            "anio": int(anio),
            "semanas": semanas,
            "altas_mes": [{"nombre": n, "fecha_alta": altas_map[n]} for n in sorted(altas_map)],
            "bajas_mes": [{"nombre": n, "fecha_baja": bajas_map[n]} for n in sorted(bajas_map)],
            "personal_activo_mes": sorted(personal_activo_set),
            "altas_mes_detalle": altas_detalle,
            "bajas_mes_detalle": bajas_detalle,
        }
    except Exception as exc:
        raise ValueError(f"No se pudo generar reporte mensual: {exc}") from exc
