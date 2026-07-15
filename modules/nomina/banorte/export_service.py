from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from modules.nomina.banorte.draft_repository import DraftStaleError, compute_reconciliation, get_draft
from modules.nomina.banorte.drift import DriftError, check_beneficiary_snapshots, check_calculo_origin_drift
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
    client_employee_number: str | None = None
    client_account_number: str | None = None
    warnings: list[str] | None = None
    user_decision: dict[str, Any] | None = None
    calculo_row_id: int | None = None


@dataclass
class ExportResult:
    export_id: int
    filename: str
    file_bytes: bytes
    file_sha256: str
    total_cents: int
    payment_count: int
    layout_date: str | None = None
    layout_date_display: str | None = None


class ExportBlockedError(ValueError):
    def __init__(
        self,
        code: str,
        rows: list[dict[str, Any]] | None = None,
        *,
        prior_export_id: int | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.rows = rows or []
        self.prior_export_id = prior_export_id


def normalize_consecutive(raw: str) -> str:
    """Validate and normalize consecutive to exactly two digits (01-99, not 00)."""
    value = str(raw or "").strip()
    if not value.isdigit():
        raise ExportBlockedError("invalid_consecutive")
    if len(value) > 2:
        raise ExportBlockedError("invalid_consecutive")
    normalized = value.zfill(2) if len(value) == 1 else value
    if len(normalized) != 2 or not normalized.isdigit():
        raise ExportBlockedError("invalid_consecutive")
    num = int(normalized)
    if num < 1 or num > 99:
        raise ExportBlockedError("invalid_consecutive")
    return normalized


def resolve_layout_date_monterrey(*, client_layout_date: str | None = None) -> tuple[str, str]:
    """Authoritative layout date from America/Monterrey; ignores client input."""
    _ = client_layout_date
    now = datetime.now(TZ)
    return now.strftime("%Y%m%d"), now.strftime("%d/%m/%Y")


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _build_payments_and_items(
    conn: sqlite3.Connection,
    draft_rows: Sequence[DraftPaymentRow],
    *,
    confirm_manuals: bool,
) -> tuple[list[NormalizedPayment], list[dict[str, Any]], int, list[int], list[int]]:
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
                "calculo_row_id": draft.calculo_row_id,
            }
        )

    if blocked:
        raise ExportBlockedError("rows_require_review", blocked)
    if manual_count and not confirm_manuals:
        raise ExportBlockedError("manual_beneficiaries_confirmation_required")
    return rebuilt, item_rows, manual_count, aliases_used, recommendations_accepted


def _insert_export(
    conn: sqlite3.Connection,
    *,
    user: str,
    now: str,
    used_date: str,
    auto_date: str,
    consecutive: str,
    filename: str,
    file_bytes: bytes,
    digest: str,
    item_rows: list[dict[str, Any]],
    manual_count: int,
    aliases_used: list[int],
    recommendations_accepted: list[int],
    capture_origin: str,
    confirm_date_override: bool,
    layout_date: str | None,
    prior_id: int | None,
    confirm_duplicate_consecutive: bool,
    calculo_id: int | None,
    draft_id: int | None,
) -> int:
    total_cents = sum(r["amount_cents"] for r in item_rows)
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_exports (
            created_by, created_at, timezone, layout_date, layout_date_auto,
            date_override_confirmed, consecutive, filename, payment_count, total_cents,
            capture_origin, incidents_json, manual_row_count, aliases_used_json,
            recommendations_accepted_json, warnings_ignored_json,
            duplicate_consecutive_confirmed, duplicate_of_export_id,
            file_sha256, file_size, file_blob, status, calculo_id, draft_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'GENERATED', ?, ?)
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
                ["duplicate_consecutive"] if prior_id is not None and confirm_duplicate_consecutive else []
            ),
            1 if prior_id is not None and confirm_duplicate_consecutive else 0,
            prior_id if prior_id is not None and confirm_duplicate_consecutive else None,
            digest,
            len(file_bytes),
            file_bytes,
            calculo_id,
            draft_id,
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
                is_manual_beneficiary, warnings_json, user_decision_json, calculo_row_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                item.get("calculo_row_id"),
            ),
        )
    return export_id


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
    calculo_id: int | None = None,
    draft_id: int | None = None,
    conn: sqlite3.Connection | None = None,
    commit: bool = True,
) -> ExportResult:
    """Generate .pag export.

    When ``conn`` is provided, the caller owns the transaction: this function will
    not commit/close. Pass ``commit=False`` with an external connection for atomic
    draft+export workflows.
    """
    now = _now()
    auto_date, _display = resolve_layout_date_monterrey(client_layout_date=layout_date)
    used_date = auto_date
    consecutive = normalize_consecutive(consecutive)

    owns_conn = conn is None
    if owns_conn:
        conn = connect(db_path)
    assert conn is not None
    try:
        ensure_banorte_tables(conn)
        rebuilt, item_rows, manual_count, aliases_used, recommendations_accepted = _build_payments_and_items(
            conn, draft_rows, confirm_manuals=confirm_manuals
        )
        prior = conn.execute(
            "SELECT id FROM nomina_banorte_exports WHERE layout_date=? AND consecutive=? ORDER BY id DESC LIMIT 1",
            (used_date, consecutive),
        ).fetchone()
        if prior is not None and not confirm_duplicate_consecutive:
            raise ExportBlockedError(
                "duplicate_consecutive_confirmation_required",
                prior_export_id=int(prior["id"]),
            )

        file_bytes = build_pag_file(layout_date=used_date, consecutive=consecutive, payments=rebuilt)
        digest = sha256_hex(file_bytes)
        filename = build_filename(consecutive)
        export_id = _insert_export(
            conn,
            user=user,
            now=now,
            used_date=used_date,
            auto_date=auto_date,
            consecutive=consecutive,
            filename=filename,
            file_bytes=file_bytes,
            digest=digest,
            item_rows=item_rows,
            manual_count=manual_count,
            aliases_used=aliases_used,
            recommendations_accepted=recommendations_accepted,
            capture_origin=capture_origin,
            confirm_date_override=confirm_date_override,
            layout_date=layout_date,
            prior_id=int(prior["id"]) if prior is not None else None,
            confirm_duplicate_consecutive=confirm_duplicate_consecutive,
            calculo_id=calculo_id,
            draft_id=draft_id,
        )
        if owns_conn and commit:
            conn.commit()
        total_cents = sum(r["amount_cents"] for r in item_rows)
        return ExportResult(export_id, filename, file_bytes, digest, total_cents, len(item_rows))
    except Exception:
        if owns_conn:
            conn.rollback()
        raise
    finally:
        if owns_conn:
            conn.close()


def _export_result_from_row(conn: sqlite3.Connection, export_id: int) -> ExportResult:
    row = conn.execute(
        "SELECT id, filename, file_blob, file_sha256, total_cents, payment_count FROM nomina_banorte_exports WHERE id=?",
        (int(export_id),),
    ).fetchone()
    if row is None:
        raise KeyError("export_not_found")
    return ExportResult(
        int(row["id"]),
        str(row["filename"]),
        bytes(row["file_blob"]),
        str(row["file_sha256"]),
        int(row["total_cents"]),
        int(row["payment_count"]),
    )


def generate_from_persistent_draft(
    db_path: str,
    user: str,
    draft_id: int,
    *,
    expected_revision: int,
    consecutive: str,
    layout_date: str | None = None,
    confirm_duplicate_consecutive: bool = False,
    confirm_manuals: bool = False,
    confirm_date_override: bool = False,
) -> ExportResult:
    """Idempotent generate bound to a persisted draft (single transaction)."""
    # 1) Existing export for draft → return immediately (before revision checks)
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        existing = conn.execute(
            "SELECT id FROM nomina_banorte_exports WHERE draft_id=?",
            (int(draft_id),),
        ).fetchone()
        if existing is not None:
            return _export_result_from_row(conn, int(existing["id"]))
    finally:
        conn.close()

    draft = get_draft(db_path, draft_id)
    if draft is None:
        raise KeyError("draft_not_found")

    # Drift against origin (read-only helpers use own connections)
    try:
        check_calculo_origin_drift(db_path, draft)
    except DriftError as exc:
        raise ExportBlockedError(exc.code) from exc

    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        conn.execute("BEGIN IMMEDIATE")
        # re-check idempotency inside lock
        existing = conn.execute(
            "SELECT id FROM nomina_banorte_exports WHERE draft_id=?",
            (int(draft_id),),
        ).fetchone()
        if existing is not None:
            conn.commit()
            return _export_result_from_row(conn, int(existing["id"]))

        row = conn.execute(
            "SELECT * FROM nomina_banorte_export_drafts WHERE id=?",
            (int(draft_id),),
        ).fetchone()
        if row is None:
            raise KeyError("draft_not_found")
        if row["status"] != "OPEN":
            raise ExportBlockedError("draft_not_open")
        if int(row["revision"]) != int(expected_revision):
            raise DraftStaleError(int(draft_id), int(row["revision"]))

        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM nomina_banorte_export_draft_rows WHERE draft_id=? ORDER BY position",
            (int(draft_id),),
        ).fetchall()]
        for r in rows:
            r["warnings"] = json.loads(r.get("warnings_json") or "[]")
            r["user_decision"] = json.loads(r.get("user_decision_json") or "{}")

        blocked = check_beneficiary_snapshots(conn, rows)
        if blocked:
            raise ExportBlockedError("rows_require_review", blocked)

        included = [r for r in rows if int(r.get("included") or 0) == 1]
        rec = compute_reconciliation(rows)
        payment_rows: list[DraftPaymentRow] = []
        for r in included:
            cents = int(r["amount_final_cents"])
            amount_raw = f"{Decimal(cents) / Decimal(100):.2f}"
            payment_rows.append(
                DraftPaymentRow(
                    position=int(r["position"]),
                    nombre_recibido=str(r["nombre_recibido"]),
                    beneficiary_id=int(r["beneficiary_id"]),
                    amount_raw=amount_raw,
                    match_kind=str(r.get("match_kind") or "MANUAL_SELECT"),
                    alias_id=int(r["alias_id"]) if r.get("alias_id") else None,
                    client_employee_number=r.get("employee_number_snapshot"),
                    client_account_number=r.get("account_number_snapshot"),
                    warnings=r.get("warnings") or [],
                    user_decision=r.get("user_decision") or {},
                    calculo_row_id=int(r["calculo_row_id"]) if r.get("calculo_row_id") else None,
                )
            )
        if rec.total_final_cents != sum(int(r["amount_final_cents"]) for r in included):
            raise ExportBlockedError("reconciliation_mismatch")

        origin = str(row["origin_kind"])
        capture_origin = (
            "CALCULO_RUN"
            if origin == "CALCULO_RUN"
            else "EXCEL_NOMINA"
            if origin == "EXCEL_NOMINA"
            else "MANUAL_CAPTURE"
        )
        layout_date, layout_date_display = resolve_layout_date_monterrey(client_layout_date=layout_date)
        consecutive_norm = normalize_consecutive(consecutive)
        result = generate_export(
            db_path,
            user,
            payment_rows,
            consecutive=consecutive_norm,
            layout_date=layout_date,
            confirm_duplicate_consecutive=confirm_duplicate_consecutive,
            confirm_manuals=confirm_manuals,
            confirm_date_override=False,
            capture_origin=capture_origin,
            calculo_id=int(row["calculo_id"]) if row["calculo_id"] is not None else None,
            draft_id=int(draft_id),
            conn=conn,
            commit=False,
        )
        result.layout_date = layout_date
        result.layout_date_display = layout_date_display
        if result.total_cents != rec.total_final_cents:
            raise ExportBlockedError("pag_total_mismatch")

        now = _now()
        cur = conn.execute(
            """
            UPDATE nomina_banorte_export_drafts
            SET status='GENERATED', updated_by=?, updated_at=?, revision = revision + 1
            WHERE id=? AND revision=? AND status='OPEN'
            """,
            (user, now, int(draft_id), int(expected_revision)),
        )
        if cur.rowcount != 1:
            raise DraftStaleError(int(draft_id), int(expected_revision))
        conn.commit()
        return result
    except sqlite3.IntegrityError:
        conn.rollback()
        # unique draft_id race — return winner
        winner = conn.execute(
            "SELECT id FROM nomina_banorte_exports WHERE draft_id=?",
            (int(draft_id),),
        ).fetchone()
        if winner is not None:
            return _export_result_from_row(conn, int(winner["id"]))
        raise
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
