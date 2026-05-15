"""Extracción heurística de fecha de emisión desde PDF o XML (CFDI)."""

from __future__ import annotations

import re
from datetime import datetime
from io import BytesIO


def _parse_flexible_date(s: str) -> str | None:
    if not s or not str(s).strip():
        return None
    s_full = str(s).strip()
    head = s_full.split("T", 1)[0] if "T" in s_full else s_full
    s = head[:10].replace("/", "-")
    # ISO YYYY-MM-DD
    m = re.search(r"(20\d{2})[-/]([01]\d)[-/]([0-3]\d)", s)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        cand = f"{y}-{mo}-{d}"
        try:
            return datetime.strptime(cand, "%Y-%m-%d").date().isoformat()
        except ValueError:
            pass
    # DD/MM/YYYY
    m2 = re.search(r"([0-3]\d)[-/]([01]\d)[-/](20\d{2})", s_full)
    if m2:
        d, mo, y = m2.group(1), m2.group(2), m2.group(3)
        try:
            return datetime(int(y), int(mo), int(d)).date().isoformat()
        except ValueError:
            pass
    return None


def extract_fecha_emision_from_pdf(data: bytes) -> str | None:
    if not data:
        return None
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        reader = PdfReader(BytesIO(data))
    except Exception:
        return None
    chunks: list[str] = []
    for page in reader.pages[:8]:
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        chunks.append(t)
    text = "\n".join(chunks)
    if not text.strip():
        return None
    # CFDI-like attribute in text
    m = re.search(r'Fecha\s*[=:]\s*["\']?(20\d{2}-[01]\d-[0-3]\d)', text, re.I)
    if m:
        hit = _parse_flexible_date(m.group(1))
        if hit:
            return hit
    # Fecha de emisión / Emisión
    for pat in (
        r"(?:fecha\s+de\s+emisión|fecha\s+emisión|emisión|fecha)\s*[:\s]+([0-3]?\d[/\-][01]?\d[/\-]20\d{2}|20\d{2}[/\-][01]?\d[/\-][0-3]?\d)",
        r"(20\d{2}-[01]\d-[0-3]\d)",
    ):
        for m in re.finditer(pat, text, re.I):
            hit = _parse_flexible_date(m.group(1))
            if hit:
                return hit
    return None


def extract_fecha_emision_from_xml(data: bytes) -> str | None:
    if not data:
        return None
    try:
        txt = data.decode("utf-8", errors="ignore")
    except Exception:
        return None
    m = re.search(r'Fecha\s*=\s*["\'](20\d{2}-[01]\d-[0-3]\d)', txt, re.I)
    if m:
        return _parse_flexible_date(m.group(1))
    m2 = re.search(r"(20\d{2}-[01]\d-[0-3]\dT)", txt)
    if m2:
        return _parse_flexible_date(m2.group(1))
    return None


def extract_fecha_emision_from_bytes(data: bytes, *, ext: str) -> str | None:
    e = (ext or "").lower().strip(".")
    if e == "pdf":
        return extract_fecha_emision_from_pdf(data)
    if e in {"xml"}:
        return extract_fecha_emision_from_xml(data)
    return None
