from __future__ import annotations

import json
import os
from typing import Any

DATA_DIR = os.environ.get("DATA_DIR", "./data")
ALIAS_PATH = os.path.join(DATA_DIR, "alias_nombres.json")
AGRUPACIONES_PATH = os.path.join(DATA_DIR, "agrupaciones_clientes.json")


def _normalize_nombre(value: Any) -> str:
    return " ".join(str(value or "").upper().strip().split())


def _read_json_dict(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {}


def _write_json_dict(path: str, data: dict[str, Any]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def guardar_alias(nombre_nomina: str, nombre_headcount: str) -> None:
    alias = _read_json_dict(ALIAS_PATH)
    key = _normalize_nombre(nombre_nomina)
    value = _normalize_nombre(nombre_headcount)
    alias[key] = value
    _write_json_dict(ALIAS_PATH, alias)


def obtener_alias(nombre_nomina: str) -> str | None:
    alias = _read_json_dict(ALIAS_PATH)
    key = _normalize_nombre(nombre_nomina)
    value = alias.get(key)
    return str(value) if value is not None else None


def guardar_agrupacion(nombre_agrupacion: str, lista_clientes: list[str]) -> None:
    agrupaciones = _read_json_dict(AGRUPACIONES_PATH)
    agrupaciones[str(nombre_agrupacion)] = list(lista_clientes)
    _write_json_dict(AGRUPACIONES_PATH, agrupaciones)


def obtener_agrupaciones() -> dict[str, Any]:
    return _read_json_dict(AGRUPACIONES_PATH)


def eliminar_agrupacion(nombre_agrupacion: str) -> None:
    agrupaciones = _read_json_dict(AGRUPACIONES_PATH)
    key = str(nombre_agrupacion)
    if key in agrupaciones:
        del agrupaciones[key]
        _write_json_dict(AGRUPACIONES_PATH, agrupaciones)
