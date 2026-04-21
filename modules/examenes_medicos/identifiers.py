"""Generación y reutilización de identificadores administrativos (sin tocar plantillas DOCX)."""

from __future__ import annotations

import hashlib
import random
import sqlite3


def normalize_nombre_key(nombres: str, apellidos: str) -> str:
    """Clave estable para mismo paciente (mayúsculas, espacios colapsados)."""
    n = " ".join(str(nombres or "").split()).casefold()
    a = " ".join(str(apellidos or "").split()).casefold()
    return f"{n}|||{a}"


def stable_codigo_barra(nombre_key: str) -> str:
    """3 letras A-Z + 10 dígitos, estable para la misma clave de nombre."""
    h = hashlib.sha256(nombre_key.encode("utf-8")).hexdigest()
    letters = "".join(chr(65 + int(h[i : i + 2], 16) % 26) for i in (0, 2, 4))
    n = int(h[8:32], 16) % (10**10)
    digits = f"{n:010d}"
    return letters + digits


def _rand_digits(rng: random.Random, n: int) -> str:
    lo = 10 ** (n - 1)
    hi = (10**n) - 1
    return str(rng.randint(lo, hi))


def generate_unique_folio_orina(db_path: str, conn: sqlite3.Connection | None, rng: random.Random) -> str:
    for _ in range(40):
        cand = _rand_digits(rng, 6)
        row = conn.execute(
            "SELECT 1 FROM examenes_medicos_folios_usados WHERE kind = ? AND folio = ?",
            ("orina", cand),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO examenes_medicos_folios_usados (kind, folio) VALUES (?, ?)",
                ("orina", cand),
            )
            return cand
    raise RuntimeError("No se pudo generar folio de orina único.")


def generate_unique_folio_sangre(conn: sqlite3.Connection, rng: random.Random) -> str:
    for _ in range(60):
        cand = _rand_digits(rng, 12)
        row = conn.execute(
            "SELECT 1 FROM examenes_medicos_folios_usados WHERE kind = ? AND folio = ?",
            ("sangre", cand),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO examenes_medicos_folios_usados (kind, folio) VALUES (?, ?)",
                ("sangre", cand),
            )
            return cand
    raise RuntimeError("No se pudo generar folio de sangre único.")


def _rand_cliente_new(rng: random.Random) -> str:
    return _rand_digits(rng, 8)


def get_or_create_cliente_numero(conn: sqlite3.Connection, nombre_key: str, rng: random.Random) -> str:
    row = conn.execute(
        "SELECT cliente_numero FROM examenes_medicos_cliente_cache WHERE nombre_key = ?",
        (nombre_key,),
    ).fetchone()
    if row:
        return str(row[0])

    for _ in range(50):
        cand = _rand_cliente_new(rng)
        clash = conn.execute(
            "SELECT 1 FROM examenes_medicos_cliente_cache WHERE cliente_numero = ?", (cand,)
        ).fetchone()
        if clash is None:
            conn.execute(
                """
                INSERT INTO examenes_medicos_cliente_cache (nombre_key, cliente_numero, created_at)
                VALUES (?, ?, datetime('now'))
                """,
                (nombre_key, cand),
            )
            return cand
    raise RuntimeError("No se pudo asignar número de cliente único.")


def migrate_examenes_medicos_identifier_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS examenes_medicos_cliente_cache (
            nombre_key TEXT NOT NULL PRIMARY KEY,
            cliente_numero TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS examenes_medicos_folios_usados (
            kind TEXT NOT NULL CHECK(kind IN ('orina','sangre')),
            folio TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (kind, folio)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_em_cliente_num ON examenes_medicos_cliente_cache (cliente_numero)"
    )
