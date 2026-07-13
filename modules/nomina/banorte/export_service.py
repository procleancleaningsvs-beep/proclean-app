from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from modules.nomina.banorte.models import NormalizedPayment
from modules.nomina.banorte.money import parse_money, to_cents
from modules.nomina.banorte.pag_layout import build_filename, build_pag_file, sha256_hex
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables

TZ = ZoneInfo("America/Monterrey")


@dataclass
class DraftPaymentRow:
    position: int
    nombre_recibido: str
    beneficiary_id: int
    amount_raw: str
    match_kind: str
    alias_id: int | None = None
    # Optional client-claimed snapshots for drift detection
    client_employee_number: str | None = None
    client_account_number: str | None = None
    warnings: list[str] | None = None
    user_decision: dict[str, Any] | None = None


@dataclass
class ExportResult:
    export_id: int
    filename: str
    file_bytes: bytes
    file_sha256: str
    total_cents: int
    payment_count: int


class ExportBlockedError(ValueError):
    def __init__(self, code: str, rows: list[dict[str, Any]] | None = None):
        super().__init__(code)
        self.code = code
        self.rows = rows or []


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def generate_export(
    db_path: str,
    user: str,
    draft_rows: Sequence[DraftPaymentRow],
    *,
    consecutive: str,
    layout_date: str | None = None,
    confirm_duplicate_consecutive: bool = False,
    confirm_manuals: bool = False,
    confirm_date_override: bool = False,
    capture_origin: str = "PASTE_LISTS",
) -> ExportResult:
    now = _now()
    auto_date = datetime.now(TZ).strftime("%Y%m%d")
    used_date = layout_date or auto_date
    if layout_date and layout_date != auto_date and not confirm_date_override:
        raise ExportBlockedError("date_override_confirmation_required")

    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        if not draft_rows:
            raise ExportBlockedError("empty_draft")

        rebuilt: list[NormalizedPayment] = []
        item_rows: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        manual_count = 0
        aliases_used: list[int] = []
        recommendations_accepted: list[int] = []

        for draft in draft_rows:
            ben = conn.execute(
                "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
                (draft.beneficiary_id,),
            ).fetchone()
            if ben is None:
                blocked.append({"position": draft.position, "reason": "beneficiary_missing"})
                continue
            if ben["record_status"] != "ACTIVO":
                blocked.append(
                    {
                        "position": draft.position,
                        "reason": "beneficiary_not_active",
                        "record_status": ben["record_status"],
                    }
                )
                continue
            # Drift vs client snapshot
            if draft.client_account_number is not None and str(draft.client_account_number) != str(
                ben["account_number"]
            ):
                blocked.append({"position": draft.position, "reason": "account_changed_since_preview"})
                continue
            if draft.client_employee_number is not None and str(draft.client_employee_number) != str(
                ben["employee_number_effective"]
            ):
                blocked.append({"position": draft.position, "reason": "employee_changed_since_preview"})
                continue

            money = parse_money(draft.amount_raw)
            if not money.ok or money.amount is None:
                blocked.append({"position": draft.position, "reason": money.error or "invalid_amount"})
                continue
            cents = to_cents(money.amount)
            if ben["validation_status"] == "MANUAL_PENDIENTE_VALIDACION":
                manual_count += 1
            rebuilt.append(
                NormalizedPayment(
                    beneficiary_id=int(ben["id"]),
                    employee_number=str(ben["employee_number_effective"]),
                    account_number=str(ben["account_number"]),
                    amount=money.amount,
                    source_reference=f"pos:{draft.position}",
                )
            )
            if draft.alias_id:
                aliases_used.append(int(draft.alias_id))
            if draft.match_kind == "FUZZY_ACCEPTED":
                recommendations_accepted.append(draft.position)
            item_rows.append(
                {
                    "position": draft.position,
                    "nombre_recibido": draft.nombre_recibido,
                    "beneficiary_id": int(ben["id"]),
                    "employee_number_effective": str(ben["employee_number_effective"]),
                    "account_number": str(ben["account_number"]),
                    "curp": ben["curp"],
                    "amount_cents": cents,
                    "match_kind": draft.match_kind,
                    "alias_id": draft.alias_id,
                    "validation_status": ben["validation_status"],
                    "record_status": ben["record_status"],
                    "is_manual_beneficiary": 1
                    if ben["validation_status"] == "MANUAL_PENDIENTE_VALIDACION"
                    else 0,
                    "warnings_json": json.dumps(draft.warnings or [], ensure_ascii=False),
                    "user_decision_json": json.dumps(draft.user_decision or {}, ensure_ascii=False),
                }
            )

        if blocked:
            raise ExportBlockedError("rows_require_review", blocked)
        if manual_count and not confirm_manuals:
            raise ExportBlockedError("manual_beneficiaries_confirmation_required")

        prior = conn.execute(
            "SELECT id FROM nomina_banorte_exports WHERE layout_date=? AND consecutive=? ORDER BY id DESC LIMIT 1",
            (used_date, consecutive),
        ).fetchone()
        if prior is not None and not confirm_duplicate_consecutive:
            raise ExportBlockedError("duplicate_consecutive_confirmation_required")

        file_bytes = build_pag_file(layout_date=used_date, consecutive=consecutive, payments=rebuilt)
        digest = sha256_hex(file_bytes)
        filename = build_filename(consecutive)
        total_cents = sum(r["amount_cents"] for r in item_rows)

        cur = conn.execute(
            """
            INSERT INTO nomina_banorte_exports (
                created_by, created_at, timezone, layout_date, layout_date_auto,
                date_override_confirmed, consecutive, filename, payment_count, total_cents,
                capture_origin, incidents_json, manual_row_count, aliases_used_json,
                recommendations_accepted_json, warnings_ignored_json,
                duplicate_consecutive_confirmed, duplicate_of_export_id,
                file_sha256, file_size, file_blob, status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'GENERATED')
            """,
            (
                user,
                now,
                "America/Monterrey",
                used_date,
                auto_date,
                1 if confirm_date_override and layout_date and layout_date != auto_date else 0,
                consecutive,
                filename,
                len(item_rows),
                total_cents,
                capture_origin,
                json.dumps([], ensure_ascii=False),
                manual_count,
                json.dumps(aliases_used),
                json.dumps(recommendations_accepted),
                json.dumps(
                    ["duplicate_consecutive"] if prior is not None and confirm_duplicate_consecutive else []
                ),
                1 if prior is not None and confirm_duplicate_consecutive else 0,
                int(prior["id"]) if prior is not None and confirm_duplicate_consecutive else None,
                digest,
                len(file_bytes),
                file_bytes,
            ),
        )
        export_id = int(cur.lastrowid)
        for item in item_rows:
            conn.execute(
                """
                INSERT INTO nomina_banorte_export_items (
                    export_id, position, nombre_recibido, beneficiary_id,
                    employee_number_effective, account_number, curp, amount_cents,
                    match_kind, alias_id, validation_status, record_status,
                    is_manual_beneficiary, warnings_json, user_decision_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    export_id,
                    item["position"],
                    item["nombre_recibido"],
                    item["beneficiary_id"],
                    item["employee_number_effective"],
                    item["account_number"],
                    item["curp"],
                    item["amount_cents"],
                    item["match_kind"],
                    item["alias_id"],
                    item["validation_status"],
                    item["record_status"],
                    item["is_manual_beneficiary"],
                    item["warnings_json"],
                    item["user_decision_json"],
                ),
            )
        conn.commit()
        return ExportResult(export_id, filename, file_bytes, digest, total_cents, len(item_rows))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_export_blob(db_path: str, export_id: int) -> tuple[str, bytes, str]:
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT filename, file_blob, file_sha256 FROM nomina_banorte_exports WHERE id=?",
            (export_id,),
        ).fetchone()
        if row is None:
            raise KeyError("export_not_found")
        return str(row["filename"]), bytes(row["file_blob"]), str(row["file_sha256"])
    finally:
        conn.close()
