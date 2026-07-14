"""Read-only adapter: saved calculo rows → Banorte draft row payloads."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from modules.nomina.banorte.calculo_queries import (
    get_calculo_run_readonly,
    list_calculo_rows_readonly,
    neto_final_to_decimal,
    neto_to_cents,
)


@dataclass
class AdapterRow:
    calculo_row_id: int
    nombre_recibido: str
    nss_snapshot: str | None
    banco_snapshot: str | None
    employee_number_snapshot: str | None
    account_number_snapshot: str | None
    amount_original_cents: int
    amount_final_cents: int
    included: int
    row_state: str
    match_kind: str
    warnings: list[str]


@dataclass
class AdapterResult:
    calculo_id: int
    origin_updated_at: str
    origin_hash: str
    rows: list[AdapterRow]


def origin_hash_for_run(run: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    parts: list[str] = ["v1"]
    for r in sorted(rows, key=lambda x: int(x["id"])):
        try:
            neto = str(neto_final_to_decimal(r.get("neto_a_pagar_final")))
        except ValueError:
            neto = "INVALID"
        parts.append(
            "|".join(
                [
                    str(r.get("id") or ""),
                    str(r.get("asistencia_row_id") or ""),
                    str(r.get("nss") or ""),
                    str(r.get("numero_empleado") or ""),
                    str(r.get("cuenta") or ""),
                    neto,
                ]
            )
        )
    raw = "v1|" + "|".join(parts[1:])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def origin_hash_for_manual_capture(names_text: str, amounts_text: str) -> str:
    payload = json.dumps(
        {
            "names": (names_text or "").replace("\r\n", "\n").replace("\r", "\n"),
            "amounts": (amounts_text or "").replace("\r\n", "\n").replace("\r", "\n"),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if not digest:
        raise ValueError("empty_manual_hash")
    return digest


def build_draft_rows_from_calculo(db_path: str, calculo_id: int) -> AdapterResult:
    # Guard: never import calc engine
    run = get_calculo_run_readonly(db_path, calculo_id)
    if run is None:
        raise KeyError("calculo_not_found")
    rows = list_calculo_rows_readonly(db_path, calculo_id)
    if not rows:
        raise ValueError("calculo_empty")
    oh = origin_hash_for_run(run, rows)
    out_rows: list[AdapterRow] = []
    for r in rows:
        warnings: list[str] = []
        try:
            cents = neto_to_cents(r.get("neto_a_pagar_final"))
        except ValueError:
            raise ValueError(f"neto_invalid_row:{r.get('id')}") from None
        if cents < 0:
            raise ValueError(f"neto_negative_row:{r.get('id')}")
        included = 1 if cents > 0 else 0
        row_state = "OK" if included else "EXCLUDED"
        banco = str(r.get("banco") or "").strip().upper()
        if included and not banco:
            row_state = "NEEDS_REVIEW"
            warnings.append("banco_vacio")
        elif included and banco and banco != "BANORTE":
            # inclusion refined in prepare_service after matching
            warnings.append("banco_no_banorte")
        out_rows.append(
            AdapterRow(
                calculo_row_id=int(r["id"]),
                nombre_recibido=str(r.get("nombre_empleado") or ""),
                nss_snapshot=str(r["nss"]) if r.get("nss") else None,
                banco_snapshot=str(r["banco"]) if r.get("banco") else None,
                employee_number_snapshot=str(r["numero_empleado"]) if r.get("numero_empleado") else None,
                account_number_snapshot=str(r["cuenta"]) if r.get("cuenta") else None,
                amount_original_cents=cents if cents > 0 else 0,
                amount_final_cents=cents if cents > 0 else 0,
                included=included,
                row_state=row_state if included else "EXCLUDED",
                match_kind="NONE",
                warnings=warnings,
            )
        )
    if not any(x.included for x in out_rows):
        raise ValueError("calculo_no_positive_neto")
    return AdapterResult(
        calculo_id=int(calculo_id),
        origin_updated_at=str(run.get("updated_at") or ""),
        origin_hash=oh,
        rows=out_rows,
    )
