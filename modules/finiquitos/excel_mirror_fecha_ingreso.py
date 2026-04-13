"""Headcount OneDrive (link compartido): descarga, caché y cruce nombre → fecha de ingreso."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import unicodedata
import zipfile
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import pandas as pd
import requests

logger = logging.getLogger(__name__)

HEADCOUNT_ONEDRIVE_URL_ENV = "HEADCOUNT_ONEDRIVE_URL"
HEADCOUNT_EXCEL_SHEET_NAME_ENV = "HEADCOUNT_EXCEL_SHEET_NAME"

_DEFAULT_ONEDRIVE_URL = (
    "https://1drv.ms/x/c/8e68947e1a885aad/IQCP45t3XNYPTp8iKlfiKuuEAZnbHZWVgzf1HzZTjJkpCV0?e=XcsNiB"
)

_COL_NOMBRE = "NOMBRE COMPLETO"
_COL_FECHA = "FECHA DE INGRESO"

_CACHE_TTL_SEC = 300
_STALE_GRACE_SEC = 300

_cache_lock = threading.Lock()
_cache_df: pd.DataFrame | None = None
_cache_loaded_at: float = 0.0
_cache_url_used: str = ""


def headcount_onedrive_url_resolved() -> str:
    raw = (os.environ.get(HEADCOUNT_ONEDRIVE_URL_ENV) or "").strip()
    return raw or _DEFAULT_ONEDRIVE_URL


def _sheet_name_arg() -> str | int:
    raw = (os.environ.get(HEADCOUNT_EXCEL_SHEET_NAME_ENV) or "").strip()
    if not raw:
        return 0
    if raw.isdigit():
        return int(raw)
    return raw


def normalizar_nombre(texto: str) -> str:
    """Trim, mayúsculas, sin acentos, espacios colapsados, caracteres raros → espacio."""
    s = str(texto or "").strip().replace("\u00a0", " ")
    nk = unicodedata.normalize("NFKD", s)
    buf: list[str] = []
    for ch in nk:
        if unicodedata.combining(ch):
            continue
        cat = unicodedata.category(ch)
        if ch.isspace() or ch in "-_'.,;:/\\|()[]{}\"«»":
            buf.append(" ")
        elif cat.startswith("L") or cat == "Nd":
            buf.append(ch)
        else:
            buf.append(" ")
    return " ".join("".join(buf).split()).upper()


# Alias usado en otras partes del repo
normalizar_nombre_para_cruce = normalizar_nombre


def _norm_header(h: Any) -> str:
    return normalizar_nombre(str(h).strip())


def _columnas_requeridas(df: pd.DataFrame) -> tuple[str, str] | tuple[None, None]:
    want_n = _norm_header(_COL_NOMBRE)
    want_f = _norm_header(_COL_FECHA)
    col_nombre = col_fecha = None
    for c in df.columns:
        key = _norm_header(c)
        if key == want_n:
            col_nombre = str(c)
        elif key == want_f:
            col_fecha = str(c)
    if col_nombre and col_fecha:
        return col_nombre, col_fecha
    return None, None


def _celda_es_marca_sin_fecha(val: Any) -> bool:
    try:
        if pd.isna(val):
            return True
    except TypeError:
        pass
    if isinstance(val, pd.Timestamp):
        return bool(pd.isna(val))
    s = str(val).strip()
    if not s:
        return True
    u = s.upper().replace(" ", "")
    if u in ("NO", "N/A", "NA", "-", "--"):
        return True
    return False


def _parse_fecha_celda(val: Any) -> date | None:
    if _celda_es_marca_sin_fecha(val):
        return None
    if isinstance(val, pd.Timestamp):
        if pd.isna(val):
            return None
        return val.date()
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    if re.fullmatch(r"\d+\.0+", s) or re.fullmatch(r"\d+", s):
        try:
            n = int(float(s))
            base = date(1899, 12, 30)
            return base + timedelta(days=n)
        except (ValueError, OSError, OverflowError):
            pass
    return None


def _is_html_payload(data: bytes, content_type: str | None) -> bool:
    ct = (content_type or "").lower()
    if "text/html" in ct or "application/xhtml" in ct:
        return True
    head = data[:1200].lstrip().lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html") or head.startswith(b"<head")


def _looks_like_xlsx(data: bytes) -> bool:
    if len(data) < 64 or not data.startswith(b"PK"):
        return False
    try:
        zf = zipfile.ZipFile(BytesIO(data))
        names = set(zf.namelist())
        return "[Content_Types].xml" in names and any(n.startswith("xl/") for n in names)
    except zipfile.BadZipFile:
        return False


def _url_with_download_flag(url: str) -> str:
    u = urlparse(url)
    pairs = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k.lower() != "download"]
    pairs.append(("download", "1"))
    return urlunparse((u.scheme, u.netloc, u.path, u.params, urlencode(pairs), u.fragment))


def descargar_excel_desde_onedrive(url: str) -> bytes:
    """Descarga el .xlsx siguiendo redirecciones; si llega HTML, reintenta con download=1."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ProCleanApp/1.0; +https://railway.app)"}
    last_err: Exception | None = None
    for attempt, u in enumerate((url, _url_with_download_flag(url))):
        try:
            r = requests.get(u, allow_redirects=True, timeout=30, headers=headers)
            r.raise_for_status()
            data = r.content
            ctype = r.headers.get("Content-Type")
            if _is_html_payload(data, ctype) or not _looks_like_xlsx(data):
                if attempt == 0:
                    logger.info(
                        "Descarga OneDrive: respuesta no es xlsx usable (html=%s, len=%s); reintentando con download=1.",
                        _is_html_payload(data, ctype),
                        len(data),
                    )
                    continue
                raise ValueError(
                    "El contenido descargado no parece un archivo Excel (.xlsx). "
                    "Comprueba que el enlace sea público o que OneDrive permita la descarga anónima."
                )
            return data
        except Exception as exc:
            last_err = exc
            logger.warning("Intento de descarga OneDrive falló (%s): %s", attempt + 1, exc)
            continue
    assert last_err is not None
    raise last_err


def _load_dataframe_fresh(url: str) -> pd.DataFrame:
    raw = descargar_excel_desde_onedrive(url)
    sheet = _sheet_name_arg()
    return pd.read_excel(BytesIO(raw), engine="openpyxl", sheet_name=sheet)


def _get_cached_dataframe(url: str) -> pd.DataFrame:
    """DataFrame con caché en memoria (TTL 5 min); si falla la descarga, usa caché vencida dentro del margen."""
    global _cache_df, _cache_loaded_at, _cache_url_used
    now = time.monotonic()
    with _cache_lock:
        if (
            _cache_df is not None
            and _cache_url_used == url
            and (now - _cache_loaded_at) < _CACHE_TTL_SEC
        ):
            return _cache_df

    try:
        df = _load_dataframe_fresh(url)
        with _cache_lock:
            _cache_df = df
            _cache_loaded_at = time.monotonic()
            _cache_url_used = url
        return df
    except Exception as exc:
        logger.exception("No se pudo descargar o leer el Excel de headcount: %s", exc)
        with _cache_lock:
            stale_age = time.monotonic() - _cache_loaded_at if _cache_df is not None else None
            if _cache_df is not None and _cache_url_used == url and stale_age is not None:
                if stale_age < _CACHE_TTL_SEC + _STALE_GRACE_SEC:
                    logger.warning(
                        "Usando caché Excel antigua (%.0f s desde última descarga exitosa) tras fallo de red/parseo.",
                        stale_age,
                    )
                    return _cache_df
        raise


def buscar_fecha_ingreso_en_dataframe(
    df: pd.DataFrame,
    nombre_completo: str,
) -> tuple[date | None, str | None, str | None]:
    """
    Devuelve (fecha, nombre_encontrado, error).
    - Éxito: (date, str, None)
    - Sin fila: (None, None, mensaje)
    - Filas pero sin fecha válida: (None, nombre_referencia, mensaje)
    """
    nombre_completo = (nombre_completo or "").strip()
    if not nombre_completo:
        return None, None, "El nombre completo es obligatorio."

    col_n, col_f = _columnas_requeridas(df)
    if not col_n or not col_f:
        return None, None, (
            f"El Excel debe incluir las columnas «{_COL_NOMBRE}» y «{_COL_FECHA}» "
            "(los encabezados pueden variar en mayúsculas o espacios)."
        )

    q = normalizar_nombre(nombre_completo)
    if not q:
        return None, None, "El nombre completo es obligatorio."

    work = df[[col_n, col_f]].copy()

    def _cell_norm(x: Any) -> str:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return ""
        return normalizar_nombre(str(x))

    work["__norm"] = work[col_n].map(_cell_norm)
    sub = work[work["__norm"] == q]

    matches: list[tuple[str, Any]] = []
    for _, row in sub.iterrows():
        raw_n = row[col_n]
        if raw_n is None or (isinstance(raw_n, float) and pd.isna(raw_n)):
            continue
        nombre_cell = str(raw_n).strip()
        matches.append((nombre_cell, row[col_f]))

    if not matches:
        return None, None, "No se encontró coincidencia en el Excel."

    if len(matches) > 1:
        logger.warning(
            "Varias filas (%d) con el mismo nombre normalizado; se usa la primera con fecha de ingreso válida.",
            len(matches),
        )

    for nombre_raw, celda in matches:
        if _celda_es_marca_sin_fecha(celda):
            continue
        parsed = _parse_fecha_celda(celda)
        if parsed is not None:
            return parsed, nombre_raw, None

    ref_name = matches[0][0]
    return None, ref_name, "La coincidencia existe, pero no tiene fecha válida."


def buscar_fecha_ingreso_headcount_onedrive(
    nombre_completo: str,
    *,
    url: str | None = None,
) -> tuple[date | None, str | None, str | None]:
    """Descarga (con caché), lee el Excel y cruza por nombre. Sin rutas locales ni Microsoft Graph."""
    resolved = (url or "").strip() or headcount_onedrive_url_resolved()
    try:
        df = _get_cached_dataframe(resolved)
    except Exception as exc:
        return None, None, f"No se pudo obtener el Excel de headcount: {exc}"
    return buscar_fecha_ingreso_en_dataframe(df, nombre_completo)
