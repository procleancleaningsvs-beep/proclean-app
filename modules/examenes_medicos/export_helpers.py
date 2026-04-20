from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from modules.examenes_medicos.validation import _norm, parse_date_iso


def app_mx_today() -> date:
    return datetime.now(ZoneInfo("America/Mexico_City")).date()


def default_yesterday_iso_mx() -> str:
    return (app_mx_today() - timedelta(days=1)).isoformat()


def safe_file_stem(*parts: str, fallback: str = "documento") -> str:
    raw = " ".join(p.strip() for p in parts if p and str(p).strip())
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw).strip(" ._") or fallback
    return s[:100]


def build_paciente_orina(nombres: str, apellidos: str) -> str:
    return " ".join(_norm(f"{nombres} {apellidos}").split())


def build_paciente_sangre(nombres: str, apellidos: str) -> str:
    """Formato tipo laboratorio: APELLIDO1 APELLIDO2,NOMBRES en mayúsculas."""
    n = " ".join(_norm(nombres).split()).upper()
    ap_chunks = _norm(apellidos).split()
    if len(ap_chunks) >= 2:
        ap1 = ap_chunks[0].upper()
        ap2 = " ".join(ap_chunks[1:]).upper()
        body = f"{ap1} {ap2},{n}"
    elif ap_chunks:
        body = f"{ap_chunks[0].upper()},{n}"
    else:
        body = n
    return body


def _mapping_val(d: dict[str, Any], key: str, default: str = "") -> str:
    v = d.get(key)
    if v is None:
        return default
    return str(v).strip()


def build_orina_mapping(data: dict[str, Any]) -> dict[str, str]:
    n = _mapping_val(data, "nombres")
    a = _mapping_val(data, "apellidos")
    pac = build_paciente_orina(n, a)
    fe = _mapping_val(data, "fecha_estudio")
    if fe and len(fe) == 10 and fe[4] == "-":
        fe = fecha_iso_a_dd_mm_yyyy(fe)
    return {
        "{edad}": _mapping_val(data, "edad"),
        "{sexo}": _mapping_val(data, "sexo"),
        "{folio}": _mapping_val(data, "folio"),
        "{paciente_nombre_completo}": pac,
        "{fecha_estudio}": fe,
        "{aspecto}": _mapping_val(data, "aspecto"),
        "{color}": _mapping_val(data, "color"),
        "{densidad}": _mapping_val(data, "densidad"),
        "{ph_orina}": _mapping_val(data, "ph_orina"),
        "{eritrocitos}": _mapping_val(data, "eritrocitos"),
        "{leucocitos}": _mapping_val(data, "leucocitos"),
    }


def build_sangre_mapping(data: dict[str, Any]) -> dict[str, str]:
    """`data` debe traer fechas en ISO (AAAA-MM-DD) y horas; se formatean para el DOCX."""
    data = dict(data)
    for fk in ("fecha_nacimiento", "fecha_toma", "fecha_val"):
        if fk in data and _norm(data.get(fk)):
            data[fk] = fecha_iso_a_dd_mm_yyyy(str(data[fk]))
    if _norm(data.get("hora_toma")):
        data["hora_toma"] = normalizar_hora_hhmmss(str(data["hora_toma"]))
    if _norm(data.get("hora_val")):
        data["hora_val"] = normalizar_hora_hhmmss(str(data["hora_val"]))
    if _norm(data.get("codigo_barra")):
        data["codigo_barra"] = _norm(data["codigo_barra"]).upper()

    keys = [
        "leucocitos",
        "eritrocitos",
        "hemoglobina",
        "hematocrito",
        "VCM",
        "HCM",
        "conc_media_hb_corp",
        "AD_D.E.",
        "AD_C.V.",
        "plaquetas",
        "V_plaquetario_medio",
        "linfocitos_pct",
        "neutrofilos_pct",
        "monocitos_pct",
        "eosinofilos_pct",
        "basofilos_pct",
        "linfocitos_abs",
        "neutrofilos_abs",
        "monocitos_abs",
        "eosinofilos_abs",
        "basofilos_abs",
        "glucosa",
        "urea",
        "bun",
        "creatinina",
        "acido_urico",
        "colesterol_total",
        "trigliceridos",
        "edad",
        "sexo",
        "hora_val",
        "fecha_val",
        "hora_toma",
        "fecha_toma",
        "paciente_nombre_completo",
        "cliente_numero",
        "folio",
        "fecha_nacimiento",
        "codigo_barra",
    ]
    out: dict[str, str] = {}
    for k in keys:
        out[f"{{{k}}}"] = _mapping_val(data, k)
    return out


def fecha_iso_a_dd_mm_yyyy(iso: str) -> str:
    d, _err = parse_date_iso(iso)
    if d is None:
        return _norm(iso)
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def normalizar_hora_hhmmss(hora: str) -> str:
    s = _norm(hora)
    if not s:
        return ""
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(s, fmt).time()
            return t.strftime("%H:%M:%S")
        except ValueError:
            continue
    return s


def default_hora_val_sugerida(hora_toma: str) -> str:
    s = _norm(hora_toma)
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(s, fmt).time()
            dt = datetime.combine(date(2000, 1, 1), t) + timedelta(hours=4)
            return dt.strftime("%H:%M:%S")
        except ValueError:
            continue
    return "12:00:00"


def resolve_generated_artifact(generated_dir: Path, relpath: str | None) -> Path | None:
    if not relpath or not str(relpath).strip():
        return None
    base = generated_dir.resolve()
    target = (base / relpath).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None
    if not target.is_file():
        return None
    return target
