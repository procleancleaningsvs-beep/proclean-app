from __future__ import annotations

import re
import unicodedata
from typing import Iterable

GROUP_AURIGA = "AURIGA"
GROUP_PEPSI = "PEPSI"
GROUP_CARRIER = "CARRIER"
GROUP_VITRO = "VITRO"

GROUP_PRIORITY = (GROUP_PEPSI, GROUP_CARRIER, GROUP_AURIGA, GROUP_VITRO)

GROUP_LABELS = {
    GROUP_AURIGA: "Auriga",
    GROUP_PEPSI: "Pepsi",
    GROUP_CARRIER: "Carrier",
    GROUP_VITRO: "Vitro",
}

_AURIGA_TOKENS = (
    "AURIGA",
    "ARMIDA",
    "AURORA",
    "CITICA",
    "GM",
    "TORRE BALZAK",
    "TORRE OLMA",
)

_VARIOS_TOKENS = ("MULTICLIENTE", "VARIOS CLIENTES", "VARIOS")


def normalize_group_text(value: str) -> str:
    text = " ".join(str(value or "").replace("\u00a0", " ").upper().split()).strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    return " ".join(text.split()).strip()


def classify_asistencia_group(
    raw_values: Iterable[str], *, is_varios_clients: bool = False
) -> str | None:
    merged = " | ".join(normalize_group_text(value) for value in raw_values if str(value or "").strip())
    if not merged and not is_varios_clients:
        return None
    if "PEPSI" in merged:
        return GROUP_PEPSI
    if "CARRIER" in merged:
        return GROUP_CARRIER
    if any(token in merged for token in _AURIGA_TOKENS):
        return GROUP_AURIGA
    if "VITRO" in merged:
        return GROUP_VITRO
    if is_varios_clients or any(token in merged for token in _VARIOS_TOKENS):
        return GROUP_VITRO
    return None
