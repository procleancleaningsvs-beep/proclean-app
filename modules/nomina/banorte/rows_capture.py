from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from modules.nomina.banorte.money import parse_money, to_cents
from modules.nomina.banorte.paste_service import parse_paste_lists

_OBSERVATION_LABELS: dict[str, str] = {
    "LATEST_RECORD_SELECTED": "Registro oficial más reciente seleccionado.",
    "NAME_NORMALIZED": "Nombre normalizado automáticamente.",
    "CONTROLLED_EQUIVALENCE": "Coincidencia mediante equivalencia controlada.",
    "RECONCILIATION_REVIEW": "Reconciliación requiere revisión.",
    "ACCOUNT_MISMATCH": "Cuenta no coincide.",
    "EMPLOYEE_MISMATCH": "Número de empleado no coincide.",
    "AMOUNT_INVALID": "Importe inválido.",
    "AMOUNT_EMPTY": "Importe vacío.",
    "NAME_EMPTY": "Nombre vacío.",
    "LENGTH_MISMATCH": "Listas con distinta longitud.",
}


@dataclass
class CaptureRow:
    client_row_key: str
    position: int
    name_raw: str | None
    catalog_person_id: int | None
    amount_raw: str | None
    state: str
    beneficiary_id: int | None = None
    observation_codes: list[str] = field(default_factory=list)

    def observation_text(self) -> str:
        parts = [
            _OBSERVATION_LABELS[code]
            for code in self.observation_codes
            if code in _OBSERVATION_LABELS
        ]
        return " ".join(parts)


def observation_label(code: str) -> str | None:
    return _OBSERVATION_LABELS.get(code)


def _new_key() -> str:
    return uuid.uuid4().hex


def _split_lines(text: str) -> list[str]:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _is_unambiguous_tsv(lines: list[str]) -> bool:
    relevant = [line for line in lines if str(line).strip()]
    if len(relevant) < 1:
        return False
    for line in relevant:
        if line.count("\t") != 1:
            return False
        parts = line.split("\t")
        if len(parts) != 2:
            return False
    return True


def parse_tsv_capture(text: str) -> list[CaptureRow] | None:
    lines = _split_lines(text)
    if not _is_unambiguous_tsv(lines):
        return None
    rows: list[CaptureRow] = []
    for i, line in enumerate(lines, start=1):
        if not str(line).strip():
            rows.append(
                CaptureRow(
                    client_row_key=_new_key(),
                    position=i,
                    name_raw="",
                    catalog_person_id=None,
                    amount_raw="",
                    state="NEEDS_REVIEW",
                    observation_codes=["NAME_EMPTY", "AMOUNT_EMPTY"],
                )
            )
            continue
        name_part, amount_part = line.split("\t", 1)
        rows.append(_capture_from_parts(i, name_part, amount_part))
    return rows


def _capture_from_parts(position: int, name_raw: str | None, amount_raw: str | None) -> CaptureRow:
    codes: list[str] = []
    state = "OK"
    name = name_raw if name_raw is not None else ""
    amount = amount_raw if amount_raw is not None else ""
    if not str(name).strip():
        codes.append("NAME_EMPTY")
        state = "NEEDS_REVIEW"
    money = parse_money(amount) if str(amount).strip() else None
    if not str(amount).strip():
        codes.append("AMOUNT_EMPTY")
        state = "NEEDS_REVIEW"
    elif money is None or not money.ok:
        codes.append("AMOUNT_INVALID")
        state = "NEEDS_REVIEW"
    elif money.error == "zero" or (money.amount is not None and to_cents(money.amount) <= 0):
        codes.append("AMOUNT_INVALID")
        state = "NEEDS_REVIEW"
    return CaptureRow(
        client_row_key=_new_key(),
        position=position,
        name_raw=name,
        catalog_person_id=None,
        amount_raw=amount,
        state=state,
        observation_codes=codes,
    )


def parse_rows_from_lists(names_text: str, amounts_text: str) -> list[CaptureRow]:
    parsed = parse_paste_lists(names_text, amounts_text)
    rows: list[CaptureRow] = []
    for line in parsed.rows:
        codes: list[str] = []
        state = "OK"
        if line.incomplete:
            state = "NEEDS_REVIEW"
            if not str(line.raw_name or "").strip():
                codes.append("NAME_EMPTY")
            if not str(line.raw_amount or "").strip():
                codes.append("AMOUNT_EMPTY")
            elif line.amount_result is None or not line.amount_result.ok:
                codes.append("AMOUNT_INVALID")
        if parsed.length_mismatch:
            codes.append("LENGTH_MISMATCH")
            state = "NEEDS_REVIEW"
        rows.append(
            CaptureRow(
                client_row_key=_new_key(),
                position=line.position,
                name_raw=line.raw_name,
                catalog_person_id=None,
                amount_raw=line.raw_amount,
                state=state,
                observation_codes=codes,
            )
        )
    return rows


def parse_rows_from_payload(payload: list[dict]) -> list[CaptureRow]:
    rows: list[CaptureRow] = []
    for i, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            continue
        position = int(item.get("position") or i)
        key = str(item.get("client_row_key") or _new_key())
        catalog_person_id = item.get("catalog_person_id")
        cpid = int(catalog_person_id) if catalog_person_id not in (None, "") else None
        bid = item.get("beneficiary_id")
        beneficiary_id = int(bid) if bid not in (None, "") else None
        row = _capture_from_parts(
            position,
            item.get("name_raw"),
            item.get("amount_raw"),
        )
        row.client_row_key = key
        row.catalog_person_id = cpid
        row.beneficiary_id = beneficiary_id
        if cpid is not None and row.state == "OK":
            row.observation_codes = list(row.observation_codes)
        rows.append(row)
    rows.sort(key=lambda r: r.position)
    for idx, row in enumerate(rows, start=1):
        row.position = idx
    return rows


def parse_capture_input(
    *,
    names_text: str = "",
    amounts_text: str = "",
    rows_payload: list[dict] | None = None,
    tsv_text: str = "",
) -> list[CaptureRow]:
    if rows_payload:
        return parse_rows_from_payload(rows_payload)
    if tsv_text.strip():
        tsv_rows = parse_tsv_capture(tsv_text)
        if tsv_rows is not None:
            return tsv_rows
    combined = names_text
    if amounts_text and "\t" in names_text and not amounts_text.strip():
        tsv_rows = parse_tsv_capture(names_text)
        if tsv_rows is not None:
            return tsv_rows
    return parse_rows_from_lists(names_text, amounts_text)


def capture_rows_to_prepare_inputs(rows: list[CaptureRow]) -> list[dict]:
    prepared: list[dict] = []
    for row in rows:
        cents = 0
        warnings: list[str] = []
        if row.amount_raw is not None and str(row.amount_raw).strip():
            money = parse_money(row.amount_raw)
            if money.ok and money.amount is not None:
                cents = to_cents(money.amount)
            else:
                warnings.append("amount_invalid")
        elif "AMOUNT_EMPTY" in row.observation_codes:
            warnings.append("amount_empty")
        included = 1 if row.state == "OK" and cents > 0 else 0
        row_state = row.state if included else ("EXCLUDED" if cents <= 0 and row.state == "OK" else row.state)
        prepared.append(
            {
                "position": row.position,
                "nombre_recibido": row.name_raw or "",
                "amount_original_cents": max(0, cents),
                "amount_final_cents": cents if cents > 0 else 0,
                "included": included,
                "match_kind": "NONE",
                "row_state": row_state if included else ("EXCLUDED" if row_state == "OK" else row_state),
                "warnings": warnings,
                "user_decision": {},
                "catalog_person_id": row.catalog_person_id,
                "beneficiary_id": row.beneficiary_id,
                "catalog_observation_codes": list(row.observation_codes),
                "client_row_key": row.client_row_key,
            }
        )
    return prepared
