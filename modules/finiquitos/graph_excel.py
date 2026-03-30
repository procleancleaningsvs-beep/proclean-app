"""Lectura de fecha de ingreso desde Excel en OneDrive/SharePoint vía Microsoft Graph."""

from __future__ import annotations

import base64
import logging
import os
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"


def _encode_share_id(sharing_url: str) -> str:
    u = sharing_url.strip()
    b = base64.urlsafe_b64encode(u.encode("utf-8")).decode("ascii").rstrip("=")
    return f"u!{b}"


def _client_credentials_token() -> str:
    tenant = (os.environ.get("GRAPH_TENANT_ID") or os.environ.get("AZURE_TENANT_ID") or "").strip()
    client_id = (os.environ.get("GRAPH_CLIENT_ID") or os.environ.get("AZURE_CLIENT_ID") or "").strip()
    secret = (os.environ.get("GRAPH_CLIENT_SECRET") or os.environ.get("AZURE_CLIENT_SECRET") or "").strip()
    if not tenant or not client_id or not secret:
        raise RuntimeError(
            "Falta configuración de Microsoft Graph. Define GRAPH_TENANT_ID, GRAPH_CLIENT_ID y GRAPH_CLIENT_SECRET."
        )
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "client_secret": secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    r = requests.post(url, data=data, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"No se pudo obtener token de Graph (HTTP {r.status_code}).")
    js = r.json()
    tok = js.get("access_token")
    if not tok:
        raise RuntimeError("Respuesta de token sin access_token.")
    return str(tok)


def _headers(token: str, workbook_session: str | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {token}"}
    if workbook_session:
        h["workbook-session-id"] = workbook_session
    return h


def _normalize_name(s: str) -> str:
    s = " ".join((s or "").split())
    nk = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nk if not unicodedata.combining(c)).casefold()


def buscar_fecha_ingreso_excel(
    sharing_url: str,
    nombre_completo: str,
    *,
    token: str | None = None,
) -> tuple[date | None, str | None]:
    """
    Devuelve (fecha, None) o (None, mensaje_error).
    """
    nombre_completo = (nombre_completo or "").strip()
    if not nombre_completo:
        return None, "El nombre completo es obligatorio."

    try:
        tok = token or _client_credentials_token()
    except RuntimeError as exc:
        return None, str(exc)

    share_id = _encode_share_id(sharing_url)
    item_url = f"{GRAPH}/shares/{share_id}/driveItem"
    r = requests.get(item_url, headers=_headers(tok), timeout=90)
    if r.status_code != 200:
        return None, f"No se pudo resolver el archivo compartido (HTTP {r.status_code})."

    item = r.json()
    drive_id = item.get("parentReference", {}).get("driveId") or item.get("driveId")
    item_id = item.get("id")
    if not drive_id or not item_id:
        return None, "Respuesta de Graph sin drive/item."

    sess_url = f"{GRAPH}/drives/{drive_id}/items/{item_id}/workbook/createSession"
    rs = requests.post(sess_url, headers=_headers(tok), json={"persistChanges": False}, timeout=90)
    if rs.status_code not in (200, 201):
        try:
            err = rs.json().get("error", {})
            msg = (err.get("message") or "") + " " + (err.get("code") or "")
        except Exception:
            msg = rs.text[:500]
        if "workbook" in msg.lower() or "not supported" in msg.lower() or rs.status_code == 501:
            return None, (
                "Microsoft Graph no puede abrir la API de Excel para este archivo. "
                "Asegúrate de que el enlace sea de OneDrive/SharePoint empresarial y que el archivo "
                "sea un libro de Excel almacenado en la nube (no solo un enlace de descarga HTML)."
            )
        return None, f"No se pudo crear sesión de libro (HTTP {rs.status_code}): {msg}"

    session_id = rs.json().get("id")
    if not session_id:
        return None, "Sesión de libro sin id."

    range_url = (
        f"{GRAPH}/drives/{drive_id}/items/{item_id}/workbook/worksheets/"
        f"Base%20de%20datos/usedRange(valuesOnly=true)"
    )
    rr = requests.get(range_url, headers=_headers(tok, session_id), timeout=120)

    if rr.status_code != 200:
        return None, f"No se pudo leer la hoja 'Base de datos' (HTTP {rr.status_code})."

    data = rr.json()
    values = data.get("values") or []
    if not values:
        return None, "No se encontró fecha de ingreso en la hoja Base de datos."

    # Columnas H (índice 7) y R (índice 17); usedRange puede empezar en A1.
    rows_hr: list[tuple[Any, Any]] = []
    for row in values:
        if len(row) < 18:
            continue
        cell_r = row[17]
        cell_h = row[7] if len(row) > 7 else None
        if cell_r is None or str(cell_r).strip() == "":
            continue
        rows_hr.append((cell_h, str(cell_r).strip()))

    q = nombre_completo.strip()

    def pick(pred) -> list[tuple[Any, str]]:
        return [(h, r) for h, r in rows_hr if pred(r)]

    ordered = [
        lambda r: r == q,
        lambda r: r.casefold() == q.casefold(),
        lambda r: _normalize_name(r) == _normalize_name(q),
        lambda r: " ".join(r.split()).casefold() == " ".join(q.split()).casefold(),
    ]
    cell_h: Any = None
    for pred in ordered:
        m = pick(pred)
        if len(m) > 1:
            return None, "Se encontraron múltiples coincidencias para el nombre. Ajusta el nombre completo."
        if len(m) == 1:
            cell_h, _ = m[0]
            break

    if cell_h is None:
        return None, "No se encontró fecha de ingreso en la hoja Base de datos."
    parsed = _parse_excel_date(cell_h)
    if parsed is None:
        return None, "Se encontró el trabajador, pero la fecha de ingreso en Excel no es válida."

    return parsed, None


def _parse_excel_date(val: Any) -> date | None:
    if val is None or str(val).strip() == "":
        return None
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
        except Exception:
            pass
    return None
