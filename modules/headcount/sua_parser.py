from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import fitz  # PyMuPDF

from modules.headcount.matching import normalize_curp, normalize_nss, normalize_text

_ANCHORS = (
    "SISTEMA UNICO DE AUTODETERMINACION",
    "CEDULA DE DETERMINACION DE CUOTAS",
    "NO. DE SEGURIDAD SOCIAL",
    "REGISTRO PATRONAL",
    "TOTAL DE COTIZANTES",
)

_HEADER_SKIP_FRAGMENTS = (
    "SISTEMA UNICO DE AUTODETERMINACION",
    "CEDULA DE DETERMINACION",
    "NO. DE SEGURIDAD SOCIAL",
    "N O M B R E",
    "RFC/CURP",
    "REGISTRO PATRONAL",
    "NOMBRE O RAZON SOCIAL",
    "PERIODO DE PROCESO",
    "FECHA DE PROCESO",
    "AREA GEOGRAFICA",
    "DELEGACION IMSS",
    "SUBDELEGACION",
    "PRIMA RT",
    "MOV",
    "DIAS",
    "SDI",
    "PAGINA",
    "HOJA",
)

_NSS_RE = re.compile(r"\b(\d{2}-\d{2}-\d{2}-\d{4}-\d)\b")
_CURP_RE = re.compile(r"\b([A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d)\b", re.IGNORECASE)
_MOV_RE = re.compile(
    r"\b(ALTA|BAJA|REIN|P/?CV|P/?IV|PCV|PIV)\b",
    re.IGNORECASE,
)
_FECHA_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
_NUM_RE = re.compile(r"\b(\d+(?:\.\d+)?)\b")
_TOTAL_COT_RE = re.compile(
    r"TOTAL\s+DE\s+COTIZANTES\s*[:\s]*(\d+)",
    re.IGNORECASE,
)
_RP_RE = re.compile(r"REGISTRO\s+PATRONAL\s*[:\s]*([A-Z0-9\-]+)", re.IGNORECASE)
_RAZON_RE = re.compile(r"(?:NOMBRE O RAZON SOCIAL|RAZON SOCIAL)\s*[:\s]*(.+)", re.IGNORECASE)
_RFC_RE = re.compile(r"RFC\s*(?:PATRONAL)?\s*[:\s]*([A-Z&]{3,4}\d{6}[A-Z0-9]{3})", re.IGNORECASE)
_PERIODO_RE = re.compile(r"PERIODO\s+DE\s+PROCESO\s*[:\s]*([^\n]+)", re.IGNORECASE)
_FECHA_PROC_RE = re.compile(r"FECHA\s+DE\s+PROCESO\s*[:\s]*(\d{2}/\d{2}/\d{4})", re.IGNORECASE)
_AREA_RE = re.compile(r"AREA\s+GEOGRAFICA\s*[:\s]*([^\n]+)", re.IGNORECASE)
_DELEG_RE = re.compile(r"DELEGACION\s+IMSS\s*[:\s]*([^\n]+)", re.IGNORECASE)
_SUBDEL_RE = re.compile(r"SUBDELEGACION\s+IMSS\s*[:\s]*([^\n]+)", re.IGNORECASE)
_PRIMA_RE = re.compile(r"PRIMA\s+RT\s*[:\s]*([^\n]+)", re.IGNORECASE)


@dataclass
class SuaParseResult:
    ok: bool
    es_sua: bool = False
    metadatos: dict[str, Any] = field(default_factory=dict)
    trabajadores: list[dict[str, Any]] = field(default_factory=list)
    total_cotizantes: int | None = None
    trabajadores_extraidos: int = 0
    paginas_procesadas: int = 0
    registros_por_pagina: list[int] = field(default_factory=list)
    ultimos_registros: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    diagnostico: dict[str, Any] = field(default_factory=dict)


def _normalize_page_text(text: str) -> str:
    t = text.replace("\r", "\n")
    t = re.sub(r"[ \t]+", " ", t)
    return t


def _line_is_header(line: str) -> bool:
    n = normalize_text(line)
    if not n:
        return True
    for frag in _HEADER_SKIP_FRAGMENTS:
        if frag in n:
            return True
    if _NSS_RE.search(line) and "NO. DE SEGURIDAD SOCIAL" in n:
        return True
    return False


def _extract_metadata(full_text: str) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    m = _RP_RE.search(full_text)
    if m:
        meta["registro_patronal"] = m.group(1).strip()
    m = _RAZON_RE.search(full_text)
    if m:
        meta["razon_social"] = m.group(1).strip()[:200]
    m = _RFC_RE.search(full_text)
    if m:
        meta["rfc_patronal"] = m.group(1).strip().upper()
    m = _PERIODO_RE.search(full_text)
    if m:
        meta["periodo_proceso"] = m.group(1).strip()[:80]
    m = _FECHA_PROC_RE.search(full_text)
    if m:
        meta["fecha_proceso"] = m.group(1).strip()
    m = _AREA_RE.search(full_text)
    if m:
        meta["area_geografica"] = m.group(1).strip()[:80]
    m = _DELEG_RE.search(full_text)
    if m:
        meta["delegacion_imss"] = m.group(1).strip()[:80]
    m = _SUBDEL_RE.search(full_text)
    if m:
        meta["subdelegacion_imss"] = m.group(1).strip()[:80]
    m = _PRIMA_RE.search(full_text)
    if m:
        meta["prima_rt"] = m.group(1).strip()[:40]
    totals = list(_TOTAL_COT_RE.finditer(full_text))
    if totals:
        meta["total_cotizantes"] = int(totals[-1].group(1))
    return meta


def _parse_worker_line(line: str, pagina: int) -> dict[str, Any] | None:
    nss_m = _NSS_RE.search(line)
    if not nss_m:
        return None
    if _line_is_header(line):
        return None

    nss_orig = nss_m.group(1)
    rest = line[nss_m.end() :].strip()
    curp = ""
    curp_m = _CURP_RE.search(rest)
    if curp_m:
        curp = normalize_curp(curp_m.group(1))
        rest = (rest[: curp_m.start()] + rest[curp_m.end() :]).strip()

    mov = ""
    mov_fecha = ""
    mov_m = _MOV_RE.search(rest)
    if mov_m:
        mov = mov_m.group(1).upper().replace(" ", "")
        if mov in {"PCV", "PIV"}:
            mov = f"P/{mov[1:]}"
        rest = (rest[: mov_m.start()] + rest[mov_m.end() :]).strip()

    fechas = _FECHA_RE.findall(rest)
    if fechas:
        mov_fecha = fechas[0]
        rest = rest.replace(mov_fecha, " ").strip()

    nums = _NUM_RE.findall(rest)
    dias = None
    sdi = None
    if nums:
        try:
            dias = int(float(nums[0]))
        except ValueError:
            dias = None
        if len(nums) >= 2:
            try:
                sdi = float(nums[-1])
            except ValueError:
                sdi = None

    nombre = re.sub(r"\s+", " ", rest).strip(" ,;")
    nombre = re.sub(_MOV_RE, " ", nombre).strip()
    if len(nombre) < 3:
        return None

    return {
        "nss_sua_original": nss_orig,
        "nss_normalizado": normalize_nss(nss_orig),
        "nombre_sua_original": nombre,
        "nombre_normalizado": normalize_text(nombre),
        "curp": curp,
        "movimiento_clave": mov,
        "movimiento_fecha": mov_fecha,
        "dias": dias,
        "sdi": sdi,
        "pagina_origen": pagina,
    }


def _parse_workers_from_pages(pages_text: list[str]) -> tuple[list[dict[str, Any]], list[int]]:
    trabajadores: list[dict[str, Any]] = []
    por_pagina: list[int] = []
    seen_nss: set[str] = set()

    for pi, page_text in enumerate(pages_text, start=1):
        count_page = 0
        for raw_line in page_text.split("\n"):
            line = raw_line.strip()
            if not line or _line_is_header(line):
                continue
            worker = _parse_worker_line(line, pi)
            if not worker:
                continue
            nss_key = worker["nss_normalizado"]
            if nss_key and nss_key in seen_nss:
                continue
            if nss_key:
                seen_nss.add(nss_key)
            trabajadores.append(worker)
            count_page += 1
        por_pagina.append(count_page)

    return trabajadores, por_pagina


def _detect_sua(full_text: str) -> bool:
    n = normalize_text(full_text)
    hits = sum(1 for a in _ANCHORS if a in n)
    return hits >= 2


def parse_sua_pdf_bytes(pdf_bytes: bytes) -> SuaParseResult:
    result = SuaParseResult(ok=False, es_sua=False)
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        result.error = f"No se pudo abrir el PDF: {exc}"
        return result

    pages_text: list[str] = []
    try:
        for page in doc:
            pages_text.append(_normalize_page_text(page.get_text("text") or ""))
    finally:
        doc.close()

    result.paginas_procesadas = len(pages_text)
    full_text = "\n".join(pages_text)
    result.es_sua = _detect_sua(full_text)
    if not result.es_sua:
        result.error = "El archivo no parece ser una Cédula SUA válida."
        return result

    result.metadatos = _extract_metadata(full_text)
    result.total_cotizantes = result.metadatos.get("total_cotizantes")
    trabajadores, por_pagina = _parse_workers_from_pages(pages_text)
    result.trabajadores = trabajadores
    result.registros_por_pagina = por_pagina
    result.trabajadores_extraidos = len(trabajadores)
    result.ultimos_registros = trabajadores[-5:]

    if result.total_cotizantes is None:
        result.error = "No se encontró 'Total de Cotizantes' en el PDF."
        result.diagnostico = _build_diagnostico(result)
        return result

    if result.trabajadores_extraidos != result.total_cotizantes:
        result.error = (
            "La lectura del SUA no coincide con el Total de Cotizantes. "
            "No se generó reporte para evitar resultados incorrectos."
        )
        result.diagnostico = _build_diagnostico(result)
        return result

    result.ok = True
    return result


def _build_diagnostico(result: SuaParseResult) -> dict[str, Any]:
    total = result.total_cotizantes or 0
    extraidos = result.trabajadores_extraidos
    return {
        "total_cotizantes": total,
        "trabajadores_extraidos": extraidos,
        "diferencia": extraidos - total,
        "paginas_procesadas": result.paginas_procesadas,
        "registros_por_pagina": result.registros_por_pagina,
        "ultimos_registros": result.ultimos_registros,
        "mensaje": result.error
        or "La lectura del SUA no coincide con el Total de Cotizantes. No se generó reporte para evitar resultados incorrectos.",
    }
