from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.validators import normalize_name


@dataclass
class MatchCandidate:
    beneficiary_id: int
    nombre_original: str
    employee_number_effective: str
    account_number: str
    curp: str | None
    validation_status: str
    record_status: str
    score: float
    via: str


@dataclass
class MatchResult:
    kind: str  # EXACT | ALIAS | FUZZY_RECOMMENDATION | AMBIGUOUS | NONE | ALIAS_INACTIVE_RESOLVED | ALIAS_INACTIVE_BLOCKED
    selected_id: int | None = None
    auto_selected: bool = False
    candidates: list[MatchCandidate] = field(default_factory=list)
    alias_id: int | None = None
    alias_pointed_inactive_id: int | None = None
    message: str | None = None


def _fold(text: str) -> str:
    return normalize_name(text)


def _active_beneficiaries(conn) -> list[Any]:
    return list(
        conn.execute(
            "SELECT * FROM nomina_banorte_beneficiaries WHERE record_status='ACTIVO' ORDER BY id"
        ).fetchall()
    )


def _follow_replacement_chain(conn, beneficiary_id: int) -> list[int]:
    """Return ACTIVO successors that replace this id (direct children only expanded once)."""
    # Find unique ACTIVO descendant via replaces_id chain.
    visited: set[int] = set()
    current = beneficiary_id
    activos: list[int] = []
    # Walk forward: rows where replaces_id points to current, repeatedly.
    frontier = [current]
    while frontier:
        cid = frontier.pop()
        if cid in visited:
            continue
        visited.add(cid)
        children = conn.execute(
            "SELECT id, record_status FROM nomina_banorte_beneficiaries WHERE replaces_id=?",
            (cid,),
        ).fetchall()
        for ch in children:
            if ch["record_status"] == "ACTIVO":
                activos.append(int(ch["id"]))
            frontier.append(int(ch["id"]))
    return activos


def match_name(db_path: str, raw_name: str) -> MatchResult:
    target = _fold(raw_name)
    if not target:
        return MatchResult(kind="NONE", message="empty_name")
    conn = connect(db_path)
    try:
        # Alias first
        alias = conn.execute(
            "SELECT * FROM nomina_banorte_aliases WHERE alias_normalizado=? AND is_active=1",
            (target,),
        ).fetchone()
        if alias is not None:
            ben = conn.execute(
                "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
                (int(alias["beneficiary_id"]),),
            ).fetchone()
            if ben is None:
                return MatchResult(kind="NONE", message="alias_orphan")
            if ben["record_status"] == "ACTIVO":
                return MatchResult(
                    kind="ALIAS",
                    selected_id=int(ben["id"]),
                    auto_selected=True,
                    alias_id=int(alias["id"]),
                    candidates=[
                        MatchCandidate(
                            int(ben["id"]),
                            ben["nombre_original"],
                            ben["employee_number_effective"],
                            ben["account_number"],
                            ben["curp"],
                            ben["validation_status"],
                            ben["record_status"],
                            1.0,
                            "alias",
                        )
                    ],
                )
            # Inactive: resolve via replacement chain
            activos = _follow_replacement_chain(conn, int(ben["id"]))
            if len(activos) == 1:
                succ = conn.execute(
                    "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
                    (activos[0],),
                ).fetchone()
                return MatchResult(
                    kind="ALIAS_INACTIVE_RESOLVED",
                    selected_id=None,  # recommendation only — not auto for inactive origin
                    auto_selected=False,
                    alias_id=int(alias["id"]),
                    alias_pointed_inactive_id=int(ben["id"]),
                    message="alias_points_to_inactive_recommend_successor",
                    candidates=[
                        MatchCandidate(
                            int(succ["id"]),
                            succ["nombre_original"],
                            succ["employee_number_effective"],
                            succ["account_number"],
                            succ["curp"],
                            succ["validation_status"],
                            succ["record_status"],
                            1.0,
                            "alias_successor",
                        )
                    ],
                )
            return MatchResult(
                kind="ALIAS_INACTIVE_BLOCKED",
                auto_selected=False,
                alias_id=int(alias["id"]),
                alias_pointed_inactive_id=int(ben["id"]),
                message="alias_inactive_ambiguous_or_missing_successor",
            )

        actives = _active_beneficiaries(conn)
        exact = [b for b in actives if b["nombre_normalizado"] == target]
        if len(exact) == 1:
            b = exact[0]
            return MatchResult(
                kind="EXACT",
                selected_id=int(b["id"]),
                auto_selected=True,
                candidates=[
                    MatchCandidate(
                        int(b["id"]),
                        b["nombre_original"],
                        b["employee_number_effective"],
                        b["account_number"],
                        b["curp"],
                        b["validation_status"],
                        b["record_status"],
                        1.0,
                        "exact",
                    )
                ],
            )
        if len(exact) > 1:
            return MatchResult(
                kind="AMBIGUOUS",
                auto_selected=False,
                candidates=[
                    MatchCandidate(
                        int(b["id"]),
                        b["nombre_original"],
                        b["employee_number_effective"],
                        b["account_number"],
                        b["curp"],
                        b["validation_status"],
                        b["record_status"],
                        1.0,
                        "exact_ambiguous",
                    )
                    for b in exact
                ],
            )

        scored: list[MatchCandidate] = []
        for b in actives:
            score = SequenceMatcher(None, target, b["nombre_normalizado"]).ratio()
            # light MA/MARIA suggestion boost only for ranking, never auto
            if " MA " in f" {target} " or target.startswith("MA "):
                alt = target.replace(" MA ", " MARIA ").replace("MA ", "MARIA ", 1)
                score = max(score, SequenceMatcher(None, alt, b["nombre_normalizado"]).ratio())
            if score >= 0.86:
                scored.append(
                    MatchCandidate(
                        int(b["id"]),
                        b["nombre_original"],
                        b["employee_number_effective"],
                        b["account_number"],
                        b["curp"],
                        b["validation_status"],
                        b["record_status"],
                        score,
                        "fuzzy",
                    )
                )
        scored.sort(key=lambda c: c.score, reverse=True)
        if len(scored) == 1:
            return MatchResult(
                kind="FUZZY_RECOMMENDATION",
                selected_id=None,
                auto_selected=False,
                candidates=scored,
                message="fuzzy_requires_confirmation",
            )
        if len(scored) > 1:
            return MatchResult(kind="AMBIGUOUS", auto_selected=False, candidates=scored[:5])
        return MatchResult(kind="NONE")
    finally:
        conn.close()


def save_alias(db_path: str, alias_original: str, beneficiary_id: int, user: str) -> int:
    norm = _fold(alias_original)
    conn = connect(db_path)
    try:
        existing = conn.execute(
            "SELECT * FROM nomina_banorte_aliases WHERE alias_normalizado=? AND is_active=1",
            (norm,),
        ).fetchone()
        if existing is not None:
            if int(existing["beneficiary_id"]) == int(beneficiary_id):
                return int(existing["id"])
            raise ValueError("active_alias_exists_deactivate_first")
        ben = conn.execute(
            "SELECT id FROM nomina_banorte_beneficiaries WHERE id=? AND record_status='ACTIVO'",
            (beneficiary_id,),
        ).fetchone()
        if ben is None:
            raise ValueError("beneficiary_not_active")
        from datetime import datetime
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("America/Monterrey")).isoformat(timespec="seconds")
        cur = conn.execute(
            """
            INSERT INTO nomina_banorte_aliases (
                alias_original, alias_normalizado, beneficiary_id, is_active, created_by, created_at
            ) VALUES (?,?,?,1,?,?)
            """,
            (alias_original, norm, beneficiary_id, user, now),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def deactivate_alias(db_path: str, alias_id: int, user: str) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("America/Monterrey")).isoformat(timespec="seconds")
    conn = connect(db_path)
    try:
        conn.execute(
            """
            UPDATE nomina_banorte_aliases
            SET is_active=0, deactivated_by=?, deactivated_at=?
            WHERE id=?
            """,
            (user, now, alias_id),
        )
        conn.commit()
    finally:
        conn.close()
