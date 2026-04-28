from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any

from openpyxl import load_workbook
from rapidfuzz import fuzz

from modules.comparativo import alias_service
from modules.comparativo.headcount_service import buscar_trabajador

DATA_DIR = os.environ.get("DATA_DIR", "./data")
COMPARATIVOS_DIR = os.path.join(DATA_DIR, "comparativos")
NOMINAS_DIR = os.path.join(DATA_DIR, "nominas")


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


def _parse_period_sort_key(value: str) -> datetime:
    parsed = _parse_ddmmyyyy(value)
    if parsed is not None:
        return parsed
    return datetime.min


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


def detectar_similitudes(lista_nomina: list[str], lista_activos: list[str], umbral: int = 88) -> list[dict[str, Any]]:
    try:
        similitudes: list[dict[str, Any]] = []
        activos_norm = [_normalize_name(n) for n in lista_activos if _normalize_name(n)]

        for nombre_raw in lista_nomina:
            nomina_norm = _normalize_name(nombre_raw)
            if not nomina_norm:
                continue

            alias = alias_service.obtener_alias(nomina_norm)
            if alias:
                # Alias conocido: se toma como resuelto y se omite sugerencia.
                continue

            for activo in activos_norm:
                if nomina_norm == activo:
                    continue
                score = float(fuzz.ratio(nomina_norm, activo))
                if score >= float(umbral):
                    similitudes.append(
                        {
                            "nomina": nomina_norm,
                            "headcount": activo,
                            "score": score,
                        }
                    )

        similitudes.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
        return similitudes
    except Exception as exc:
        raise ValueError(f"No se pudo detectar similitudes: {exc}") from exc


def aplicar_aliases_y_comparar(lista_nomina_raw: list[str], lista_activos: list[str]) -> dict[str, Any]:
    try:
        lista_resuelta: list[str] = []
        aliases_aplicados: list[dict[str, str]] = []

        for nombre_raw in lista_nomina_raw:
            original = _normalize_name(nombre_raw)
            if not original:
                continue
            alias = alias_service.obtener_alias(original)
            resuelto = _normalize_name(alias) if alias else original
            lista_resuelta.append(resuelto)
            if resuelto != original:
                aliases_aplicados.append({"original": original, "resuelto": resuelto})

        resultado = comparar_listas(lista_resuelta, lista_activos)
        resultado["aliases_aplicados"] = aliases_aplicados
        return resultado
    except Exception as exc:
        raise ValueError(f"No se pudo aplicar aliases y comparar: {exc}") from exc


def guardar_nomina_semana(
    cliente: str,
    periodo_inicio: str,
    periodo_fin: str,
    lista_nombres: list[str],
) -> dict[str, Any]:
    try:
        os.makedirs(NOMINAS_DIR, exist_ok=True)
        nombres_norm = sorted({_normalize_name(n) for n in lista_nombres if _normalize_name(n)})
        payload: dict[str, Any] = {
            "cliente": str(cliente or "").strip(),
            "periodo_inicio": str(periodo_inicio or "").strip(),
            "periodo_fin": str(periodo_fin or "").strip(),
            "fecha_guardado": datetime.now().isoformat(),
            "empleados": nombres_norm,
        }

        agrupaciones = alias_service.obtener_agrupaciones()
        if payload["cliente"] in agrupaciones:
            raw_clients = agrupaciones.get(payload["cliente"], [])
            payload["clientes_agrupados"] = [str(c).strip() for c in raw_clients if str(c).strip()]

        safe_cliente = _normalize_spaces(payload["cliente"] or "general").replace(" ", "_").replace("/", "-")
        safe_inicio = payload["periodo_inicio"].replace("/", "-")
        safe_fin = payload["periodo_fin"].replace("/", "-")
        out_path = os.path.join(NOMINAS_DIR, f"{safe_cliente}_{safe_inicio}_{safe_fin}.json")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        return payload
    except Exception as exc:
        raise ValueError(f"No se pudo guardar nómina semanal: {exc}") from exc


def obtener_nominas_guardadas(cliente: str | None = None) -> list[dict[str, Any]]:
    try:
        os.makedirs(NOMINAS_DIR, exist_ok=True)
        items: list[dict[str, Any]] = []
        for name in os.listdir(NOMINAS_DIR):
            if not name.lower().endswith(".json"):
                continue
            path = os.path.join(NOMINAS_DIR, name)
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                continue
            if cliente:
                objetivo = str(cliente).strip().casefold()
                cliente_nomina = str(data.get("cliente", "")).strip().casefold()
                agrupados = [str(c).strip().casefold() for c in data.get("clientes_agrupados", []) if str(c).strip()]
                if objetivo != cliente_nomina and objetivo not in agrupados:
                    continue
            items.append(data)
        items.sort(key=lambda x: _parse_period_sort_key(str(x.get("periodo_inicio", ""))), reverse=True)
        return items
    except Exception as exc:
        raise ValueError(f"No se pudo obtener nóminas guardadas: {exc}") from exc
