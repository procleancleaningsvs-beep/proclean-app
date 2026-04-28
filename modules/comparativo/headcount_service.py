from __future__ import annotations

import os
import threading
import time
from datetime import date, datetime
from io import BytesIO
from typing import Any

import pandas as pd

from modules.finiquitos.excel_mirror_fecha_ingreso import descargar_excel_desde_onedrive

DATA_DIR = os.environ.get("DATA_DIR", "./data")
HEADCOUNT_ONEDRIVE_URL_ENV = "HEADCOUNT_ONEDRIVE_URL"
_CACHE_TTL_SEC = 300
_STALE_GRACE_SEC = 300
_cache_lock = threading.Lock()
_cache_df: pd.DataFrame | None = None
_cache_loaded_at: float = 0.0
_cache_url_used: str = ""


def _normalize_spaces(value: str) -> str:
    return " ".join((value or "").split())


def _normalize_name(value: Any) -> str:
    return _normalize_spaces(str(value or "").upper().strip())


def _normalize_header(value: Any) -> str:
    return _normalize_spaces(str(value or "").strip().upper())


def _is_empty(value: Any) -> bool:
    try:
        if pd.isna(value):
            return True
    except TypeError:
        pass
    return value is None or str(value).strip() == ""


def _format_fecha(value: Any) -> str:
    if _is_empty(value):
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[:10], fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return s


def _headcount_url() -> str:
    return (os.environ.get(HEADCOUNT_ONEDRIVE_URL_ENV, "") or "").strip()


def obtener_df_headcount() -> pd.DataFrame:
    try:
        url = _headcount_url()
        if not url:
            raise ValueError("No está configurada la variable HEADCOUNT_ONEDRIVE_URL.")

        now = time.monotonic()
        global _cache_df, _cache_loaded_at, _cache_url_used
        with _cache_lock:
            if (
                _cache_df is not None
                and _cache_url_used == url
                and (now - _cache_loaded_at) < _CACHE_TTL_SEC
            ):
                return _cache_df

        raw = descargar_excel_desde_onedrive(url)
        df = pd.read_excel(BytesIO(raw), engine="openpyxl", header=None)
        with _cache_lock:
            _cache_df = df
            _cache_loaded_at = time.monotonic()
            _cache_url_used = url
        return df
    except Exception as exc:
        with _cache_lock:
            if _cache_df is not None and _cache_url_used == _headcount_url():
                stale_age = time.monotonic() - _cache_loaded_at
                if stale_age < (_CACHE_TTL_SEC + _STALE_GRACE_SEC):
                    return _cache_df
        raise ValueError(f"No se pudo obtener el headcount desde OneDrive: {exc}") from exc


def actualizar_headcount(_file=None) -> dict[str, Any]:
    global _cache_df, _cache_loaded_at, _cache_url_used
    with _cache_lock:
        _cache_df = None
        _cache_loaded_at = 0.0
        _cache_url_used = ""
    return {
        "message": (
            "Caché invalidado. El headcount se actualizará automáticamente desde "
            "OneDrive en la próxima consulta."
        )
    }


def obtener_activos(cliente: str | None = None) -> list[dict[str, Any]]:
    try:
        df = obtener_df_headcount()
        header_row_idx = None
        header_map: dict[str, int] = {}
        for i in range(len(df.index)):
            row_values = df.iloc[i].tolist()
            normalized = [_normalize_header(v) for v in row_values]
            if "STATUS OPERACIÓN" in normalized or "STATUS OPERACION" in normalized:
                header_row_idx = i
                header_map = {normalized[j]: j for j in range(len(normalized))}
                break

        if header_row_idx is None:
            raise ValueError("No se encontró la fila de encabezados con 'STATUS OPERACIÓN'.")

        def col(name: str) -> int | None:
            if name in header_map:
                return header_map[name]
            alt = name.replace("Ó", "O")
            if alt in header_map:
                return header_map[alt]
            return None

        required = [
            "STATUS OPERACIÓN",
            "NOMBRE COMPLETO",
            "APELLIDO PATERNO",
            "APELLIDO MATERNO",
            "NOMBRE",
            "CLIENTE",
            "PATRON",
            "FECHA DE INGRESO",
            "SUELDO DIARIO",
            "PUESTO",
            "NSS",
            "RFC HOMOCLAVE",
            "CURP",
            "CP FISCAL",
            "STATUS IMSS",
            "GENERO",
        ]
        missing = [key for key in required if col(key) is None]
        if missing:
            raise ValueError(f"Faltan columnas requeridas en headcount: {', '.join(missing)}")

        activos: list[dict[str, Any]] = []
        for i in range(header_row_idx + 1, len(df.index)):
            row_values = df.iloc[i].tolist()
            status_raw = row_values[col("STATUS OPERACIÓN")] if col("STATUS OPERACIÓN") is not None else ""
            status = _normalize_spaces(str(status_raw or "").strip().upper())
            if status != "ALTA":
                continue

            record = {
                "nombre_completo": _normalize_name(row_values[col("NOMBRE COMPLETO")]),
                "apellido_paterno": _normalize_spaces(str(row_values[col("APELLIDO PATERNO")] or "").strip()),
                "apellido_materno": _normalize_spaces(str(row_values[col("APELLIDO MATERNO")] or "").strip()),
                "nombre": _normalize_spaces(str(row_values[col("NOMBRE")] or "").strip()),
                "cliente": _normalize_spaces(str(row_values[col("CLIENTE")] or "").strip()),
                "patron": _normalize_spaces(str(row_values[col("PATRON")] or "").strip()),
                "fecha_ingreso": _format_fecha(row_values[col("FECHA DE INGRESO")]),
                "sueldo_diario": None if _is_empty(row_values[col("SUELDO DIARIO")]) else row_values[col("SUELDO DIARIO")],
                "puesto": _normalize_spaces(str(row_values[col("PUESTO")] or "").strip()),
                "nss": _normalize_spaces(str(row_values[col("NSS")] or "").strip()),
                "rfc_homoclave": _normalize_spaces(str(row_values[col("RFC HOMOCLAVE")] or "").strip()),
                "curp": _normalize_spaces(str(row_values[col("CURP")] or "").strip()),
                "cp_fiscal": _normalize_spaces(str(row_values[col("CP FISCAL")] or "").strip()),
                "status_imss": _normalize_spaces(str(row_values[col("STATUS IMSS")] or "").strip()),
                "genero": _normalize_spaces(str(row_values[col("GENERO")] or "").strip()),
            }
            if not record["nombre_completo"]:
                continue
            activos.append(record)

        if cliente:
            filtro = str(cliente).strip().casefold()
            activos = [item for item in activos if str(item.get("cliente", "")).strip().casefold() == filtro]
        return activos
    except Exception as exc:
        raise ValueError(f"No se pudo leer headcount desde OneDrive: {exc}") from exc


def buscar_trabajador(nombre_completo: str) -> dict[str, Any] | None:
    try:
        if not nombre_completo:
            return None
        objetivo = _normalize_name(nombre_completo)
        for item in obtener_activos():
            if _normalize_name(item.get("nombre_completo", "")) == objetivo:
                return item
        return None
    except Exception as exc:
        raise ValueError(f"No se pudo buscar trabajador: {exc}") from exc


def obtener_metadata_headcount() -> dict[str, Any]:
    with _cache_lock:
        loaded_at = _cache_loaded_at
    fecha = None
    if loaded_at > 0:
        try:
            approx_dt = datetime.now() - pd.to_timedelta(time.monotonic() - loaded_at, unit="s")
            fecha = approx_dt.strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            fecha = None
    return {
        "url_configurada": bool(_headcount_url()),
        "fecha_actualizacion": fecha,
    }
