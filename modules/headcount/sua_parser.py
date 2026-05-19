from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import fitz  # PyMuPDF

from modules.headcount.matching import (
    enrich_sua_worker_fields,
    normalize_curp,
    normalize_nss,
    normalize_text,
    sua_es_activo_al_corte,
    sua_tiene_baja,
)

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
    "SUBTOTAL",
    "TOTAL A PAGAR",
    "U M A",
    "PAGINA",
    "HOJA",
    "CLAVE",
    "FECHA",
    "DIAS",
    "SDI",
    "INC",
    "AUS",
    "C F",
    "EXC",
    "G P S",
    "I V ",
    "G.M.P",
    "P.D.",
    "R.T.",
    "ENFERMEDADES",
    "SUMAS",
    "OBRERA",
    "PATRONAL",
    "ACTIVIDAD",
    "DELEGACION",
    "PRIMA",
)

_NSS_LINE_RE = re.compile(r"^\s*(\d{2}-\d{2}-\d{2}-\d{4}-\d)\s*$")
_NSS_INLINE_RE = re.compile(r"\b(\d{2}-\d{2}-\d{2}-\d{4}-\d)\b")
_CURP_LINE_RE = re.compile(r"^[A-Z]{4}\d{6}[HM][A-Z0-9]{7}\d$", re.IGNORECASE)
_CURP_INLINE_RE = re.compile(r"\b([A-Z]{4}\d{6}[HM][A-Z0-9]{7})\b", re.IGNORECASE)
_MOV_RE = re.compile(
    r"\b(ALTA|BAJA|REIN|P/?CV|P/?IV|PCV|PIV)\b",
    re.IGNORECASE,
)
_FECHA_RE = re.compile(r"^\s*(\d{2}/\d{2}/\d{4})\s*$")
_FECHA_INLINE_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
_LABEL_TOTAL_RE = re.compile(r"TOTAL\s+DE\s+COTIZANTES", re.IGNORECASE)
_INT_LINE_RE = re.compile(r"^\s*(\d{1,6})\s*$")
_AMOUNT_LINE_RE = re.compile(r"^\s*[\d,]+\.\d{2}\s*$")
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
    total_sua_activos_al_corte: int = 0
    total_sua_bajas_periodo: int = 0
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
    if len(n) <= 2 and n.isdigit():
        return False
    for frag in _HEADER_SKIP_FRAGMENTS:
        if frag in n:
            return True
    if _NSS_INLINE_RE.search(line) and "NO. DE SEGURIDAD SOCIAL" in n:
        return True
    if _LABEL_TOTAL_RE.search(line):
        return True
    if re.fullmatch(r"P\s*A\s*G\s*I\s*N\s*A", n):
        return True
    return False


def _normalize_mov_token(raw: str) -> str:
    mov = raw.upper().replace(" ", "")
    if mov in {"PCV", "PIV"}:
        return f"P/{mov[1:]}"
    return mov


def _mov_from_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    upper = stripped.upper()
    if upper in {"ALTA", "BAJA", "REIN", "P/CV", "P/IV", "PCV", "PIV"}:
        return _normalize_mov_token(upper), ""
    mov_m = _MOV_RE.search(stripped)
    if not mov_m:
        return None
    mov = _normalize_mov_token(mov_m.group(1))
    fechas = _FECHA_INLINE_RE.findall(stripped)
    mov_fecha = fechas[0] if fechas else ""
    rest = stripped
    for m in _MOV_RE.finditer(stripped):
        rest = rest.replace(m.group(0), " ", 1)
    for f in fechas:
        rest = rest.replace(f, " ", 1)
    if normalize_text(rest):
        return None
    return mov, mov_fecha


def _looks_like_curp(line: str) -> bool:
    s = line.strip().upper().replace(" ", "").replace("/", "")
    if len(s) != 18:
        return False
    if not re.match(r"^[A-Z]{4}\d{6}[HM]", s):
        return False
    return bool(re.match(r"^[A-Z0-9]{18}$", s))


def _is_skippable_data_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if _AMOUNT_LINE_RE.match(s):
        return True
    if _INT_LINE_RE.match(s.replace(",", "")):
        return True
    if _FECHA_RE.match(s) and _mov_from_line(s) is None:
        return True
    return False


def _parse_orphan_movement_line(line: str) -> tuple[str, str] | None:
    if _NSS_LINE_RE.match(line) or _line_is_header(line):
        return None
    parsed = _mov_from_line(line)
    if parsed:
        mov, mov_fecha = parsed
        if mov_fecha or line.strip().upper() in {"ALTA", "BAJA", "REIN", "P/CV", "P/IV"}:
            return mov, mov_fecha
        return mov, mov_fecha
    mov_m = _MOV_RE.search(line)
    if not mov_m:
        return None
    rest = line
    for m in _MOV_RE.finditer(line):
        rest = rest.replace(m.group(0), " ", 1)
    for f in _FECHA_RE.findall(line):
        rest = rest.replace(f, " ", 1)
    if normalize_text(rest) and len(normalize_text(rest)) > 8:
        return None
    mov = _normalize_mov_token(mov_m.group(1))
    fechas = _FECHA_INLINE_RE.findall(line)
    return mov, fechas[0] if fechas else ""


def _apply_movement_to_worker(worker: dict[str, Any], mov: str, mov_fecha: str) -> None:
    worker["movimiento_clave"] = mov
    if mov_fecha:
        worker["movimiento_fecha"] = mov_fecha


def _parse_block(block_lines: list[str], pagina: int) -> dict[str, Any] | None:
    lines = [ln.strip() for ln in block_lines if ln.strip() and not _line_is_header(ln)]
    if not lines or not _NSS_LINE_RE.match(lines[0]):
        return None

    nss_orig = _NSS_LINE_RE.match(lines[0]).group(1)
    curp = ""
    curp_idx = -1
    mov = ""
    mov_fecha = ""

    for idx, ln in enumerate(lines):
        mk = _mov_from_line(ln)
        if mk:
            mov, mov_fecha = mk
            if not mov_fecha and idx + 1 < len(lines) and _FECHA_RE.match(lines[idx + 1]):
                mov_fecha = lines[idx + 1].strip()

    for idx, ln in enumerate(lines[1:], start=1):
        if _looks_like_curp(ln):
            curp = normalize_curp(ln)
            curp_idx = idx
            break

    nombre_parts: list[str] = []
    for idx in range(1, len(lines)):
        if curp_idx > 0 and idx >= curp_idx:
            break
        ln = lines[idx]
        if _mov_from_line(ln) or _looks_like_curp(ln) or _is_skippable_data_line(ln):
            continue
        if _NSS_LINE_RE.match(ln):
            break
        nombre_parts.append(ln)

    nombre = " ".join(nombre_parts).strip()
    nombre = re.sub(r"\s+", " ", nombre).strip()
    if len(nombre) < 2 and not curp:
        return None

    dias = None
    sdi = None
    ints: list[int] = []
    for ln in lines:
        if _is_skippable_data_line(ln) and _INT_LINE_RE.match(ln.replace(",", "").strip()):
            try:
                ints.append(int(ln.replace(",", "").strip()))
            except ValueError:
                pass
    if ints:
        dias = ints[0] if ints[0] <= 31 else None

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


def _parse_worker_line(line: str, pagina: int) -> dict[str, Any] | None:
    """Fallback: registro en una sola línea (PDFs con layout horizontal)."""
    if _line_is_header(line):
        return None
    if _NSS_LINE_RE.match(line):
        return _parse_block([line], pagina)
    nss_m = _NSS_INLINE_RE.search(line)
    if not nss_m:
        return None

    nss_orig = nss_m.group(1)
    rest = line[nss_m.end() :].strip()
    curp = ""
    curp_m = _CURP_INLINE_RE.search(rest)
    if curp_m:
        curp = normalize_curp(curp_m.group(1))
        rest = (rest[: curp_m.start()] + rest[curp_m.end() :]).strip()

    mov = ""
    mov_fecha = ""
    mov_m = _MOV_RE.search(rest)
    if mov_m:
        mov = _normalize_mov_token(mov_m.group(1))
        rest = (rest[: mov_m.start()] + rest[mov_m.end() :]).strip()

    fechas = _FECHA_INLINE_RE.findall(rest)
    if fechas:
        mov_fecha = fechas[0]
        for f in fechas:
            rest = rest.replace(f, " ").strip()

    nums = re.findall(r"\b(\d+(?:\.\d+)?)\b", rest.replace(",", ""))
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
    if len(nombre) < 3 and not curp:
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


def _page_lines(page_text: str) -> list[str]:
    return [ln.strip() for ln in page_text.split("\n") if ln.strip()]


def _parse_workers_from_pages(pages_text: list[str]) -> tuple[list[dict[str, Any]], list[int]]:
    trabajadores: list[dict[str, Any]] = []
    por_pagina: list[int] = []
    seen_nss: set[str] = set()

    for pi, page_text in enumerate(pages_text, start=1):
        lines = _page_lines(page_text)
        count_page = 0
        nss_indices = [i for i, ln in enumerate(lines) if _NSS_LINE_RE.match(ln)]

        for k, start in enumerate(nss_indices):
            end = nss_indices[k + 1] if k + 1 < len(nss_indices) else len(lines)
            block = lines[start:end]
            worker = _parse_block(block, pi)
            if not worker:
                worker = _parse_worker_line(lines[start], pi)
            if not worker:
                continue
            nss_key = worker.get("nss_normalizado") or ""
            if nss_key and nss_key in seen_nss:
                continue
            if nss_key:
                seen_nss.add(nss_key)
            trabajadores.append(worker)
            count_page += 1

        if nss_indices:
            tail_start = nss_indices[-1] + 1
            for ln in lines[tail_start:]:
                orphan = _parse_orphan_movement_line(ln)
                if orphan and trabajadores:
                    mov, mov_fecha = orphan
                    _apply_movement_to_worker(trabajadores[-1], mov, mov_fecha)
            for ln in lines:
                if _NSS_LINE_RE.match(ln):
                    continue
                if _NSS_INLINE_RE.search(ln) and not _line_is_header(ln):
                    worker = _parse_worker_line(ln, pi)
                    if worker:
                        nss_key = worker.get("nss_normalizado") or ""
                        if nss_key and nss_key not in seen_nss:
                            seen_nss.add(nss_key)
                            trabajadores.append(worker)
                            count_page += 1
        else:
            for ln in lines:
                orphan = _parse_orphan_movement_line(ln)
                if orphan and trabajadores:
                    mov, mov_fecha = orphan
                    _apply_movement_to_worker(trabajadores[-1], mov, mov_fecha)
                elif not _line_is_header(ln):
                    worker = _parse_worker_line(ln, pi)
                    if worker:
                        nss_key = worker.get("nss_normalizado") or ""
                        if nss_key and nss_key not in seen_nss:
                            seen_nss.add(nss_key)
                            trabajadores.append(worker)
                            count_page += 1

        por_pagina.append(count_page)

    return trabajadores, por_pagina


def _extract_int_from_line(line: str) -> int | None:
    s = line.strip().replace(",", "").replace(" ", "")
    m = _INT_LINE_RE.match(s)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"(\d{1,6})", s)
    if m:
        return int(m.group(1))
    return None


def _extract_total_cotizantes(
    all_lines: list[str],
    *,
    total_pages: int,
    nss_unique_count: int,
) -> int | None:
    label_indices = [i for i, ln in enumerate(all_lines) if _LABEL_TOTAL_RE.search(ln)]
    if not label_indices:
        return None

    label_idx = label_indices[-1]
    before: list[int] = []
    after: list[int] = []

    for j in range(max(0, label_idx - 8), label_idx):
        val = _extract_int_from_line(all_lines[j])
        if val is not None and val > 0:
            before.append(val)

    for j in range(label_idx + 1, min(len(all_lines), label_idx + 6)):
        val = _extract_int_from_line(all_lines[j])
        if val is not None and val > 0:
            after.append(val)

    after_first = after[0] if after else None
    before_last = before[-1] if before else None

    if before_last is not None:
        if after_first == total_pages:
            return before_last
        if before_last != total_pages:
            if after_first is None or after_first == total_pages:
                return before_last
            if before_last >= (after_first or 0):
                return before_last
        if nss_unique_count > 0 and before_last == nss_unique_count:
            return before_last

    if before_last is not None and nss_unique_count > 0:
        if abs(before_last - nss_unique_count) <= 3:
            return before_last
        if after_first == total_pages:
            return before_last

    if nss_unique_count > 0:
        if after_first == total_pages or after_first == total_pages:
            return nss_unique_count
        if after_first and after_first != nss_unique_count and before_last is None:
            if nss_unique_count > total_pages:
                return nss_unique_count

    if before_last and before_last != total_pages:
        return before_last

    if nss_unique_count > total_pages:
        return nss_unique_count

    return None


def _extract_metadata(full_text: str, all_lines: list[str], total_pages: int, nss_count: int) -> dict[str, Any]:
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

    total = _extract_total_cotizantes(all_lines, total_pages=total_pages, nss_unique_count=nss_count)
    if total is not None:
        meta["total_cotizantes"] = total
    return meta


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
    all_lines = _page_lines(full_text)
    result.es_sua = _detect_sua(full_text)
    if not result.es_sua:
        result.error = "El archivo no parece ser una Cédula SUA válida."
        return result

    trabajadores_raw, por_pagina = _parse_workers_from_pages(pages_text)
    nss_count = len({w.get("nss_normalizado") for w in trabajadores_raw if w.get("nss_normalizado")})

    result.metadatos = _extract_metadata(full_text, all_lines, result.paginas_procesadas, nss_count)
    result.total_cotizantes = result.metadatos.get("total_cotizantes")

    if result.total_cotizantes is None and nss_count > 0:
        result.total_cotizantes = nss_count
        result.metadatos["total_cotizantes"] = nss_count

    trabajadores = [enrich_sua_worker_fields(w) for w in trabajadores_raw]
    result.trabajadores = trabajadores
    result.registros_por_pagina = por_pagina
    result.trabajadores_extraidos = len(trabajadores)
    result.total_sua_activos_al_corte = sum(
        1 for w in trabajadores if sua_es_activo_al_corte(w.get("sua_movimiento_clave"))
    )
    result.total_sua_bajas_periodo = sum(1 for w in trabajadores if sua_tiene_baja(w.get("sua_movimiento_clave")))
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
    activos = sum(1 for w in result.trabajadores if w.get("sua_es_activo_al_corte"))
    bajas = sum(1 for w in result.trabajadores if w.get("sua_tiene_baja"))
    return {
        "total_cotizantes": total,
        "trabajadores_extraidos": extraidos,
        "total_sua_activos_al_corte": activos,
        "total_sua_bajas_periodo": bajas,
        "diferencia": extraidos - total,
        "paginas_procesadas": result.paginas_procesadas,
        "registros_por_pagina": result.registros_por_pagina,
        "ultimos_registros": result.ultimos_registros,
        "mensaje": result.error
        or "La lectura del SUA no coincide con el Total de Cotizantes. No se generó reporte para evitar resultados incorrectos.",
    }
