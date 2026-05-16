"""Extracción de datos de negocio desde CFDI (XML) y heurística en PDF."""

from __future__ import annotations

import re
from typing import Any


def _f(s: str | None) -> float | None:
    if s is None or str(s).strip() == "":
        return None
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


def extract_datos_desde_xml_cfdi(data: bytes) -> dict[str, Any]:
    """
    Lee atributos comunes del Comprobante CFDI 3.3/4.0 y traslado IVA (mejor esfuerzo).
    Devuelve claves opcionales: numero, subtotal, iva, total, fecha (ISO date).
    """
    out: dict[str, Any] = {}
    try:
        txt = data.decode("utf-8", errors="ignore")
    except Exception:
        return out
    if "Comprobante" not in txt and "cfdi:Comprobante" not in txt:
        return out

    m_fecha = re.search(r'Fecha\s*=\s*["\']([^"\']+)["\']', txt, re.I)
    if m_fecha:
        raw = m_fecha.group(1).strip()
        out["fecha"] = raw[:10] if len(raw) >= 10 else None

    m_sub = re.search(r'SubTotal\s*=\s*["\']([^"\']+)["\']', txt, re.I)
    if m_sub:
        out["subtotal"] = _f(m_sub.group(1))

    m_tot = re.search(r'Total\s*=\s*["\']([^"\']+)["\']', txt, re.I)
    if m_tot:
        out["total"] = _f(m_tot.group(1))

    # IVA trasladado (primer Importe con NombreImpuesto IVA)
    m_iva = re.search(
        r'(?:NombreImpuesto|Impuesto)\s*=\s*["\']IVA["\'][^>]{0,400}?Importe\s*=\s*["\']([^"\']+)["\']',
        txt,
        re.I | re.DOTALL,
    )
    if not m_iva:
        m_iva = re.search(r'Importe\s*=\s*["\']([^"\']+)["\'][^<]{0,200}?NombreImpuesto\s*=\s*["\']IVA["\']', txt, re.I | re.DOTALL)
    if m_iva:
        out["iva"] = _f(m_iva.group(1))

    serie = ""
    folio = ""
    ms = re.search(r'Serie\s*=\s*["\']([^"\']*)["\']', txt, re.I)
    if ms:
        serie = (ms.group(1) or "").strip()
    mf = re.search(r'Folio\s*=\s*["\']([^"\']*)["\']', txt, re.I)
    if mf:
        folio = (mf.group(1) or "").strip()
    num = f"{serie}{folio}".strip()
    if num and re.search(r"\d", num):
        out["numero"] = num.upper() if num.isalnum() else num

    return out


def extract_datos_desde_pdf(data: bytes) -> dict[str, Any]:
    """Heurística ligera: totales tipo $12,345.67 cerca de TOTAL / IMPORTE."""
    out: dict[str, Any] = {}
    if not data:
        return out
    try:
        from pypdf import PdfReader
    except ImportError:
        return out
    try:
        from io import BytesIO

        reader = PdfReader(BytesIO(data))
    except Exception:
        return out
    chunks: list[str] = []
    for page in reader.pages[:6]:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            pass
    text = "\n".join(chunks)
    if not text.strip():
        return out

    # Último monto con formato mexicano/USD-like con coma miles
    amounts = re.findall(r"\$?\s*([\d]{1,3}(?:,\d{3})*(?:\.\d{2})?)", text)
    best: float | None = None
    for a in amounts:
        v = _f(a.replace(",", ""))
        if v is not None and (best is None or v > best):
            best = v
    if best is not None and best > 0:
        out["total"] = best

    return out


def extract_datos_desde_adjunto_bytes(data: bytes, *, ext: str) -> dict[str, Any]:
    e = (ext or "").lower().strip(".")
    if e == "xml":
        return extract_datos_desde_xml_cfdi(data)
    if e == "pdf":
        return extract_datos_desde_pdf(data)
    return {}
