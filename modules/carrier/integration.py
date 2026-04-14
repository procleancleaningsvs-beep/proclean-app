"""
Integración mínima con el flujo existente de Altas / movimientos IMSS (`nuevo_formato`).

Solo usa la sesión Flask y la base de datos; no importa el generador ni modifica rutas
de Altas más allá de parámetros opcionales en la misma vista.
"""

from __future__ import annotations

SESSION_KEY_RETURN_EXPEDIENTE = "carrier_curso_return_expediente_id"


def peek_return_expediente_id(session: dict) -> int | None:
    raw = session.get(SESSION_KEY_RETURN_EXPEDIENTE)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def set_return_expediente_id(session: dict, expediente_id: int) -> None:
    session[SESSION_KEY_RETURN_EXPEDIENTE] = int(expediente_id)


def pop_return_expediente_id(session: dict) -> int | None:
    raw = session.pop(SESSION_KEY_RETURN_EXPEDIENTE, None)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def clear_return_expediente_id(session: dict) -> None:
    session.pop(SESSION_KEY_RETURN_EXPEDIENTE, None)
