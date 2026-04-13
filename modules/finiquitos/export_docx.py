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
    rows_list = meta.get("percepciones") or []
    by_slot = {str(r.get("slot") or ""): r for r in rows_list if isinstance(r, dict)}
    slot_to_row_idx = (("t1", 1), ("t2", 2), ("t3", 3), ("t5", 4), ("t6", 5))
    for slot, ri in slot_to_row_idx:
        r = by_slot.get(slot) or {}
        num = str(r.get("num") or "")
        nom = str(r.get("nom") or "")
        mapping[f"{{r{ri}n}}"] = num
        mapping[f"{{r{ri}c}}"] = nom
        mapping[f"{{p{ri}_num}}"] = num
        mapping[f"{{p{ri}_nom}}"] = nom
        m = Decimal(str(r.get("monto") or 0))
        if slot in ("t1", "t2"):
            mapping["{" + slot + "}"] = _importe_sueldo_o_septimo_docx(m)
        else:
            mapping["{" + slot + "}"] = _importe_percepcion_docx(m)

    pex = meta.get("percepciones_extra") or []
    mapping["{p1}"] = mapping["{p_nuevo}"] = mapping["{t12}"] = ""
    if isinstance(pex, list) and len(pex) > 0 and isinstance(pex[0], dict):
        e0 = pex[0]
        mapping["{p1}"] = str(e0.get("num") or "").strip()
        mapping["{p_nuevo}"] = str(e0.get("nom") or "").strip()
        t12m = Decimal(str(e0.get("monto") or 0))
        mapping["{t12}"] = _importe_percepcion_docx(t12m)

    tot = calc["totales"]
    pdf = calc["pdf_filas"]
    ajuste = Decimal(str(tot.get("ajuste_neto") or 0))
    n7 = by_slot.get("n7") or {}
    np = by_slot.get("np") or {}

    extras: list[tuple[str, str, str]] = []
    for d in sorted(meta.get("deducciones_extra") or [], key=lambda x: int(str(x.get("num") or "999"))):
        if not isinstance(d, dict):
            continue
        nn = str(d.get("num") or "").strip()
        cn = str(d.get("nom") or "").strip()
        tv = Decimal(str(d.get("monto") or 0))
        if not nn and not cn and tv == 0:
            continue
        extras.append((nn, cn, format_importe(tv)))

    nd, cd, t11d = "", "", ""
    if ajuste > 0:
        nd, cd, t11d = "99", "Ajuste al neto", format_importe(abs(ajuste))

    ded_docx = empaquetar_filas_deduccion_para_docx_extras(pdf, extras, nd=nd, cd=cd, t11d=t11d)
    for k, v in ded_docx.items():
        if k in ("t8", "t9", "t10", "t11d"):
            mapping["{" + k + "}"] = _as_positive_amount_str(v)
        else:
            mapping["{" + k + "}"] = v

    if ajuste < 0:
        mapping["{np}"] = "99"
        mapping["{cp}"] = "Ajuste al neto"
        mapping["{t11p}"] = _as_positive_amount_str(format_importe(abs(ajuste)))
    else:
        mapping["{np}"] = str(np.get("num") or "")
        mapping["{cp}"] = str(np.get("nom") or "")
        tpm = Decimal(str(np.get("monto") or 0))
        mapping["{t11p}"] = _as_positive_amount_str(format_importe(tpm)) if tpm > 0 else ""

    mapping["{n7}"] = str(n7.get("num") or "")
    mapping["{c_pant}"] = str(n7.get("nom") or "")
    t7m = Decimal(str(n7.get("monto") or 0))
    mapping["{t7}"] = _importe_percepcion_docx(t7m)


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
    if ajuste < 0:
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

