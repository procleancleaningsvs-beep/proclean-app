"""Deterministic catalog-to-catalog lineage decisions for Catalog Admin V2 C2."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from modules.nomina.banorte.catalog_parser import (
    catalog_name_key_v1,
    catalog_name_normalized_v1,
)


LINEAGE_EVIDENCE_VERSION = "CATALOG_LINEAGE_V1"
_GENERIC_RFCS = frozenset({"XAXX010101000", "XEXX010101000"})
# Catalog persons are employees (natural persons), whose complete RFC has 13 chars.
_RFC_RE = re.compile(r"^[A-ZÑ&]{4}(\d{2})(\d{2})(\d{2})[A-Z0-9]{3}$")


@dataclass(frozen=True)
class PriorCurrentCandidate:
    authority_kind: str
    authority_version_id: int
    person_id: int | None
    beneficiary_id: int
    employee: str
    account: str
    name_original: str
    name_normalized: str
    name_controlled_key: str
    beneficiary_name_normalized: str
    beneficiary_employee_requested: str | None
    beneficiary_substituted: int
    beneficiary_manual_effective: int
    rfc: str | None
    birth_date: str | None
    material_fingerprint_version: str
    material_fingerprint: str


@dataclass(frozen=True)
class LineageDecision:
    status: str
    method: str
    predecessor_person_id: int | None
    predecessor_beneficiary_id: int | None
    evidence_json: str | None
    evidence_sha256: str | None
    matched_signals: tuple[str, ...]
    different_signals: tuple[str, ...]
    candidate_count: int


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def structurally_valid_rfc(value: Any) -> bool:
    """Validate the deterministic structure and embedded date of a Mexican RFC."""
    rfc = str(value or "").strip().upper()
    if rfc in _GENERIC_RFCS:
        return False
    match = _RFC_RE.fullmatch(rfc)
    if match is None:
        return False
    year, month, day = map(int, match.groups())
    for full_year in (1900 + year, 2000 + year):
        try:
            date(full_year, month, day)
            return True
        except ValueError:
            continue
    return False


def rfc_matches_birth_date(value: Any, birth_date_iso: str) -> bool:
    rfc = str(value or "").strip().upper()
    if not structurally_valid_rfc(rfc):
        return False
    try:
        birth = date.fromisoformat(str(birth_date_iso))
    except ValueError:
        return False
    match = _RFC_RE.fullmatch(rfc)
    assert match is not None
    return match.groups() == (f"{birth.year % 100:02d}", f"{birth.month:02d}", f"{birth.day:02d}")


def compatible_name_method(target_name: str, prior_name: str, *, l2: bool = False) -> str | None:
    prefix = "PREVIOUS_ACTIVE_RFC_BIRTH_" if l2 else "EXACT_EMPLOYEE_ACCOUNT_"
    if str(target_name or "").strip().casefold() == str(prior_name or "").strip().casefold():
        return prefix + "RAW_NAME"
    if catalog_name_normalized_v1(target_name) == catalog_name_normalized_v1(prior_name):
        return prefix + "CANONICAL_NAME"
    if catalog_name_key_v1(target_name) == catalog_name_key_v1(prior_name):
        return prefix + "CONTROLLED_MA"
    return None


def _evidence(
    *,
    target_version_id: int,
    target_person_id: int,
    target_row_hash: str,
    candidate: PriorCurrentCandidate,
    method: str,
    matched: Iterable[str],
    different: Iterable[str],
) -> tuple[str, str]:
    payload = {
        "evidence_version": LINEAGE_EVIDENCE_VERSION,
        "target": {
            "version_id": int(target_version_id),
            "person_id": int(target_person_id),
            "row_sha256": str(target_row_hash),
        },
        "predecessor_authority": {
            "kind": candidate.authority_kind,
            "version_id": int(candidate.authority_version_id),
            "person_id": candidate.person_id,
            "beneficiary_id": int(candidate.beneficiary_id),
            "material_fingerprint_version": candidate.material_fingerprint_version,
            "material_fingerprint": candidate.material_fingerprint,
        },
        "method": method,
        "matched_signals": sorted(set(matched)),
        "different_signals": sorted(set(different)),
    }
    encoded = _canonical_json(payload)
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def unconfirmed_lineage(*, candidate_count: int = 0) -> LineageDecision:
    return LineageDecision(
        status="UNCONFIRMED",
        method="NONE",
        predecessor_person_id=None,
        predecessor_beneficiary_id=None,
        evidence_json=None,
        evidence_sha256=None,
        matched_signals=(),
        different_signals=(),
        candidate_count=int(candidate_count),
    )


def manual_confirmed_lineage(
    *,
    target_version_id: int,
    target_person_id: int,
    target_row_hash: str,
    candidate: PriorCurrentCandidate,
) -> LineageDecision:
    evidence_json, evidence_sha = _evidence(
        target_version_id=target_version_id,
        target_person_id=target_person_id,
        target_row_hash=target_row_hash,
        candidate=candidate,
        method="MANUAL_CONTINUITY_CONFIRMED",
        matched=("manual_prior_current",),
        different=(),
    )
    return LineageDecision(
        status="CONFIRMED",
        method="MANUAL_CONTINUITY_CONFIRMED",
        predecessor_person_id=candidate.person_id,
        predecessor_beneficiary_id=candidate.beneficiary_id,
        evidence_json=evidence_json,
        evidence_sha256=evidence_sha,
        matched_signals=("manual_prior_current",),
        different_signals=(),
        candidate_count=1,
    )


def decide_automatic_lineage(
    *,
    target_version_id: int,
    target_person_id: int,
    target_row_hash: str,
    target_employee: str,
    target_account: str,
    target_name: str,
    target_rfc: str,
    target_birth_date: str,
    candidates: Iterable[PriorCurrentCandidate],
    target_rfc_count: int,
    prior_rfc_count: int,
) -> LineageDecision:
    """Apply frozen L1 then L2 rules; insufficient evidence never blocks existence."""
    universe = tuple(candidates)
    exact_pair = tuple(
        candidate
        for candidate in universe
        if candidate.employee == target_employee and candidate.account == target_account
    )
    compatible_pair = tuple(
        (candidate, compatible_name_method(target_name, candidate.name_original))
        for candidate in exact_pair
    )
    compatible_pair = tuple(item for item in compatible_pair if item[1] is not None)
    if len(compatible_pair) == 1:
        candidate, method = compatible_pair[0]
        contradiction = False
        if candidate.person_id is not None:
            if candidate.birth_date and candidate.birth_date != target_birth_date:
                contradiction = True
            if (
                candidate.rfc
                and structurally_valid_rfc(candidate.rfc)
                and structurally_valid_rfc(target_rfc)
                and candidate.rfc != target_rfc
            ):
                contradiction = True
        if not contradiction:
            evidence_json, evidence_sha = _evidence(
                target_version_id=target_version_id,
                target_person_id=target_person_id,
                target_row_hash=target_row_hash,
                candidate=candidate,
                method=str(method),
                matched=("employee", "account", "name"),
                different=(),
            )
            return LineageDecision(
                status="CONFIRMED",
                method=str(method),
                predecessor_person_id=candidate.person_id,
                predecessor_beneficiary_id=candidate.beneficiary_id,
                evidence_json=evidence_json,
                evidence_sha256=evidence_sha,
                matched_signals=("account", "employee", "name"),
                different_signals=(),
                candidate_count=1,
            )

    valid_l2 = rfc_matches_birth_date(target_rfc, target_birth_date) and int(target_rfc_count) == 1
    if valid_l2:
        l2_candidates = tuple(
            candidate
            for candidate in universe
            if candidate.person_id is not None
            and candidate.rfc == target_rfc
            and candidate.birth_date == target_birth_date
            and rfc_matches_birth_date(candidate.rfc, str(candidate.birth_date))
        )
        compatible_l2 = tuple(
            (candidate, compatible_name_method(target_name, candidate.name_original, l2=True))
            for candidate in l2_candidates
        )
        compatible_l2 = tuple(item for item in compatible_l2 if item[1] is not None)
        if len(compatible_l2) == 1 and int(prior_rfc_count) == 1:
            candidate, method = compatible_l2[0]
            different = tuple(
                signal
                for signal, changed in (
                    ("employee", candidate.employee != target_employee),
                    ("account", candidate.account != target_account),
                )
                if changed
            )
            evidence_json, evidence_sha = _evidence(
                target_version_id=target_version_id,
                target_person_id=target_person_id,
                target_row_hash=target_row_hash,
                candidate=candidate,
                method=str(method),
                matched=("rfc", "birth_date", "name"),
                different=different,
            )
            return LineageDecision(
                status="CONFIRMED",
                method=str(method),
                predecessor_person_id=candidate.person_id,
                predecessor_beneficiary_id=candidate.beneficiary_id,
                evidence_json=evidence_json,
                evidence_sha256=evidence_sha,
                matched_signals=("birth_date", "name", "rfc"),
                different_signals=tuple(sorted(different)),
                candidate_count=1,
            )
    return unconfirmed_lineage(candidate_count=len(exact_pair))
