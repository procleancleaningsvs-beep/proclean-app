"""Arma el dict de placeholders del finiquito y exporta DOCX/PDF."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from modules.finiquitos.calc import format_importe
from modules.finiquitos.docx_placeholders import replace_placeholders_in_docx_bytes
from modules.finiquitos.finiquito_template_patch import patch_finiquito_docx_template_bytes
from modules.finiquitos.fecha_es import fecha_emision_larga
from modules.finiquitos.fecha_limite_pago import fecha_limite_pago_finiquito_larga
from modules.finiquitos.nombre_archivo_finiquito import normalizar_nombre_empleado_documento
from modules.finiquitos.numero_letra import importe_mxn_a_letra
from modules.vitroflex_docs.libreoffice_pdf import docx_bytes_to_pdf_bytes


def _as_positive_amount_str(s: str) -> str:
    """Convierte monto formateado a valor absoluto formateado (visual deducciones)."""
    raw = (s or "").replace(",", "").strip()
    if not raw:
        return ""
    return format_importe(abs(Decimal(raw)))


def _fila_deduccion_docx_visible(num: str, concepto: str, importe_fmt: str) -> bool:
    return bool((num or "").strip() or (concepto or "").strip() or (importe_fmt or "").strip())


def _importe_sueldo_o_septimo_docx(val: Any) -> str:
    """Sueldo y séptimo día siempre muestran importe, incluso 0.00."""
    try:
        d = Decimal(str(val))
    except Exception:
        d = Decimal("0")
    return format_importe(d)


def _importe_percepcion_docx(val: Any) -> str:
    """Importe en columna de percepciones (excepto sueldo/séptimo): vacío si el monto es cero."""
    try:
        d = Decimal(str(val))
    except Exception:
        return ""
    if d == 0:
        return ""
    return format_importe(d)


def empaquetar_filas_deduccion_para_docx(
    pdf: dict[str, Any],
    *,
    nd: str,
    cd: str,
    t11d: str,
) -> dict[str, str]:
    """
    Una sola cola de deducciones activas (41 / 43 / 45 y, al final, 99 si aplica),
    asignada en orden a los cuatro slots del template: n8→n9→n10→nd.
    Los slots que sobren quedan vacíos para no dejar huecos entre activos.
    """
    orden: list[tuple[str, str, str]] = []
    if _fila_deduccion_docx_visible(pdf.get("n8", ""), pdf.get("c_isa", ""), pdf.get("t8", "")):
        orden.append((pdf["n8"], pdf["c_isa"], pdf["t8"]))
    if _fila_deduccion_docx_visible(pdf.get("n9", ""), pdf.get("c_i174", ""), pdf.get("t9", "")):
        orden.append((pdf["n9"], pdf["c_i174"], pdf["t9"]))
    if _fila_deduccion_docx_visible(pdf.get("n10", ""), pdf.get("c_imes", ""), pdf.get("t10", "")):
        orden.append((pdf["n10"], pdf["c_imes"], pdf["t10"]))
    if _fila_deduccion_docx_visible(nd, cd, t11d):
        orden.append((nd, cd, t11d))

    slots: tuple[tuple[str, str, str], ...] = (
        ("n8", "c_isa", "t8"),
        ("n9", "c_i174", "t9"),
        ("n10", "c_imes", "t10"),
        ("nd", "cd", "t11d"),
    )
    out: dict[str, str] = {k: "" for trio in slots for k in trio}
    for i, trio in enumerate(slots):
        if i < len(orden):
            num, conc, imp = orden[i]
            nk, ck, tk = trio
            out[nk] = num
            out[ck] = conc
            out[tk] = imp
    return out


def empaquetar_filas_percepcion_para_docx(
    n7: str,
    c_pant: str,
    t7: str,
    np: str,
    cp: str,
    t11p: str,
) -> dict[str, str]:
    """
    Cola corta de percepciones con placeholders propios (prima + ajuste al neto):
    los activos suben a {n7}/{np} consecutivos y el resto se vacía.

    Sueldo y séptimo siempre llevan 0.00 si aplica; el resto de importes de filas fijas
    usa _importe_percepcion_docx (vacío en cero).
    """
    orden: list[tuple[str, str, str]] = []
    if _fila_deduccion_docx_visible(n7, c_pant, t7):
        orden.append((n7, c_pant, t7))
    if _fila_deduccion_docx_visible(np, cp, t11p):
        orden.append((np, cp, t11p))

    slots_meta: tuple[tuple[str, str, str], ...] = (
        ("n7", "c_pant", "t7"),
        ("np", "cp", "t11p"),
    )
    out: dict[str, str] = {
        "n7": "",
        "c_pant": "",
        "t7": "",
        "np": "",
        "cp": "",
        "t11p": "",
    }
    for i, (nk, ck, tk) in enumerate(slots_meta):
        if i < len(orden):
            num, conc, imp = orden[i]
            out[nk] = num
            out[ck] = conc
            out[tk] = imp
    return out


def empaquetar_filas_deduccion_para_docx_extras(
    pdf: dict[str, Any],
    extra_rows: list[tuple[str, str, str]],
    *,
    nd: str,
    cd: str,
    t11d: str,
) -> dict[str, str]:
    """
    Igual que empaquetar_filas_deduccion_para_docx pero inserta deducciones extra del usuario
    (num, concepto, importe ya formateado) antes del renglón 99 (nd/cd/t11d), y compacta a 4 slots.
    Si hay más de 4 renglones visibles, fusiona el excedente en el último slot como «Varios».
    """
    orden: list[tuple[str, str, str]] = []
    if _fila_deduccion_docx_visible(pdf.get("n8", ""), pdf.get("c_isa", ""), pdf.get("t8", "")):
        orden.append((pdf["n8"], pdf["c_isa"], pdf["t8"]))
    if _fila_deduccion_docx_visible(pdf.get("n9", ""), pdf.get("c_i174", ""), pdf.get("t9", "")):
        orden.append((pdf["n9"], pdf["c_i174"], pdf["t9"]))
    if _fila_deduccion_docx_visible(pdf.get("n10", ""), pdf.get("c_imes", ""), pdf.get("t10", "")):
        orden.append((pdf["n10"], pdf["c_imes"], pdf["t10"]))
    for num, conc, imp in extra_rows:
        if _fila_deduccion_docx_visible(num, conc, imp):
            orden.append((num, conc, imp))
    if _fila_deduccion_docx_visible(nd, cd, t11d):
        orden.append((nd, cd, t11d))

    slots: tuple[tuple[str, str, str], ...] = (
        ("n8", "c_isa", "t8"),
        ("n9", "c_i174", "t9"),
        ("n10", "c_imes", "t10"),
        ("nd", "cd", "t11d"),
    )
    out: dict[str, str] = {k: "" for trio in slots for k in trio}
    if len(orden) <= len(slots):
        for i, trio in enumerate(slots):
            if i < len(orden):
                num, conc, imp = orden[i]
                nk, ck, tk = trio
                out[nk] = num
                out[ck] = conc
                out[tk] = imp
        return out

    for i in range(3):
        num, conc, imp = orden[i]
        nk, ck, tk = slots[i]
        out[nk] = num
        out[ck] = conc
        out[tk] = imp
    rest = orden[3:]
    tot = Decimal("0")
    for _, _, imp in rest:
        try:
            tot += abs(Decimal(str(imp).replace(",", "")))
        except Exception:
            pass
    nk, ck, tk = slots[3]
    out[nk] = rest[0][0] if rest else ""
    out[ck] = "Varios"
    out[tk] = format_importe(tot) if tot > 0 else ""
    return out


# Capacidad plantilla v2 (edición libre): percepciones y deducciones compactas.
MAX_V2_PERC_LINES = 8
MAX_V2_DED_LINES = 6

# Orden de triples en DOCX (percepciones): 5 base + n7 + np + p1.
V2_PERC_PLACEHOLDER_TRIPLES: tuple[tuple[str, str, str], ...] = (
    ("{r1n}", "{r1c}", "{t1}"),
    ("{r2n}", "{r2c}", "{t2}"),
    ("{r3n}", "{r3c}", "{t3}"),
    ("{r4n}", "{r4c}", "{t5}"),
    ("{r5n}", "{r5c}", "{t6}"),
    ("{n7}", "{c_pant}", "{t7}"),
    ("{np}", "{cp}", "{t11p}"),
    ("{p1}", "{p_nuevo}", "{t12}"),
)

V2_DED_PLACEHOLDER_TRIPLES: tuple[tuple[str, str, str], ...] = (
    ("{n8}", "{c_isa}", "{t8}"),
    ("{n9}", "{c_i174}", "{t9}"),
    ("{n10}", "{c_imes}", "{t10}"),
    ("{nd}", "{cd}", "{t11d}"),
    ("{d1}", "{d_nuevo}", "{t13}"),
    ("{d2}", "{d_nuevo2}", "{t14}"),
)


def _v2_parse_num_sort_key(num: str) -> tuple[int, str]:
    t = (num or "").strip()
    if not t:
        return (10**9, "")
    try:
        return (int(t, 10), t)
    except ValueError:
        return (10**9, t)


def _v2_row_p_active(num: str, nom: str, m: Decimal) -> bool:
    return bool((num or "").strip() or (nom or "").strip() or m != 0)


def _v2_importe_percepcion_row(*, lab_key: str | None, slot: str, m: Decimal) -> str:
    lk = (lab_key or "").strip()
    sl = (slot or "").strip().lower()
    if lk in ("sueldo", "septimo_dia") or sl in ("t1", "t2"):
        return _importe_sueldo_o_septimo_docx(m)
    return _importe_percepcion_docx(m)


def _v2_collect_percepciones_sorted(calc: dict[str, Any]) -> list[dict[str, Any]]:
    meta = calc.get("edicion_libre_desglose_meta") or {}
    rows: list[dict[str, Any]] = []
    combined_src: list[Any] = []
    for key in ("percepciones", "percepciones_extra"):
        part = meta.get(key) or []
        if isinstance(part, list):
            combined_src.extend(part)
    for r in combined_src:
        if not isinstance(r, dict):
            continue
        num = str(r.get("num") or "").strip()
        nom = str(r.get("nom") or "").strip()
        m = Decimal(str(r.get("monto") or 0))
        if not _v2_row_p_active(num, nom, m):
            continue
        rows.append(
            {
                "num": num,
                "nom": nom,
                "monto": m,
                "slot": str(r.get("slot") or "").strip().lower(),
                "labKey": str(r.get("labKey") or "").strip() or None,
                "fiscal": str(r.get("fiscal") or "gravable"),
            }
        )
    tot = calc.get("totales") or {}
    ajuste = Decimal(str(tot.get("ajuste_neto") or 0))
    if ajuste < 0 and not any(str(x.get("num") or "").strip() == "99" for x in rows):
        rows.append(
            {
                "num": "99",
                "nom": "Ajuste al neto",
                "monto": abs(ajuste),
                "slot": "",
                "labKey": None,
                "fiscal": "gravable",
            }
        )
    rows.sort(key=lambda x: (_v2_parse_num_sort_key(str(x.get("num") or ""))[0], str(x.get("slot") or "")))
    return rows


def _v2_collect_deducciones_sorted(calc: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Triples (num, concepto, importe_formateado positivo) para empaquetar."""
    pdf = calc.get("pdf_filas") or {}
    tot = calc.get("totales") or {}
    meta = calc.get("edicion_libre_desglose_meta") or {}
    ajuste = Decimal(str(tot.get("ajuste_neto") or 0))
    orden: list[tuple[str, str, str, int]] = []

    def add_row(num: str, conc: str, imp_fmt: str) -> None:
        if not _fila_deduccion_docx_visible(num, conc, imp_fmt):
            return
        orden.append((num, conc, imp_fmt, _v2_parse_num_sort_key(num)[0]))

    if _fila_deduccion_docx_visible(pdf.get("n8", ""), pdf.get("c_isa", ""), pdf.get("t8", "")):
        add_row(str(pdf.get("n8", "")), str(pdf.get("c_isa", "")), str(pdf.get("t8", "")))
    if _fila_deduccion_docx_visible(pdf.get("n9", ""), pdf.get("c_i174", ""), pdf.get("t9", "")):
        add_row(str(pdf.get("n9", "")), str(pdf.get("c_i174", "")), str(pdf.get("t9", "")))
    if _fila_deduccion_docx_visible(pdf.get("n10", ""), pdf.get("c_imes", ""), pdf.get("t10", "")):
        add_row(str(pdf.get("n10", "")), str(pdf.get("c_imes", "")), str(pdf.get("t10", "")))
    if _fila_deduccion_docx_visible(pdf.get("n_sep", ""), pdf.get("c_sep", ""), pdf.get("t_sep", "")):
        add_row(str(pdf.get("n_sep", "")), str(pdf.get("c_sep", "")), str(pdf.get("t_sep", "")))

    for d in sorted(meta.get("deducciones_extra") or [], key=lambda x: _v2_parse_num_sort_key(str(x.get("num") or ""))[0]):
        if not isinstance(d, dict):
            continue
        nn = str(d.get("num") or "").strip()
        cn = str(d.get("nom") or "").strip()
        tv = Decimal(str(d.get("monto") or 0))
        if not _v2_row_p_active(nn, cn, tv):
            continue
        add_row(nn, cn, format_importe(tv))

    if ajuste > 0:
        add_row("99", "Ajuste al neto", format_importe(abs(ajuste)))

    orden.sort(key=lambda x: (x[3], x[0]))
    return [(a[0], a[1], a[2]) for a in orden]


def check_finiquito_v2_docx_capacity(calc: dict[str, Any]) -> str | None:
    """Tras apply_desglose_manual v2: valida líneas finales para DOCX/PDF."""
    meta = calc.get("edicion_libre_desglose_meta") or {}
    if meta.get("v") != 2:
        return None
    np = len(_v2_collect_percepciones_sorted(calc))
    nd = len(_v2_collect_deducciones_sorted(calc))
    if np > MAX_V2_PERC_LINES:
        return (
            f"El desglose compactado tiene {np} percepciones; la plantilla solo admite {MAX_V2_PERC_LINES}. "
            "Reduzca conceptos o combine filas."
        )
    if nd > MAX_V2_DED_LINES:
        return (
            f"El desglose compactado tiene {nd} deducciones visibles (ISR + adicionales + ajuste); "
            f"la plantilla admite {MAX_V2_DED_LINES}. Reduzca deducciones adicionales."
        )
    return None


def _v2_clear_all_template_slots(mapping: dict[str, str]) -> None:
    """Vacía todos los slots de desglose v2 antes de rellenar (evita literales {d1}…)."""
    for trio in V2_PERC_PLACEHOLDER_TRIPLES:
        for k in trio:
            mapping[k] = ""
    for trio in V2_DED_PLACEHOLDER_TRIPLES:
        for k in trio:
            mapping[k] = ""
    for i in range(1, 6):
        mapping[f"{{p{i}_num}}"] = ""
        mapping[f"{{p{i}_nom}}"] = ""
    for slot in ("t1", "t2", "t3", "t5", "t6"):
        mapping["{" + slot + "}"] = ""


def _mapping_desglose_labels_canonical_base() -> dict[str, str]:
    """
    Modo normal (edición libre apagada): mapeo fijo explícito slot → número/concepto.
    Nuevo formato DOCX: {r1n}{r1c}…{r5c}; compatibilidad con plantillas parcheadas {pN_num}/{pN_nom}.
    """
    r = {
        "{r1n}": "1",
        "{r1c}": "Sueldo",
        "{r2n}": "3",
        "{r2c}": "Séptimo día",
        "{r3n}": "19",
        "{r3c}": "Vacaciones a tiempo",
        "{r4n}": "22",
        "{r4c}": "Prima vacacional",
        "{r5n}": "24",
        "{r5c}": "Aguinaldo",
        "{p1}": "",
        "{p_nuevo}": "",
        "{t12}": "",
    }
    for i in range(1, 6):
        r[f"{{p{i}_num}}"] = r[f"{{r{i}n}}"]
        r[f"{{p{i}_nom}}"] = r[f"{{r{i}c}}"]
    return r


def _merge_desglose_v2_placeholders(mapping: dict[str, str], calc: dict[str, Any]) -> None:
    meta = calc.get("edicion_libre_desglose_meta") or {}
    if meta.get("v") != 2:
        return
    _v2_clear_all_template_slots(mapping)

    perc_rows = _v2_collect_percepciones_sorted(calc)
    for i, trio in enumerate(V2_PERC_PLACEHOLDER_TRIPLES):
        nk, ck, tk = trio
        if i < len(perc_rows):
            r = perc_rows[i]
            num = str(r.get("num") or "")
            nom = str(r.get("nom") or "")
            m = Decimal(str(r.get("monto") or 0))
            mapping[nk] = num
            mapping[ck] = nom
            imp = _v2_importe_percepcion_row(
                lab_key=r.get("labKey"),
                slot=str(r.get("slot") or ""),
                m=m,
            )
            mapping[tk] = imp
            if nk.startswith("{r") and nk.endswith("n}"):
                try:
                    ri = int(nk.replace("{r", "").replace("n}", ""))
                    mapping[f"{{p{ri}_num}}"] = num
                    mapping[f"{{p{ri}_nom}}"] = nom
                except ValueError:
                    pass
        else:
            nk, ck, tk = trio
            mapping[nk] = mapping[ck] = ""
            mapping[tk] = ""

    ded_triples = _v2_collect_deducciones_sorted(calc)
    for i, trio in enumerate(V2_DED_PLACEHOLDER_TRIPLES):
        nk, ck, tk = trio
        if i < len(ded_triples):
            num, conc, imp = ded_triples[i]
            mapping[nk] = num
            mapping[ck] = conc
            mapping[tk] = _as_positive_amount_str(imp)
        else:
            mapping[nk] = mapping[ck] = mapping[tk] = ""


def build_finiquito_placeholders(
    *,
    lugar_emision: str,
    estado_emision: str,
    fecha_emision: date,
    fecha_baja: date,
    empleado_nombre: str,
    calc: dict[str, Any],
    incluir_prima_antig: bool,
) -> dict[str, str]:
    lab = calc["laboral"]
    tot = calc["totales"]
    pdf = calc["pdf_filas"]
    neto = Decimal(str(tot["neto_final"]))
    ajuste = Decimal(str(tot["ajuste_neto"]))
    # Convención calc: ajuste = neto_prev - neto_final. >0 baja el neto → 99 en deducciones; <0 sube → percepciones.

    pa = Decimal(str(lab.get("prima_antiguedad_monto") or 0))
    if incluir_prima_antig and pa > 0:
        n7, c_pant, t7 = "29", "Prima de antigüedad", format_importe(pa)
    else:
        n7, c_pant, t7 = "", "", ""

    if ajuste > 0:
        nd, cd, t11d = "99", "Ajuste al neto", format_importe(abs(ajuste))
        np, cp, t11p = "", "", ""
    elif ajuste < 0:
        np, cp, t11p = "99", "Ajuste al neto", format_importe(abs(ajuste))
        nd, cd, t11d = "", "", ""
    else:
        np = cp = t11p = nd = cd = t11d = ""

    fecha_larga = fecha_emision_larga(fecha_emision)
    fecha_limite = fecha_limite_pago_finiquito_larga(fecha_emision)
    nombre_doc = normalizar_nombre_empleado_documento(empleado_nombre)
    t11_val = "" if ajuste == 0 else format_importe(ajuste)
    ded_docx = empaquetar_filas_deduccion_para_docx(pdf, nd=nd, cd=cd, t11d=t11d)
    perc_docx = empaquetar_filas_percepcion_para_docx(n7, c_pant, t7, np, cp, t11p)
    suma_p_num = Decimal(str(tot["total_percepciones"]))
    meta_dm = calc.get("edicion_libre_desglose_meta") or {}
    if ajuste < 0 and meta_dm.get("v") != 2:
        suma_p_num = (suma_p_num + abs(ajuste)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    mapping: dict[str, str] = {
        "{lugar_emision}": lugar_emision or "",
        "{estado_emision}": estado_emision or "",
        "{fecha_emision_larga}": fecha_larga,
        "{fecha_letra}": fecha_larga,
        "{fecha_baja_larga}": fecha_emision_larga(fecha_baja),
        "{fecha_limite_pago}": fecha_limite,
        "{empleado_nombre_completo}": nombre_doc,
        "{neto_p}": format_importe(neto),
        "{neto_pagar_letra}": importe_mxn_a_letra(neto),
        "{t1}": _importe_sueldo_o_septimo_docx(lab["sueldo"]),
        "{t2}": _importe_sueldo_o_septimo_docx(lab["septimo_dia"]),
        "{t3}": _importe_percepcion_docx(lab["vacaciones_a_tiempo"]),
        "{t5}": _importe_percepcion_docx(lab["prima_vacacional"]),
        "{t6}": _importe_percepcion_docx(lab["aguinaldo"]),
        "{n7}": perc_docx["n7"],
        "{c_pant}": perc_docx["c_pant"],
        "{t7}": perc_docx["t7"],
        "{n8}": ded_docx["n8"],
        "{c_isa}": ded_docx["c_isa"],
        # En formato final las deducciones se imprimen en positivo (valor absoluto).
        "{t8}": _as_positive_amount_str(ded_docx["t8"]),
        "{n9}": ded_docx["n9"],
        "{c_i174}": ded_docx["c_i174"],
        "{t9}": _as_positive_amount_str(ded_docx["t9"]),
        "{n10}": ded_docx["n10"],
        "{c_imes}": ded_docx["c_imes"],
        "{t10}": _as_positive_amount_str(ded_docx["t10"]),
        "{n_sep}": pdf.get("n_sep", ""),
        "{c_sep}": pdf.get("c_sep", ""),
        "{t_sep}": _as_positive_amount_str(pdf.get("t_sep", "")),
        "{t11}": t11_val,
        "{np}": perc_docx["np"],
        "{cp}": perc_docx["cp"],
        "{t11p}": _as_positive_amount_str(perc_docx["t11p"]),
        "{nd}": ded_docx["nd"],
        "{cd}": ded_docx["cd"],
        "{t11d}": _as_positive_amount_str(ded_docx["t11d"]),
        "{suma_p}": format_importe(suma_p_num),
        "{suma_d}": pdf["suma_d"],
    }
    mapping.update(_mapping_desglose_labels_canonical_base())
    _merge_desglose_v2_placeholders(mapping, calc)
    for _k in (
        "{d1}",
        "{d_nuevo}",
        "{t13}",
        "{d2}",
        "{d_nuevo2}",
        "{t14}",
    ):
        mapping.setdefault(_k, "")
    return mapping


def render_finiquito_docx(template_path: Path, mapping: dict[str, str]) -> bytes:
    """
    Lee la plantilla del path dado, aplica parches de layout (misma lógica en app y CLI)
    y sustituye placeholders.
    """
    raw = template_path.read_bytes()
    patched = patch_finiquito_docx_template_bytes(raw)
    return replace_placeholders_in_docx_bytes(patched, mapping)


def render_finiquito_pdf(docx_bytes: bytes, *, pdf_stem: str | None = None) -> bytes:
    return docx_bytes_to_pdf_bytes(docx_bytes, pdf_stem=pdf_stem)


def render_finiquito_final(
    template_path: Path,
    mapping: dict[str, str],
    *,
    pdf_stem: str | None = None,
) -> tuple[bytes, bytes]:
    """
    Pipeline único: plantilla → parches → DOCX con placeholders → PDF (LibreOffice).
    Usar desde Flask y desde scripts de prueba.
    """
    docx_b = render_finiquito_docx(template_path, mapping)
    pdf_b = render_finiquito_pdf(docx_b, pdf_stem=pdf_stem)
    return docx_b, pdf_b


def finiquito_docx_template_bundle_path() -> Path:
    """Plantilla empaquetada en el repo (referencia para scripts sin Flask)."""
    return Path(__file__).resolve().parents[2] / "docx_templates" / "FINIQUITO FORMATO.docx"

