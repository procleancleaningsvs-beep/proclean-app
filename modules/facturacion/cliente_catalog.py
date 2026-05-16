"""Catálogo: razón social → cliente; correo/dominio → cliente; días de crédito por cliente."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from modules.facturacion.config import CLIENTE_BLOQUE_EXCEL_NAMES


def normalize_razon_key(s: str | None) -> str:
    if not s:
        return ""
    t = str(s).strip().upper()
    t = re.sub(r"\s+", " ", t)
    return t


@dataclass
class CatalogMaps:
    razon_to_principal: dict[str, str]
    dias_credito: dict[str, int]
    correo_exact: dict[str, str]
    dominio_a_cliente: list[tuple[str, str]]
    known_principales_norm: frozenset[str]
    bloque_hints_norm: frozenset[str]


def load_catalog_maps(conn: sqlite3.Connection) -> CatalogMaps:
    razon_to: dict[str, str] = {}
    for r in conn.execute(
        "SELECT razon_social, cliente_principal FROM facturacion_razon_social_map"
    ).fetchall():
        k = normalize_razon_key(str(r["razon_social"]))
        if k:
            razon_to[k] = str(r["cliente_principal"]).strip()

    dias: dict[str, int] = {}
    for r in conn.execute(
        "SELECT cliente_principal, dias_credito FROM facturacion_cliente_credito"
    ).fetchall():
        ck = normalize_razon_key(str(r["cliente_principal"]))
        if ck:
            try:
                dias[ck] = max(0, int(r["dias_credito"]))
            except (TypeError, ValueError):
                dias[ck] = 0

    correo_exact: dict[str, str] = {}
    dominio_rows: list[tuple[str, str]] = []
    for r in conn.execute(
        """
        SELECT tipo, LOWER(TRIM(valor)) AS v, TRIM(cliente_principal) AS cp
        FROM facturacion_correo_cliente_map
        """
    ).fetchall():
        tipo = str(r["tipo"] or "").strip().upper()
        v = str(r["v"] or "").strip().lower()
        cp = str(r["cp"] or "").strip()
        if not v or not cp:
            continue
        if tipo == "EMAIL":
            correo_exact[v] = cp
        elif tipo == "DOMINIO":
            dominio_rows.append((v, cp))
    dominio_rows.sort(key=lambda x: len(x[0]), reverse=True)

    known: set[str] = set()
    for v in razon_to.values():
        known.add(normalize_razon_key(v))
    for k in dias.keys():
        known.add(normalize_razon_key(k))
    for cp in correo_exact.values():
        known.add(normalize_razon_key(cp))
    for _, cp in dominio_rows:
        known.add(normalize_razon_key(cp))

    hints = frozenset(normalize_razon_key(x) for x in CLIENTE_BLOQUE_EXCEL_NAMES if str(x).strip())

    return CatalogMaps(
        razon_to_principal=razon_to,
        dias_credito=dias,
        correo_exact=correo_exact,
        dominio_a_cliente=dominio_rows,
        known_principales_norm=frozenset(x for x in known if x),
        bloque_hints_norm=hints,
    )


def principal_desde_correo_catalogo(maps: CatalogMaps, email: str | None) -> str | None:
    em = (email or "").strip().lower()
    if "@" not in em:
        return None
    if em in maps.correo_exact:
        return maps.correo_exact[em].strip()
    dom = em.split("@", 1)[-1].strip().lower()
    if not dom:
        return None
    for d, cli in maps.dominio_a_cliente:
        if dom == d or dom.endswith("." + d):
            return cli.strip()
    return None


def resolve_cliente_principal(
    maps: CatalogMaps,
    *,
    razon_social_excel: str | None,
    cli_infer: str | None,
    fix_cliente_name_fn,
    por_clasificar: str,
    email_contacto: str | None = None,
    bloque_cliente_excel: str | None = None,
) -> tuple[str, str | None]:
    """
    Devuelve (cliente_principal, razon_social_guardar).
    Prioridad: razón social en catálogo → correo exacto/dominio en catálogo → bloque Excel
    (fila encabezado) → inferencia heurística (cli_infer) → texto de razón social como nombre.
    """
    rs = (razon_social_excel or "").strip() or None
    nk = normalize_razon_key(rs) if rs else ""
    if nk and nk in maps.razon_to_principal:
        return maps.razon_to_principal[nk].strip(), rs

    p_mail = principal_desde_correo_catalogo(maps, email_contacto)
    if p_mail:
        return p_mail, rs or (email_contacto or "").strip() or None

    bloque = (bloque_cliente_excel or "").strip()
    if bloque:
        bc = fix_cliente_name_fn(bloque)
        nkb = normalize_razon_key(bc)
        if nkb and (nkb in maps.known_principales_norm or nkb in maps.bloque_hints_norm):
            return bc, rs or bc

    if cli_infer:
        base = fix_cliente_name_fn(cli_infer)
        nk2 = normalize_razon_key(base)
        if nk2 and nk2 in maps.razon_to_principal:
            return maps.razon_to_principal[nk2].strip(), rs or base
        if nk2 and nk2 in maps.known_principales_norm:
            return base, rs or base
        return base, rs or base

    if rs:
        guessed = fix_cliente_name_fn(rs)
        nk3 = normalize_razon_key(guessed)
        if nk3 and nk3 in maps.razon_to_principal:
            return maps.razon_to_principal[nk3].strip(), rs
        return guessed, rs

    return por_clasificar, rs


def dias_credito_para_cliente(maps: CatalogMaps, cliente_principal: str) -> int:
    k = normalize_razon_key(cliente_principal)
    return int(maps.dias_credito.get(k, 0))


def add_days_to_iso_date(fecha_iso: str, days: int) -> str | None:
    if not fecha_iso or days <= 0:
        return None
    try:
        d = datetime.strptime(str(fecha_iso)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return (d + timedelta(days=int(days))).isoformat()
