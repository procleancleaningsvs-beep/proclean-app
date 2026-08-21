from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


BENEFICIARY_MATERIAL_FINGERPRINT_VERSION = "BENEFICIARY_MATERIAL_V1"
BENEFICIARY_MATERIAL_KEYS: tuple[str, ...] = (
    "id",
    "nombre_normalizado",
    "curp",
    "employee_number_requested",
    "employee_number_effective",
    "account_number",
    "source_kind",
    "validation_status",
    "record_status",
    "banorte_employee_substituted",
    "manual_effective_from_account",
    "replaces_id",
)


@dataclass(frozen=True)
class BeneficiaryMaterialFingerprint:
    version: str
    sha256: str
    state_json: str


def beneficiary_material_state(beneficiary: Mapping[str, Any]) -> dict[str, Any]:
    state: dict[str, Any] = {}
    for key in BENEFICIARY_MATERIAL_KEYS:
        value = beneficiary[key]
        if key in {"id", "replaces_id"}:
            value = int(value) if value is not None else None
        elif key in {"banorte_employee_substituted", "manual_effective_from_account"}:
            value = int(value or 0)
        elif value is not None:
            value = str(value)
        state[key] = value
    return state


def beneficiary_material_state_json(beneficiary: Mapping[str, Any]) -> str:
    return json.dumps(
        beneficiary_material_state(beneficiary),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def beneficiary_material_fingerprint(
    beneficiary: Mapping[str, Any],
) -> BeneficiaryMaterialFingerprint:
    state_json = beneficiary_material_state_json(beneficiary)
    return BeneficiaryMaterialFingerprint(
        version=BENEFICIARY_MATERIAL_FINGERPRINT_VERSION,
        sha256=hashlib.sha256(state_json.encode("utf-8")).hexdigest(),
        state_json=state_json,
    )
