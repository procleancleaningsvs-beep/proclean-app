"""
Calendario de días inhábiles configurables (además de sábados y domingos).

Actualización anual:
  Edita el archivo JSON en la ruta devuelta por `inhabiles_json_path(instance_dir)`.
  Formato: objeto con clave "dates" (lista de strings "YYYY-MM-DD").

No se incluyen aquí festivos oficiales automáticos para evitar reglas ocultas;
cualquier día inhábil que no sea fin de semana debe listarse en ese JSON.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

DEFAULT_DOC = (
    "Lista de fechas inhábiles (ISO YYYY-MM-DD) además de sábados y domingos. "
    "Actualizar cada año según calendario aplicable en la operación."
)


def inhabiles_json_path(instance_dir: Path) -> Path:
    return Path(instance_dir) / "carrier_inhabiles.json"


def _default_json_bytes() -> bytes:
    payload = {"_doc": DEFAULT_DOC, "dates": []}
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def ensure_inhabiles_file(instance_dir: Path) -> Path:
    path = inhabiles_json_path(instance_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(_default_json_bytes())
    return path


def load_inhabile_dates(instance_dir: Path) -> set[date]:
    path = ensure_inhabiles_file(instance_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("No se pudo leer carrier_inhabiles.json: %s", exc)
        return set()
    dates = raw.get("dates") if isinstance(raw, dict) else None
    if not isinstance(dates, list):
        return set()
    out: set[date] = set()
    for item in dates:
        if not isinstance(item, str):
            continue
        s = item.strip()[:10]
        try:
            out.add(date.fromisoformat(s))
        except ValueError:
            logger.warning("Fecha inhábil ignorada (formato inválido): %r", item)
    return out


def merge_extra_dates(base: set[date], extra: Iterable[date]) -> set[date]:
    s = set(base)
    s.update(extra)
    return s
