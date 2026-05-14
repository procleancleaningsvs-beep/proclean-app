from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from modules.facturacion.config import (
    ALERTA_SET,
    OPERATIVO_SET,
    PAGO_SET,
    archivofaltante_auto_activo,
    cliente_requiere_po_oc,
)

MES_NOMBRE_A_NUM = {
    "ENERO": 1,
    "FEBRERO": 2,
    "MARZO": 3,
    "ABRIL": 4,
    "MAYO": 5,
    "JUNIO": 6,
    "JULIO": 7,
    "AGOSTO": 8,
    "SEPTIEMBRE": 9,
    "OCTUBRE": 10,
    "NOVIEMBRE": 11,
    "DICIEMBRE": 12,
}


def strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def norm_key(s: str) -> str:
    return " ".join(strip_accents(str(s or "")).upper().split())


def fix_cliente_name(s: str) -> str:
    t = " ".join(str(s or "").strip().split())
    t = re.sub(r"\bGEEP\b", "GEPP", t, flags=re.IGNORECASE)
    return t


def parse_mes_num(name: str | None) -> int | None:
    if not name:
        return None
    k = norm_key(name)
    return MES_NOMBRE_A_NUM.get(k)


def parse_alertas_json(raw: str | None) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for x in data:
        if isinstance(x, str):
            k = x.strip().upper()
            if k in ALERTA_SET:
                out.append(k)
    return out


def dump_alertas_json(vals: list[str]) -> str:
    clean = []
    for x in vals:
        if not isinstance(x, str):
            continue
        k = x.strip().upper()
        if k in ALERTA_SET and k not in clean:
            clean.append(k)
    return json.dumps(clean, ensure_ascii=False)


def split_operativo_y_pago(estatus_raw: str | None) -> tuple[str | None, str | None]:
    """
    A partir del texto de ESTATUS del Excel (puede mezclar pago),
    devuelve (fragmento operativo, fragmento pago) sin normalizar aún.
    """
    if estatus_raw is None:
        return None, None
    s = str(estatus_raw).strip()
    if not s:
        return None, None
    u = norm_key(s)
    # Pagos explícitos en la misma celda
    if "PAGADA" in u or "PAGADO" in u:
        return None, "PAGADO"
    return s, None


def normalize_estatus_operativo(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    u = norm_key(s)
    if u in ("PAGADA", "PAGADO"):
        return None
    if u in ("NO PORTAL",):
        return "ENVIADO"
    if "PENDIENTE" in u and "NR" in u:
        return "PENDIENTE NR"
    if u in ("RECEPCION", "FALTA NR", "FALTA NR.", "FALTA  NR"):
        return "PENDIENTE NR"
    if "RECEPC" in u and "NR" not in u:
        return "PENDIENTE NR"
    if "SE MANDO COT" in u or "SE MANDÓ COT" in strip_accents(s).upper() or "ENVIO COT" in u or "ENVÍO COT" in u:
        return "COTIZACIÓN ENVIADA"
    if u in ("ENVIADA", "ENVIADAS"):
        return "ENVIADO"
    if u == "ENVIADO":
        return "ENVIADO"
    if u == "EN COLA":
        return "EN COLA"
    if u == "PORTAL":
        return "PORTAL"
    if u == "LISTO":
        return "LISTO"
    if u == "COTIZACION ENVIADA":
        return "COTIZACIÓN ENVIADA"
    # Valores desconocidos: devolver None para decidir fallback en importación
    return None


def normalize_estatus_pago(raw: str | None, *, tiene_fecha_pago: bool = False) -> str:
    if tiene_fecha_pago:
        return "PAGADO"
    if raw is None or not str(raw).strip():
        return "PENDIENTE"
    u = norm_key(str(raw))
    if u in ("PAGADA", "PAGADO"):
        return "PAGADO"
    if "PARCIAL" in u:
        return "PARCIAL"
    if "NO APLICA" in u or "N/A" in u:
        return "NO APLICA"
    return "PENDIENTE"


def alertas_desde_texto_excel(comentario: str | None, estatus_raw: str | None) -> list[str]:
    """Detecta alertas legadas mezcladas en comentarios o estatus (no como estatus operativo)."""
    blob = f"{comentario or ''} {estatus_raw or ''}"
    u = norm_key(blob)
    out: list[str] = []
    if "URGENTE" in u:
        out.append("URGENTE")
    if re.search(r"\bERROR\b", u):
        out.append("ERROR")
    if "REFACTURAR" in u:
        out.append("REFACTURAR")
    if "FALTA COMPROBANTE" in u or "FALTA COMPROBANT" in u:
        out.append("FALTA COMPROBANTE")
    if "FALTA PO" in u or re.search(r"\bSIN PO\b", u):
        out.append("SIN PO/OC")
    return out


def merge_alertas(manual: list[str], auto_extra: list[str]) -> list[str]:
    merged: list[str] = []
    for src in (manual, auto_extra):
        for x in src:
            k = str(x).strip().upper()
            if k in ALERTA_SET and k not in merged:
                merged.append(k)
    return merged


def compute_auto_alertas(
    *,
    cliente: str,
    po_oc: str | None,
    tiene_pdf: bool,
    tiene_xml: bool,
    estatus_operativo: str,
    numero_factura: str | None = None,
    manual_alertas: list[str] | None = None,
) -> list[str]:
    extra: list[str] = []
    if cliente_requiere_po_oc(cliente):
        if not (po_oc and str(po_oc).strip()):
            extra.append("SIN PO/OC")
    num_ok = bool(numero_factura and str(numero_factura).strip() and re.search(r"\d", str(numero_factura)))
    if num_ok and (not tiene_pdf or not tiene_xml) and archivofaltante_auto_activo(estatus_operativo):
        extra.append("ARCHIVO FALTANTE")
    return merge_alertas(manual_alertas or [], extra)


def coerce_operativo_o_default(raw: str | None, *, fallback: str = "EN COLA") -> str:
    n = normalize_estatus_operativo(raw)
    if n in OPERATIVO_SET:
        return n
    return fallback


def validar_factura_payload(data: dict[str, Any]) -> tuple[bool, str | None]:
    op = str(data.get("estatus_operativo") or "").strip()
    if op not in OPERATIVO_SET:
        return False, "Estatus operativo no válido."
    pg = str(data.get("estatus_pago") or "").strip()
    if pg not in PAGO_SET:
        return False, "Estatus de pago no válido."
    alertas = data.get("alertas")
    if isinstance(alertas, list):
        for a in alertas:
            if str(a).strip().upper() not in ALERTA_SET:
                return False, f"Alerta no válida: {a}"
    return True, None


def extraer_numero_factura_desde_nombre_archivo(filename: str) -> str | None:
    """
    Extrae candidato a número de factura desde el nombre de archivo.
    Busca el último token alfanumérico largo antes de la extensión.
    """
    stem = Path(str(filename or "")).stem
    stem = stem.replace("_", " ")
    candidates = re.findall(r"[A-Za-z]*\d[\w\-]*|\d+", stem)
    if not candidates:
        return None
    candidates.sort(key=len, reverse=True)
    for c in candidates:
        c2 = c.strip().upper()
        if len(c2) >= 3 and re.search(r"\d", c2):
            return c2
    return None
