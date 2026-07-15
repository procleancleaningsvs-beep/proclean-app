"""Read-only adapter: saved calculo rows → Banorte draft row payloads."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from modules.nomina.banorte.calculo_queries import (
    get_calculo_run_readonly,
    list_calculo_rows_readonly,
    neto_final_to_decimal,
    neto_to_cents,
)
from modules.nomina.banorte.validators import is_exact_banorte_bank, normalize_banco


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
    omitted: list[dict[str, Any]] = field(default_factory=list)
    amount_errors: list[dict[str, Any]] = field(default_factory=list)


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
    run = get_calculo_run_readonly(db_path, calculo_id)
    if run is None:
        raise KeyError("calculo_not_found")
    rows = list_calculo_rows_readonly(db_path, calculo_id)
    if not rows:
        raise ValueError("calculo_empty")
    oh = origin_hash_for_run(run, rows)
    out_rows: list[AdapterRow] = []
    amount_errors: list[dict[str, Any]] = []
    omit_agg: dict[str, dict[str, Any]] = {}

    def _omit(causa: str, banco: str, cents: int) -> None:
        key = f"{causa}|{normalize_banco(banco)}"
        bucket = omit_agg.setdefault(
            key, {"causa": causa, "banco": banco or "", "count": 0, "total_cents": 0}
        )
        bucket["count"] += 1
        bucket["total_cents"] += max(0, cents)

    for r in rows:
        row_id = int(r["id"])
        try:
            cents = neto_to_cents(r.get("neto_a_pagar_final"))
        except ValueError:
            amount_errors.append(
                {
                    "calculo_row_id": row_id,
                    "causa": "amount_invalid",
                    "nombre": str(r.get("nombre_empleado") or ""),
                }
            )
            continue
        if cents < 0:
            amount_errors.append(
                {
                    "calculo_row_id": row_id,
                    "causa": "amount_negative",
                    "nombre": str(r.get("nombre_empleado") or ""),
                }
            )
            continue

        banco_raw = str(r.get("banco") or "")
        if not is_exact_banorte_bank(banco_raw):
            causa = "banco_vacio" if normalize_banco(banco_raw) == "" else "banco_no_banorte"
            _omit(causa, banco_raw, cents)
            continue

        warnings: list[str] = []
        if cents == 0:
            warnings.append("amount_zero")
            out_rows.append(
                AdapterRow(
                    calculo_row_id=row_id,
                    nombre_recibido=str(r.get("nombre_empleado") or ""),
                    nss_snapshot=str(r["nss"]) if r.get("nss") else None,
                    banco_snapshot=banco_raw or None,
                    employee_number_snapshot=str(r["numero_empleado"]) if r.get("numero_empleado") else None,
                    account_number_snapshot=str(r["cuenta"]) if r.get("cuenta") else None,
                    amount_original_cents=0,
                    amount_final_cents=0,
                    included=0,
                    row_state="EXCLUDED",
                    match_kind="NONE",
                    warnings=warnings,
                )
            )
            continue

        out_rows.append(
            AdapterRow(
                calculo_row_id=row_id,
                nombre_recibido=str(r.get("nombre_empleado") or ""),
                nss_snapshot=str(r["nss"]) if r.get("nss") else None,
                banco_snapshot=banco_raw or None,
                employee_number_snapshot=str(r["numero_empleado"]) if r.get("numero_empleado") else None,
                account_number_snapshot=str(r["cuenta"]) if r.get("cuenta") else None,
                amount_original_cents=cents,
                amount_final_cents=cents,
                included=1,
                row_state="OK",
                match_kind="NONE",
                warnings=warnings,
            )
        )

    omitted = list(omit_agg.values())
    if not out_rows and not omitted and not amount_errors:
        raise ValueError("calculo_no_banorte_rows")

    return AdapterResult(
        calculo_id=int(calculo_id),
        origin_updated_at=str(run.get("updated_at") or ""),
        origin_hash=oh,
        rows=out_rows,
        omitted=omitted,
        amount_errors=amount_errors,
    )
