from __future__ import annotations

import re
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from modules.examenes_medicos.validation import _norm, edad_desde_fecha_nacimiento, parse_date_iso


_SANGRE_DECIMALS: dict[str, int] = {
    # Biometria hematica
    "leucocitos": 2,
    "eritrocitos": 2,
    "hemoglobina": 2,
    "hematocrito": 2,
    "VCM": 2,
    "HCM": 2,
    "conc_media_hb_corp": 1,
    "AD_D.E.": 1,
    "AD_C.V.": 1,
    "plaquetas": 0,
    "V_plaquetario_medio": 2,
    "linfocitos_pct": 1,
    # Diferenciales
    "neutrofilos_pct": 1,
    "monocitos_pct": 1,
    "eosinofilos_pct": 1,
    "basofilos_pct": 1,
    "linfocitos_abs": 2,
    "neutrofilos_abs": 2,
    "monocitos_abs": 2,
    "eosinofilos_abs": 2,
    "basofilos_abs": 2,
    # Quimica clinica
    "glucosa": 1,
    "urea": 1,
    "bun": 1,
    "creatinina": 2,
    "acido_urico": 1,
    "colesterol_total": 1,
    "trigliceridos": 1,
}


def _normalize_sangre_sexo_for_export(value: Any) -> str:
    sx = _norm(value).casefold()
    if sx in {"hombre", "masculino", "varon", "varón"}:
        return "Masculino"
    if sx in {"mujer", "femenino"}:
        return "Femenino"
    return _norm(value)


def _format_sangre_value_for_export(key: str, value: Any) -> Any:
    if key == "sexo":
        return _normalize_sangre_sexo_for_export(value)
    nd = _SANGRE_DECIMALS.get(key)
    if nd is None:
        return value
    s = _norm(value).replace(",", ".")
    if not s:
        return value
    try:
        n = float(s)
    except ValueError:
        return value
    if nd == 0:
        return str(int(round(n)))
    return f"{n:.{nd}f}"


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
    """Formato tipo laboratorio: APELLIDOS NOMBRES en mayúsculas (sin coma)."""
    n = " ".join(_norm(nombres).split()).upper()
    ap_chunks = _norm(apellidos).split()
    ap = " ".join(ap_chunks).upper()
    return " ".join(x for x in (ap, n) if x)


def _mapping_val(d: dict[str, Any], key: str, default: str = "") -> str:
    v = d.get(key)
    if v is None:
        return default
    return str(v).strip()


def sexo_para_orina(sexo: str) -> str:
    """Plantilla de orina usa Masculino / Femenino / Otro."""
    return {"Mujer": "Femenino", "Hombre": "Masculino", "Otro": "Otro"}.get(
        (sexo or "").strip(), (sexo or "").strip() or "Otro"
    )


def _normalize_orina_count_cell(value: Any) -> str:
    s = _norm(value)
    if not s:
        return s
    low = s.casefold()
    if low in {"negativo", "escasas"}:
        return s.upper()
    if re.fullmatch(r"\d+", s):
        return f"{s}/C"
    if re.fullmatch(r"\d+\s*-\s*\d+", s):
        a, b = [p.strip() for p in s.split("-", 1)]
        return f"{a}-{b}/C"
    return s.upper()


def _orina_pick_aspecto(value: Any) -> str:
    v = _norm(value).upper()
    if not v:
        return "TRANSPARENTE"
    if "TURBIO" in v:
        return "TURBIO"
    if "TRANSP" in v or "LIMP" in v:
        return "TRANSPARENTE"
    return "TRANSPARENTE"


def _orina_pick_color(value: Any) -> str:
    v = _norm(value).upper()
    if not v:
        return "CLARO"
    if "AMBAR" in v or "ÁMBAR" in v:
        return "AMBAR"
    if "CLAR" in v or "AMARIL" in v:
        return "CLARO"
    return "CLARO"


def _orina_float_range(value: Any, lo: float, hi: float, nd: int, default: float) -> str:
    s = _norm(value).replace(",", ".")
    try:
        n = float(s)
    except ValueError:
        n = default
    if n < lo:
        n = lo
    if n > hi:
        n = hi
    return f"{n:.{nd}f}"


def _orina_pick_eritrocitos(value: Any) -> str:
    s = _normalize_orina_count_cell(value)
    if not s:
        return "0/C"
    m = re.fullmatch(r"(\d+)(?:-(\d+))?/C", s)
    if not m:
        return "0/C"
    a = int(m.group(1))
    b = int(m.group(2) or a)
    if a < 0:
        a = 0
    if b > 3:
        b = 3
    if a > b:
        a = b
    return f"{a}/C" if a == b else f"{a}-{b}/C"


def _orina_pick_escasas_o_negativo(value: Any) -> str:
    v = _norm(value).upper()
    if "ESCAS" in v:
        return "ESCASAS"
    if "NEGAT" in v:
        return "NEGATIVO"
    return "ESCASAS"


def build_orina_mapping(data: dict[str, Any]) -> dict[str, str]:
    n = _mapping_val(data, "nombres")
    a = _mapping_val(data, "apellidos")
    pac = build_paciente_orina(n, a)
    fe = _mapping_val(data, "fecha_estudio")
    if fe and len(fe) == 10 and fe[4] == "-":
        fe = fecha_iso_a_larga_es(fe)
    edad = _mapping_val(data, "edad")
    if edad and not edad.lower().endswith("años"):
        edad = f"{edad} años"
    # Plantilla en mayúsculas (MASCULINO / FEMENINO / OTRO) según referencia PDF.
    sexo_raw = _mapping_val(data, "sexo")
    sexo = sexo_raw.upper() if sexo_raw else ""
    aspecto_res = _orina_pick_aspecto(data.get("aspecto"))
    color_res = _orina_pick_color(data.get("color"))
    densidad_res = _orina_float_range(data.get("densidad"), 1.002, 1.030, 3, 1.018)
    ph_res = _orina_float_range(data.get("ph_orina"), 4.5, 8.0, 1, 6.3)
    erit = _orina_pick_eritrocitos(data.get("eritrocitos"))
    leuc = _normalize_orina_count_cell(data.get("leucocitos")) or "0/C"
    leuc_fijo = "5/C"
    cel_epit_res = _orina_pick_escasas_o_negativo(data.get("cel_epit"))
    cilindros_res = _orina_pick_escasas_o_negativo(data.get("cilindros"))
    cristales_res = _orina_pick_escasas_o_negativo(data.get("cristales"))
    out = {
        # Token heredado del DOCX original (Word); se limpia para no imprimirse literal.
        "{28A0092B-C50C-407E-A947-70E740481C1C}": "",
        "{edad}": edad,
        "{sexo}": sexo,
        "{folio}": _mapping_val(data, "folio"),
        "{paciente_nombre_completo}": pac,
        "{fecha_estudio}": fe,
        "{p_nombre_completo}": pac.upper(),
        "{aspecto}": aspecto_res,
        "{color}": color_res,
        "{densidad}": densidad_res,
        "{ph_orina}": ph_res,
        "{eritrocitos}": erit,
        "{leucocitos}": leuc,
        "{aspecto_resultado}": aspecto_res,
        "{color_resultado}": color_res,
        "{densidad_resultado}": densidad_res,
        "{ph_orina_resultado}": ph_res,
        "{eritrocitos_resultado}": erit,
        "{cel_epit_resultado}": cel_epit_res,
        "{cilindros_resultado}": cilindros_res,
        "{cristales_resultado}": cristales_res,
    }
    rows = {
        "aspecto": (aspecto_res, "CLARO"),
        "color": (color_res, ""),
        "densidad": (densidad_res, "1.002 a 1.030"),
        "ph_orina": (ph_res, "ACIDO (4.5-8.0)"),
        "proteinas": ("NEGATIVO", "NEGATIVO"),
        "glucosa": ("NEGATIVO", "NEGATIVO"),
        "cetonas": ("NEGATIVO", "NEGATIVO"),
        "bilirrubina": ("NEGATIVO", "NEGATIVO"),
        "hemoglobina": ("NEGATIVO", "NEGATIVO"),
        "nitritos": ("NEGATIVO", "NEGATIVO"),
        "urobilinogeno": ("NEGATIVO", "NEGATIVO"),
        "eritrocitos": (erit, "0-3/C"),
        "leucocitos": (leuc_fijo, "5/C"),
        "cel_epiteliales": (cel_epit_res, ""),
        "cilindros": (cilindros_res, ""),
        "cristales": (cristales_res, ""),
        "bacterias": ("NEGATIVO", "NEGATIVO"),
        "filamento_mucoso": ("NEGATIVO", "NEGATIVO"),
        "levaduras": ("NEGATIVO", "NEGATIVO"),
    }
    for key, (resultado, referencia) in rows.items():
        out[f"{{{key}_resultado}}"] = resultado
        out[f"{{{key}_referencia}}"] = referencia
    return out


def build_sangre_mapping(data: dict[str, Any]) -> dict[str, str]:
    """`data` debe traer fechas en ISO (AAAA-MM-DD) y horas; se formatean para el DOCX."""
    data = dict(data)
    for k, nd in _SANGRE_DECIMALS.items():
        if k in data:
            data[k] = _format_sangre_value_for_export(k, data[k])
    if "sexo" in data:
        data["sexo"] = _format_sangre_value_for_export("sexo", data["sexo"])
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


def build_orina_data_for_mapping(master: dict[str, Any], clinical_orina: dict[str, str]) -> dict[str, Any]:
    """Fusiona formulario maestro + bundle clínico de orina.

    Los datos del paciente y encabezado vienen siempre del maestro; el bundle clínico
    solo aporta parámetros de laboratorio (nunca debe pisar sexo, edad, folio, etc.).
    """
    d: dict[str, Any] = dict(clinical_orina)
    d["nombres"] = master.get("nombres")
    d["apellidos"] = master.get("apellidos")
    edad = master.get("edad")
    if not _norm(edad):
        fn, _err = parse_date_iso(master.get("fecha_nacimiento"))
        if fn:
            edad = str(edad_desde_fecha_nacimiento(fn, app_mx_today()))
    if _norm(edad):
        m = re.search(r"\d+", str(edad))
        edad = m.group(0) if m else str(edad)
    d["edad"] = edad
    d["sexo"] = sexo_para_orina(str(master.get("sexo") or ""))
    folio = _norm(master.get("folio_orina"))
    if not folio:
        folio = f"{random.randint(0, 999999):06d}"
    if re.fullmatch(r"\d{1,6}", folio):
        folio = folio.zfill(6)
    d["folio"] = folio
    d["fecha_estudio"] = master.get("fecha_estudio") or default_yesterday_iso_mx()
    return d


def build_sangre_data_for_mapping(master: dict[str, Any], clinical_sangre: dict[str, str]) -> dict[str, Any]:
    """Fusiona formulario maestro + bundle clínico de sangre para `build_sangre_mapping`."""
    base: dict[str, Any] = dict(clinical_sangre)
    for k in (
        "nombres",
        "apellidos",
        "fecha_nacimiento",
        "fecha_toma",
        "fecha_val",
        "hora_toma",
        "hora_val",
        "cliente_numero",
    ):
        if k in master and master[k] is not None:
            base[k] = master[k]
    base["folio"] = str(master.get("folio_sangre") or "").strip()
    cb = str(master.get("codigo_barra") or "").strip()
    if cb:
        base["codigo_barra"] = cb.upper()
    sx = _norm(master.get("sexo"))
    if sx:
        base["sexo"] = _normalize_sangre_sexo_for_export(sx)
    fnac, _ = parse_date_iso(master.get("fecha_nacimiento"))
    if fnac is not None:
        base["edad"] = str(edad_desde_fecha_nacimiento(fnac, app_mx_today()))
    base["paciente_nombre_completo"] = build_paciente_sangre(
        str(master.get("nombres") or ""),
        str(master.get("apellidos") or ""),
    )
    return base


def fecha_iso_a_dd_mm_yyyy(iso: str) -> str:
    d, _err = parse_date_iso(iso)
    if d is None:
        return _norm(iso)
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def fecha_iso_a_larga_es(iso: str) -> str:
    d, _err = parse_date_iso(iso)
    if d is None:
        return _norm(iso)
    meses = {
        1: "Enero",
        2: "Febrero",
        3: "Marzo",
        4: "Abril",
        5: "Mayo",
        6: "Junio",
        7: "Julio",
        8: "Agosto",
        9: "Septiembre",
        10: "Octubre",
        11: "Noviembre",
        12: "Diciembre",
    }
    return f"{d.day} de {meses[d.month]} del {d.year}"


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
