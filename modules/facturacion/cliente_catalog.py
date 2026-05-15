"""Catálogo: razón social → cliente principal; días de crédito por cliente."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta


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

    return CatalogMaps(razon_to_principal=razon_to, dias_credito=dias)


def resolve_cliente_principal(
    maps: CatalogMaps,
    *,
    razon_social_excel: str | None,
    cli_infer: str | None,
    fix_cliente_name_fn,
    por_clasificar: str,
) -> tuple[str, str | None]:
    """
    Devuelve (cliente_principal, razon_social_guardar).
    razon_social_guardar: texto de referencia (razón / correo / etiqueta import).
    """
    rs = (razon_social_excel or "").strip() or None
    nk = normalize_razon_key(rs) if rs else ""
    if nk and nk in maps.razon_to_principal:
        return maps.razon_to_principal[nk].strip(), rs

    if cli_infer:
        base = fix_cliente_name_fn(cli_infer)
        nk2 = normalize_razon_key(base)
        if nk2 and nk2 in maps.razon_to_principal:
            return maps.razon_to_principal[nk2].strip(), rs or base
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
