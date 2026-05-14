from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from pypdf import PdfReader

_TIPO_AVISO_RE = re.compile(r"\b(Retenc\S*|Modific\S*|Suspens\S*)\b", re.IGNORECASE)
_DATE_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")


@dataclass
class InfonavitParsedResult:
    metadata: dict[str, Any]
    rows: list[dict[str, Any]]
    warnings: list[str]
    errors: list[str]


def normalize_name(value: str) -> str:
    s = " ".join(str(value or "").replace("\u00a0", " ").upper().split()).strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return " ".join(s.split()).strip()


def _clean_page_text(page_text: str) -> str:
    text = page_text or ""
    text = text.replace("\r", "\n")
    text = re.sub(r"AVISO SOBRE EL USO DE LA INFORMACION CONTENIDA.+", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"AVISO SOBRE EL USO DE LA INFORMACIÓN CONTENIDA.+", "", text, flags=re.IGNORECASE | re.DOTALL)
    return text


def _extract_metadata(full_text: str) -> dict[str, Any]:
    fecha_corte_raw = ""
    registro_patronal = ""
    total_avisos_reportado = 0

    m = re.search(r"Fecha y hora de corte de la informacion:\s*([0-9.: ]+)", full_text, flags=re.IGNORECASE)
    if not m:
        m = re.search(r"Fecha y hora de corte de la información:\s*([0-9.: ]+)", full_text, flags=re.IGNORECASE)
    if m:
        fecha_corte_raw = (m.group(1) or "").strip()

    m = re.search(r"Numero de Registro Patronal:\s*([A-Z0-9]+)", full_text, flags=re.IGNORECASE)
    if not m:
        m = re.search(r"Número de Registro Patronal:\s*([A-Z0-9]+)", full_text, flags=re.IGNORECASE)
    if m:
        registro_patronal = (m.group(1) or "").strip()

    m = re.search(r"Total de avisos:\s*(\d+)", full_text, flags=re.IGNORECASE)
    if m:
        total_avisos_reportado = int(m.group(1))

    return {
        "fecha_corte": fecha_corte_raw,
        "registro_patronal": registro_patronal,
        "total_avisos_reportado": total_avisos_reportado,
    }


def _iter_row_chunks(full_text: str) -> list[tuple[dict[str, str], str]]:
    row_start = re.compile(
        r"(?P<nss>\d{11})\s+(?P<credito>\d{10})\s+(?P<folio>[A-Z]\d{11})(?:\s+(?P<folio_suffix>\d{2}))?",
        flags=re.MULTILINE,
    )
    matches = list(row_start.finditer(full_text))
    chunks: list[tuple[dict[str, str], str]] = []
    for idx, m in enumerate(matches):
        start = m.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(full_text)
        payload = {
            "nss": (m.group("nss") or "").strip(),
            "numero_credito": (m.group("credito") or "").strip(),
            "folio_base": (m.group("folio") or "").strip(),
            "folio_suffix": (m.group("folio_suffix") or "").strip(),
        }
        chunks.append((payload, full_text[start:end]))
    return chunks


def _classify_estatus(tipo_aviso: str) -> str:
    t = normalize_name(tipo_aviso)
    if "RETENC" in t:
        return "ACTIVO"
    if "MODIFIC" in t:
        return "ACTIVO_MODIFICADO"
    if "SUSPEN" in t:
        return "SUSPENDIDO"
    return "REVISION"


def _parse_chunk(base: dict[str, str], chunk: str) -> dict[str, Any]:
    raw = " ".join((chunk or "").split())
    folio = base["folio_base"] + (base["folio_suffix"] or "")
    prefix = f"{base['nss']} {base['numero_credito']} {base['folio_base']}"
    if base["folio_suffix"]:
        prefix += f" {base['folio_suffix']}"
    rest = raw[len(prefix):].strip() if raw.startswith(prefix) else raw

    tipo_match = _TIPO_AVISO_RE.search(rest)
    tipo_aviso = ""
    nombre_trabajador = rest
    after_tipo = ""
    if tipo_match:
        tipo_aviso = (tipo_match.group(1) or "").strip()
        nombre_trabajador = rest[:tipo_match.start()].strip()
        after_tipo = rest[tipo_match.end():].strip()

    fecha_match = _DATE_RE.search(after_tipo)
    motivo = after_tipo
    fecha_aviso = ""
    after_fecha = ""
    if fecha_match:
        fecha_aviso = fecha_match.group(1)
        motivo = after_tipo[:fecha_match.start()].strip()
        after_fecha = after_tipo[fecha_match.end():].strip()

    descuento_raw = ""
    tipo_descuento = ""
    m_pesos = re.match(r"^\$\s*([0-9,]+(?:\.[0-9]{1,4})?)\s+(.*)$", after_fecha)
    m_vsm = re.match(r"^([0-9]+(?:\.[0-9]{1,6})?)\s*VSM\s+(.*)$", after_fecha, flags=re.IGNORECASE)
    if m_pesos:
        descuento_raw = f"$ {m_pesos.group(1)}"
        tipo_descuento = (m_pesos.group(2) or "").strip()
    elif m_vsm:
        descuento_raw = f"{m_vsm.group(1)} VSM"
        tipo_descuento = (m_vsm.group(2) or "").strip()
    else:
        tipo_descuento = after_fecha

    warnings: list[str] = []
    if len(base["nss"]) != 11:
        warnings.append("NSS invalido o incompleto.")
    if not tipo_aviso:
        warnings.append("Tipo de aviso no reconocido.")
    if not fecha_aviso:
        warnings.append("Fecha de aviso no detectada.")

    estatus_infonavit = _classify_estatus(tipo_aviso)
    if estatus_infonavit == "REVISION":
        warnings.append("No se pudo clasificar el aviso en retencion/modificacion/suspension.")

    return {
        "nss": base["nss"],
        "numero_credito": base["numero_credito"],
        "folio_aviso": folio,
        "nombre_trabajador": nombre_trabajador,
        "nombre_normalizado": normalize_name(nombre_trabajador),
        "tipo_aviso": tipo_aviso,
        "motivo_aviso": motivo,
        "fecha_aviso": fecha_aviso,
        "descuento_raw": descuento_raw,
        "tipo_descuento": tipo_descuento,
        "estatus_infonavit": estatus_infonavit,
        "warnings": warnings,
    }


def parse_infonavit_pdf(file_bytes: bytes, *, filename: str = "infonavit.pdf") -> InfonavitParsedResult:
    warnings: list[str] = []
    errors: list[str] = []
    if not file_bytes:
        raise ValueError("Archivo vacio.")
    if not filename.lower().endswith(".pdf"):
        raise ValueError("Solo se permiten archivos PDF para INFONAVIT.")

    reader = PdfReader(BytesIO(file_bytes))
    if not reader.pages:
        raise ValueError("No se encontraron paginas en el PDF.")

    page_texts: list[str] = []
    for page in reader.pages:
        page_texts.append(_clean_page_text(page.extract_text() or ""))

    merged_text = "\n".join(page_texts)
    metadata = _extract_metadata(merged_text)
    chunks = _iter_row_chunks(merged_text)
    rows = [_parse_chunk(base, chunk) for base, chunk in chunks]

    if not rows:
        errors.append("No se detectaron avisos en el PDF.")
    total_reportado = int(metadata.get("total_avisos_reportado") or 0)
    if total_reportado and total_reportado != len(rows):
        warnings.append(
            f"Total de avisos reportado ({total_reportado}) no coincide con filas parseadas ({len(rows)})."
        )

    return InfonavitParsedResult(
        metadata=metadata,
        rows=rows,
        warnings=warnings,
        errors=errors,
    )
