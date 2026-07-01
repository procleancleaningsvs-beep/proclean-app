"""Generación y reutilización de identificadores administrativos (sin tocar plantillas DOCX)."""

from __future__ import annotations

import hashlib
import random
import re
import secrets
import sqlite3
import unicodedata
from datetime import date
from typing import Any


def normalize_nombre_key(nombres: str, apellidos: str) -> str:
    """Clave estable para mismo paciente (mayúsculas, espacios colapsados)."""
    n = " ".join(str(nombres or "").split()).casefold()
    a = " ".join(str(apellidos or "").split()).casefold()
    return f"{n}|||{a}"


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_patient_identity_key(nombres: str, apellidos: str, fecha_nacimiento: Any) -> str:
    """Identidad persistente: nombre completo normalizado + fecha de nacimiento."""
    n = _strip_accents(" ".join(str(nombres or "").split())).casefold()
    a = _strip_accents(" ".join(str(apellidos or "").split())).casefold()
    fn = str(fecha_nacimiento or "")[:10]
    return f"{n}|||{a}|||{fn}"


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


def generate_unique_folio_orina(conn: sqlite3.Connection, rng: random.Random) -> str:
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


def _secure_digits(n: int) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(n))


def _secure_nonempty_digits(n: int) -> str:
    return _secure_digits(n)


def generate_order_candidate() -> str:
    return f"B{secrets.choice('0123456789')}000{_secure_nonempty_digits(4)}"


def validate_orden_unificada(orden: str) -> bool:
    return re.fullmatch(r"B\d000\d{4}", str(orden or "")) is not None


def compact_order(orden: str) -> str:
    s = str(orden or "")
    if not validate_orden_unificada(s):
        raise ValueError("Orden unificada invalida.")
    return f"{s[0]}{s[1]}{s[5:9]}"


def generate_unique_orden_unificada(conn: sqlite3.Connection, max_attempts: int = 80) -> str:
    for _ in range(max_attempts):
        cand = generate_order_candidate()
        row = conn.execute(
            "SELECT 1 FROM examenes_medicos_ordenes_usadas WHERE orden = ?",
            (cand,),
        ).fetchone()
        if row is None:
            conn.execute("INSERT INTO examenes_medicos_ordenes_usadas (orden) VALUES (?)", (cand,))
            return cand
    raise RuntimeError("No se pudo generar una orden unica para el examen medico.")


def generate_folio_candidate() -> str:
    return _secure_digits(8)


def generate_unique_folio_unificado(conn: sqlite3.Connection, max_attempts: int = 80) -> str:
    for _ in range(max_attempts):
        cand = generate_folio_candidate()
        row = conn.execute(
            "SELECT 1 FROM examenes_medicos_folios_unificados_usados WHERE folio = ?",
            (cand,),
        ).fetchone()
        if row is None:
            conn.execute("INSERT INTO examenes_medicos_folios_unificados_usados (folio) VALUES (?)", (cand,))
            return cand
    raise RuntimeError("No se pudo generar un folio unico para el examen medico.")


def generate_paciente_id_candidate() -> str:
    return _secure_digits(8)


def get_or_create_paciente_id(
    conn: sqlite3.Connection,
    *,
    nombres: str,
    apellidos: str,
    fecha_nacimiento: str,
    max_attempts: int = 80,
) -> str:
    patient_key = normalize_patient_identity_key(nombres, apellidos, fecha_nacimiento)
    row = conn.execute(
        "SELECT paciente_id FROM examenes_medicos_paciente_ids WHERE patient_identity_key = ?",
        (patient_key,),
    ).fetchone()
    if row:
        return str(row[0])

    for _ in range(max_attempts):
        cand = generate_paciente_id_candidate()
        clash = conn.execute(
            "SELECT 1 FROM examenes_medicos_paciente_ids WHERE paciente_id = ?",
            (cand,),
        ).fetchone()
        if clash is None:
            conn.execute(
                """
                INSERT INTO examenes_medicos_paciente_ids (
                    patient_identity_key, paciente_id, nombres_norm, apellidos_norm, fecha_nacimiento
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    patient_key,
                    cand,
                    _strip_accents(" ".join(str(nombres or "").split())).casefold(),
                    _strip_accents(" ".join(str(apellidos or "").split())).casefold(),
                    str(fecha_nacimiento or "")[:10],
                ),
            )
            return cand
    raise RuntimeError("No se pudo asignar ID de paciente unico.")


def birthdate_ddmmaa(fecha_nacimiento: str) -> str:
    d = date.fromisoformat(str(fecha_nacimiento or "")[:10])
    return d.strftime("%d%m%y")


def build_unified_filename_base(orden: str, folio: str, fecha_nacimiento: str) -> str:
    if not re.fullmatch(r"\d{8}", str(folio or "")):
        raise ValueError("Folio unificado invalido.")
    return f"{compact_order(orden)}-{folio}-{birthdate_ddmmaa(fecha_nacimiento)}"


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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS examenes_medicos_paciente_ids (
            patient_identity_key TEXT NOT NULL PRIMARY KEY,
            paciente_id TEXT NOT NULL UNIQUE,
            nombres_norm TEXT,
            apellidos_norm TEXT,
            fecha_nacimiento TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_em_paciente_id ON examenes_medicos_paciente_ids (paciente_id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS examenes_medicos_ordenes_usadas (
            orden TEXT NOT NULL PRIMARY KEY,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS examenes_medicos_folios_unificados_usados (
            folio TEXT NOT NULL PRIMARY KEY,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
