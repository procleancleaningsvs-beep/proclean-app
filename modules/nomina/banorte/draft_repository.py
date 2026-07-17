"""Persistent Banorte export drafts with optimistic concurrency."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from modules.nomina.banorte.calculo_adapter import AdapterResult, origin_hash_for_manual_capture
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables

TZ = ZoneInfo("America/Monterrey")


class DraftStaleError(Exception):
    def __init__(self, draft_id: int, current_revision: int):
        super().__init__("draft_stale")
        self.draft_id = draft_id
        self.current_revision = current_revision
        self.code = "draft_stale"


class DraftConflictError(Exception):
    def __init__(self, code: str, **extra: Any):
        super().__init__(code)
        self.code = code
        self.extra = extra


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


@dataclass
class Reconciliation:
    original_row_count: int
    included_count: int
    excluded_count: int
    total_original_cents: int
    adjustments_positive_cents: int
    adjustments_negative_cents: int
    total_final_cents: int
    difference_cents: int
    payment_count: int
    original_count: int = 0
    original_total_cents: int = 0
    manual_added_count: int = 0
    manual_added_total_cents: int = 0
    included_total_cents: int = 0


def _is_manual_add(row: dict[str, Any]) -> bool:
    return str(row.get("row_origin") or "") == "MANUAL_ADD"


def compute_reconciliation(rows: list[dict[str, Any]]) -> Reconciliation:
    source_rows = [r for r in rows if not _is_manual_add(r)]
    manual_active = [
        r for r in rows if _is_manual_add(r) and int(r.get("included") or 0) == 1
    ]
    included = [r for r in rows if int(r.get("included") or 0) == 1]
    excluded = [r for r in rows if int(r.get("included") or 0) == 0]
    total_orig = sum(int(r.get("amount_original_cents") or 0) for r in source_rows)
    total_final = sum(int(r.get("amount_final_cents") or 0) for r in included)
    manual_total = sum(int(r.get("amount_final_cents") or 0) for r in manual_active)
    adj_pos = 0
    adj_neg = 0
    for r in included:
        if _is_manual_add(r):
            continue
        delta = int(r.get("amount_final_cents") or 0) - int(r.get("amount_original_cents") or 0)
        if delta > 0:
            adj_pos += delta
        elif delta < 0:
            adj_neg += -delta
    return Reconciliation(
        original_row_count=len(source_rows),
        included_count=len(included),
        excluded_count=len(excluded),
        total_original_cents=total_orig,
        adjustments_positive_cents=adj_pos,
        adjustments_negative_cents=adj_neg,
        total_final_cents=total_final,
        difference_cents=total_final - total_orig,
        payment_count=len(included),
        original_count=len(source_rows),
        original_total_cents=total_orig,
        manual_added_count=len(manual_active),
        manual_added_total_cents=manual_total,
        included_total_cents=total_final,
    )


def _row_dict(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["warnings"] = json.loads(d.get("warnings_json") or "[]")
    d["user_decision"] = json.loads(d.get("user_decision_json") or "{}")
    return d


def _undo_available(conn: sqlite3.Connection, draft_id: int) -> bool:
    row = conn.execute(
        """
        SELECT e.id
        FROM nomina_banorte_draft_events AS e
        WHERE e.draft_id = ?
          AND e.reversible = 1
          AND e.action IN ('APPLY_BENEFICIARY', 'APPLY_AMOUNT', 'EXCLUDE_ROW', 'ADD_ROW')
          AND NOT EXISTS (
            SELECT 1
            FROM nomina_banorte_draft_events AS u
            WHERE u.action = 'UNDO'
              AND u.target_event_id = e.id
          )
        ORDER BY e.id DESC
        LIMIT 1
        """,
        (int(draft_id),),
    ).fetchone()
    return row is not None


def get_draft(db_path: str, draft_id: int) -> dict[str, Any] | None:
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        d = conn.execute(
            "SELECT * FROM nomina_banorte_export_drafts WHERE id=?",
            (int(draft_id),),
        ).fetchone()
        if d is None:
            return None
        rows = conn.execute(
            """
            SELECT * FROM nomina_banorte_export_draft_rows
            WHERE draft_id=? ORDER BY position ASC
            """,
            (int(draft_id),),
        ).fetchall()
        payload = dict(d)
        payload["rows"] = [_row_dict(r) for r in rows]
        payload["reconciliation"] = compute_reconciliation(payload["rows"]).__dict__
        payload["undo_available"] = _undo_available(conn, int(draft_id))
        return payload
    finally:
        conn.close()


def find_open_draft_for_calculo(db_path: str, user: str, calculo_id: int) -> dict[str, Any] | None:
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        row = conn.execute(
            """
            SELECT id FROM nomina_banorte_export_drafts
            WHERE created_by=? AND calculo_id=? AND status='OPEN' AND origin_kind='CALCULO_RUN'
            """,
            (user, int(calculo_id)),
        ).fetchone()
        if row is None:
            return None
        return get_draft(db_path, int(row["id"]))
    finally:
        conn.close()


def find_open_manual_draft(db_path: str, user: str) -> dict[str, Any] | None:
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        row = conn.execute(
            """
            SELECT id FROM nomina_banorte_export_drafts
            WHERE created_by=? AND status='OPEN' AND origin_kind='MANUAL_CAPTURE'
            ORDER BY id DESC LIMIT 1
            """,
            (user,),
        ).fetchone()
        if row is None:
            return None
        return get_draft(db_path, int(row["id"]))
    finally:
        conn.close()


def _insert_adapter_rows(conn: sqlite3.Connection, draft_id: int, adapted: AdapterResult) -> None:
    for i, ar in enumerate(adapted.rows, start=1):
        conn.execute(
            """
            INSERT INTO nomina_banorte_export_draft_rows (
                draft_id, position, calculo_row_id, nombre_recibido, nss_snapshot, banco_snapshot,
                beneficiary_id, employee_number_snapshot, account_number_snapshot,
                amount_original_cents, amount_final_cents, included, match_kind, alias_id,
                row_state, warnings_json, user_decision_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                draft_id,
                i,
                ar.calculo_row_id,
                ar.nombre_recibido,
                ar.nss_snapshot,
                ar.banco_snapshot,
                None,
                ar.employee_number_snapshot,
                ar.account_number_snapshot,
                ar.amount_original_cents,
                ar.amount_final_cents if ar.included else 0,
                ar.included,
                ar.match_kind,
                None,
                ar.row_state if ar.included else "EXCLUDED",
                json.dumps(ar.warnings, ensure_ascii=False),
                json.dumps({}, ensure_ascii=False),
            ),
        )


def create_draft_from_adapter(
    db_path: str,
    user: str,
    adapted: AdapterResult,
) -> dict[str, Any]:
    existing = find_open_draft_for_calculo(db_path, user, adapted.calculo_id)
    if existing is not None:
        return existing
    now = _now()
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        cur = conn.execute(
            """
            INSERT INTO nomina_banorte_export_drafts (
                created_by, updated_by, created_at, updated_at, origin_kind, calculo_id,
                origin_updated_at, origin_hash, status, revision
            ) VALUES (?,?,?,?, 'CALCULO_RUN', ?,?,?, 'OPEN', 1)
            """,
            (
                user,
                user,
                now,
                now,
                adapted.calculo_id,
                adapted.origin_updated_at,
                adapted.origin_hash,
            ),
        )
        draft_id = int(cur.lastrowid)
        _insert_adapter_rows(conn, draft_id, adapted)
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        # concurrent create — reopen winner
        existing = find_open_draft_for_calculo(db_path, user, adapted.calculo_id)
        if existing is not None:
            return existing
        raise
    finally:
        conn.close()
    return get_draft(db_path, draft_id)  # type: ignore[return-value]


def create_manual_draft_shell(
    db_path: str,
    user: str,
    *,
    names_text: str,
    amounts_text: str,
    force_new: bool = False,
) -> dict[str, Any]:
    """Create MANUAL_CAPTURE draft shell. Does not auto-abandon existing OPEN manual."""
    existing = find_open_manual_draft(db_path, user)
    if existing is not None and not force_new:
        return {
            "needs_choice": True,
            "existing_draft_id": existing["id"],
            "existing_revision": existing["revision"],
            "draft": existing,
        }
    if existing is not None and force_new:
        raise DraftConflictError("manual_open_exists", draft_id=existing["id"])
    now = _now()
    oh = origin_hash_for_manual_capture(names_text, amounts_text)
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        cur = conn.execute(
            """
            INSERT INTO nomina_banorte_export_drafts (
                created_by, updated_by, created_at, updated_at, origin_kind, calculo_id,
                origin_updated_at, origin_hash, status, revision
            ) VALUES (?,?,?,?, 'MANUAL_CAPTURE', NULL, NULL, ?, 'OPEN', 1)
            """,
            (user, user, now, now, oh),
        )
        draft_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()
    return {"needs_choice": False, "draft": get_draft(db_path, draft_id)}


def _bump_or_stale(
    conn: sqlite3.Connection,
    draft_id: int,
    expected_revision: int,
    user: str,
    *,
    require_status: str | None = "OPEN",
) -> None:
    now = _now()
    clauses = ["id=?", "revision=?"]
    params: list[Any] = [user, now, int(draft_id), int(expected_revision)]
    if require_status:
        clauses.append("status=?")
        params.append(require_status)
    sql = f"""
        UPDATE nomina_banorte_export_drafts
        SET updated_by=?, updated_at=?, revision = revision + 1
        WHERE {' AND '.join(clauses)}
    """
    cur = conn.execute(sql, params)
    if cur.rowcount != 1:
        row = conn.execute(
            "SELECT revision, status FROM nomina_banorte_export_drafts WHERE id=?",
            (int(draft_id),),
        ).fetchone()
        rev = int(row["revision"]) if row else expected_revision
        raise DraftStaleError(int(draft_id), rev)


def save_draft_rows(
    db_path: str,
    draft_id: int,
    user: str,
    expected_revision: int,
    rows: list[dict[str, Any]],
    *,
    consecutive_pref: str | None = None,
) -> dict[str, Any]:
    """Persist rows preserving row ids (UPDATE existing; INSERT only truly new rows)."""
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        conn.execute("BEGIN IMMEDIATE")
        _bump_or_stale(conn, draft_id, expected_revision, user)
        if consecutive_pref is not None:
            try:
                from modules.nomina.banorte.export_service import normalize_consecutive

                pref = normalize_consecutive(consecutive_pref)
            except Exception:
                pref = consecutive_pref
            conn.execute(
                "UPDATE nomina_banorte_export_drafts SET consecutive_pref=? WHERE id=?",
                (pref, int(draft_id)),
            )
        existing = {
            int(r["id"]): dict(r)
            for r in conn.execute(
                "SELECT id FROM nomina_banorte_export_draft_rows WHERE draft_id=?",
                (int(draft_id),),
            )
        }
        seen: set[int] = set()
        for i, r in enumerate(rows, start=1):
            included = int(r.get("included") or 0)
            final_cents = int(r.get("amount_final_cents") or 0)
            if included and final_cents <= 0:
                raise ValueError("included_requires_positive_final")
            rid = r.get("id")
            params = (
                int(r.get("position") or i),
                r.get("calculo_row_id"),
                str(r.get("nombre_recibido") or ""),
                r.get("nss_snapshot"),
                r.get("banco_snapshot"),
                r.get("beneficiary_id"),
                r.get("employee_number_snapshot"),
                r.get("account_number_snapshot"),
                int(r.get("amount_original_cents") or 0),
                final_cents if included else max(0, final_cents),
                included,
                str(r.get("match_kind") or "NONE"),
                r.get("alias_id"),
                str(r.get("row_state") or ("OK" if included else "EXCLUDED")),
                json.dumps(r.get("warnings") or [], ensure_ascii=False),
                json.dumps(r.get("user_decision") or {}, ensure_ascii=False),
                r.get("excluded_at"),
                r.get("excluded_by"),
            )
            if rid is not None and int(rid) in existing:
                seen.add(int(rid))
                conn.execute(
                    """
                    UPDATE nomina_banorte_export_draft_rows SET
                        position=?, calculo_row_id=?, nombre_recibido=?, nss_snapshot=?, banco_snapshot=?,
                        beneficiary_id=?, employee_number_snapshot=?, account_number_snapshot=?,
                        amount_original_cents=?, amount_final_cents=?, included=?, match_kind=?, alias_id=?,
                        row_state=?, warnings_json=?, user_decision_json=?, excluded_at=?, excluded_by=?
                    WHERE id=? AND draft_id=?
                    """,
                    (*params, int(rid), int(draft_id)),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO nomina_banorte_export_draft_rows (
                        draft_id, position, calculo_row_id, nombre_recibido, nss_snapshot, banco_snapshot,
                        beneficiary_id, employee_number_snapshot, account_number_snapshot,
                        amount_original_cents, amount_final_cents, included, match_kind, alias_id,
                        row_state, warnings_json, user_decision_json, excluded_at, excluded_by
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (int(draft_id), *params),
                )
                seen.add(int(cur.lastrowid))
        # Soft-exclude rows omitted from payload (never DELETE — preserves event FK)
        for old_id in existing:
            if old_id not in seen:
                now = _now()
                conn.execute(
                    """
                    UPDATE nomina_banorte_export_draft_rows
                    SET included=0, row_state='EXCLUDED', excluded_at=COALESCE(excluded_at, ?),
                        excluded_by=COALESCE(excluded_by, ?)
                    WHERE id=? AND draft_id=?
                    """,
                    (now, user, old_id, int(draft_id)),
                )
        conn.commit()
    except DraftStaleError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_draft(db_path, draft_id)  # type: ignore[return-value]


def reorder_draft_rows(
    db_path: str,
    draft_id: int,
    user: str,
    expected_revision: int,
    ordered_row_ids: list[int],
) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        conn.execute("BEGIN IMMEDIATE")
        _bump_or_stale(conn, draft_id, expected_revision, user)
        existing = conn.execute(
            "SELECT id FROM nomina_banorte_export_draft_rows WHERE draft_id=? ORDER BY position",
            (int(draft_id),),
        ).fetchall()
        existing_ids = [int(r["id"]) for r in existing]
        if sorted(existing_ids) != sorted(int(x) for x in ordered_row_ids):
            raise ValueError("reorder_id_mismatch")
        # temporary negative positions
        for i, rid in enumerate(ordered_row_ids, start=1):
            conn.execute(
                "UPDATE nomina_banorte_export_draft_rows SET position=? WHERE id=? AND draft_id=?",
                (-i, int(rid), int(draft_id)),
            )
        for i, rid in enumerate(ordered_row_ids, start=1):
            conn.execute(
                "UPDATE nomina_banorte_export_draft_rows SET position=? WHERE id=? AND draft_id=?",
                (i, int(rid), int(draft_id)),
            )
        conn.commit()
    except DraftStaleError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_draft(db_path, draft_id)  # type: ignore[return-value]


def abandon_draft(
    db_path: str,
    draft_id: int,
    user: str,
    expected_revision: int,
) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        conn.execute("BEGIN IMMEDIATE")
        now = _now()
        cur = conn.execute(
            """
            UPDATE nomina_banorte_export_drafts
            SET status='ABANDONED', updated_by=?, updated_at=?, revision = revision + 1
            WHERE id=? AND revision=? AND status='OPEN'
            """,
            (user, now, int(draft_id), int(expected_revision)),
        )
        if cur.rowcount != 1:
            row = conn.execute(
                "SELECT revision FROM nomina_banorte_export_drafts WHERE id=?",
                (int(draft_id),),
            ).fetchone()
            raise DraftStaleError(int(draft_id), int(row["revision"]) if row else expected_revision)
        conn.commit()
    except DraftStaleError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_draft(db_path, draft_id)  # type: ignore[return-value]


def _insert_draft_event(
    conn: sqlite3.Connection,
    *,
    draft_id: int,
    row_id: int,
    action: str,
    reversible: int,
    target_event_id: int | None,
    before: dict[str, Any],
    after: dict[str, Any],
    revision_before: int,
    revision_after: int,
    user: str,
) -> None:
    conn.execute(
        """
        INSERT INTO nomina_banorte_draft_events (
            draft_id, row_id, action, reversible, target_event_id,
            before_nombre_recibido, after_nombre_recibido,
            before_beneficiary_id, after_beneficiary_id,
            before_amount_final_cents, after_amount_final_cents,
            before_included, after_included,
            before_row_state, after_row_state,
            before_excluded_at, after_excluded_at,
            before_excluded_by, after_excluded_by,
            revision_before, revision_after, created_by, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(draft_id),
            int(row_id),
            action,
            int(reversible),
            target_event_id,
            before.get("nombre_recibido"),
            after.get("nombre_recibido"),
            before.get("beneficiary_id"),
            after.get("beneficiary_id"),
            before.get("amount_final_cents"),
            after.get("amount_final_cents"),
            before.get("included"),
            after.get("included"),
            before.get("row_state"),
            after.get("row_state"),
            before.get("excluded_at"),
            after.get("excluded_at"),
            before.get("excluded_by"),
            after.get("excluded_by"),
            int(revision_before),
            int(revision_after),
            user,
            _now(),
        ),
    )


def exclude_draft_row(
    db_path: str,
    draft_id: int,
    row_id: int,
    user: str,
    expected_revision: int,
) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        conn.execute("BEGIN IMMEDIATE")
        before_row = conn.execute(
            "SELECT * FROM nomina_banorte_export_draft_rows WHERE id=? AND draft_id=?",
            (int(row_id), int(draft_id)),
        ).fetchone()
        if before_row is None:
            raise ValueError("row_not_found")
        before = dict(before_row)
        _bump_or_stale(conn, draft_id, expected_revision, user)
        now = _now()
        cur = conn.execute(
            """
            UPDATE nomina_banorte_export_draft_rows
            SET included=0, row_state='EXCLUDED', excluded_at=?, excluded_by=?
            WHERE id=? AND draft_id=?
            """,
            (now, user, int(row_id), int(draft_id)),
        )
        if cur.rowcount != 1:
            raise ValueError("row_not_found")
        after_row = conn.execute(
            "SELECT * FROM nomina_banorte_export_draft_rows WHERE id=? AND draft_id=?",
            (int(row_id), int(draft_id)),
        ).fetchone()
        _insert_draft_event(
            conn,
            draft_id=int(draft_id),
            row_id=int(row_id),
            action="EXCLUDE_ROW",
            reversible=1,
            target_event_id=None,
            before=before,
            after=dict(after_row),
            revision_before=int(expected_revision),
            revision_after=int(expected_revision) + 1,
            user=user,
        )
        conn.commit()
    except DraftStaleError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_draft(db_path, draft_id)  # type: ignore[return-value]


def apply_draft_row(
    db_path: str,
    draft_id: int,
    row_id: int,
    user: str,
    expected_revision: int,
    *,
    beneficiary_id: int | None = None,
    nombre_recibido: str | None = None,
    amount_final: str | None = None,
) -> dict[str, Any]:
    """Authoritative per-row mutation: reload beneficiary from SQLite; bump revision."""
    from modules.nomina.banorte.money import parse_money, to_cents
    from modules.nomina.banorte.prepare_service import compute_row_state_from_beneficiary

    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        conn.execute("BEGIN IMMEDIATE")
        draft_meta = conn.execute(
            "SELECT origin_kind FROM nomina_banorte_export_drafts WHERE id=?",
            (int(draft_id),),
        ).fetchone()
        if draft_meta is None:
            raise ValueError("draft_not_found")
        origin_kind = str(draft_meta["origin_kind"])
        row = conn.execute(
            "SELECT * FROM nomina_banorte_export_draft_rows WHERE id=? AND draft_id=?",
            (int(row_id), int(draft_id)),
        ).fetchone()
        if row is None:
            raise ValueError("row_not_found")
        before = dict(row)
        benef_touch = beneficiary_id is not None or nombre_recibido is not None
        amount_touch = amount_final is not None
        action = "APPLY_BENEFICIARY" if benef_touch else "APPLY_AMOUNT"
        if benef_touch and amount_touch:
            # Prefer beneficiary when both present (autocomplete path sends both).
            action = "APPLY_BENEFICIARY"

        _bump_or_stale(conn, draft_id, expected_revision, user)

        cents = int(row["amount_final_cents"] or 0)
        if amount_final is not None:
            money = parse_money(str(amount_final))
            if money.error == "zero":
                cents = 0
            elif money.ok and money.amount is not None:
                cents = to_cents(money.amount)
                if cents < 0:
                    raise ValueError("amount_invalid")
            else:
                raise ValueError("amount_invalid")

        bid = int(beneficiary_id) if beneficiary_id is not None else (
            int(row["beneficiary_id"]) if row["beneficiary_id"] is not None else None
        )
        ben = None
        if bid is not None:
            ben_row = conn.execute(
                "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
                (bid,),
            ).fetchone()
            if ben_row is None:
                raise ValueError("beneficiary_not_found")
            ben = dict(ben_row)

        state = compute_row_state_from_beneficiary(
            amount_final_cents=cents,
            beneficiary=ben,
            origin_kind=origin_kind,
            banco_snapshot=row["banco_snapshot"],
        )
        nombre = (
            str(nombre_recibido)
            if nombre_recibido is not None
            else (str(ben["nombre_original"]) if ben else str(row["nombre_recibido"] or ""))
        )
        warnings = list(state.get("warnings") or [])
        emp = state.get("employee_number_snapshot") or row["employee_number_snapshot"]
        acct = state.get("account_number_snapshot") or row["account_number_snapshot"]
        if ben:
            emp = ben["employee_number_effective"]
            acct = ben["account_number"]
        conn.execute(
            """
            UPDATE nomina_banorte_export_draft_rows
            SET nombre_recibido=?,
                beneficiary_id=?,
                employee_number_snapshot=?,
                account_number_snapshot=?,
                amount_final_cents=?,
                included=?,
                row_state=?,
                warnings_json=?,
                match_kind=?,
                excluded_at=NULL,
                excluded_by=NULL
            WHERE id=? AND draft_id=?
            """,
            (
                nombre,
                bid,
                emp,
                acct,
                int(state["amount_final_cents"]),
                int(state["included"]),
                str(state["row_state"]),
                json.dumps(warnings, ensure_ascii=False),
                "EXACT" if bid and int(state["included"]) == 1 else (row["match_kind"] or "NONE"),
                int(row_id),
                int(draft_id),
            ),
        )
        after_row = conn.execute(
            "SELECT * FROM nomina_banorte_export_draft_rows WHERE id=? AND draft_id=?",
            (int(row_id), int(draft_id)),
        ).fetchone()
        _insert_draft_event(
            conn,
            draft_id=int(draft_id),
            row_id=int(row_id),
            action=action,
            reversible=1,
            target_event_id=None,
            before=before,
            after=dict(after_row),
            revision_before=int(expected_revision),
            revision_after=int(expected_revision) + 1,
            user=user,
        )
        conn.commit()
    except DraftStaleError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_draft(db_path, draft_id)  # type: ignore[return-value]


def add_draft_payment(
    db_path: str,
    draft_id: int,
    user: str,
    expected_revision: int,
    *,
    beneficiary_id: int,
    amount_final: str,
    request_nonce: str | None = None,
    confirm_duplicate_beneficiary: bool = False,
) -> dict[str, Any]:
    """Append a MANUAL_ADD payment row (ADD_ROW event) from an active beneficiary."""
    from modules.nomina.banorte.money import parse_money, to_cents
    from modules.nomina.banorte.prepare_service import compute_row_state_from_beneficiary

    money = parse_money(str(amount_final))
    if money.error == "zero" or (money.ok and money.amount is not None and to_cents(money.amount) <= 0):
        raise ValueError("amount_must_be_positive")
    if not money.ok or money.amount is None:
        raise ValueError("amount_invalid")
    cents = to_cents(money.amount)
    if cents <= 0:
        raise ValueError("amount_must_be_positive")
    nonce = (request_nonce or "").strip() or None

    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        conn.execute("BEGIN IMMEDIATE")
        if nonce:
            prior = conn.execute(
                """
                SELECT result_revision, result_row_id FROM nomina_banorte_draft_request_nonces
                WHERE draft_id=? AND request_nonce=?
                """,
                (int(draft_id), nonce),
            ).fetchone()
            if prior is not None:
                conn.commit()
                out = get_draft(db_path, int(draft_id))
                if out is None:
                    raise ValueError("draft_not_found")
                return out

        draft_meta = conn.execute(
            "SELECT origin_kind, status, revision FROM nomina_banorte_export_drafts WHERE id=?",
            (int(draft_id),),
        ).fetchone()
        if draft_meta is None:
            raise ValueError("draft_not_found")
        if draft_meta["status"] != "OPEN":
            raise ValueError("draft_not_open")
        origin_kind = str(draft_meta["origin_kind"])
        ben_row = conn.execute(
            "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
            (int(beneficiary_id),),
        ).fetchone()
        if ben_row is None:
            raise ValueError("beneficiary_not_found")
        ben = dict(ben_row)
        if ben.get("record_status") != "ACTIVO":
            raise ValueError("beneficiary_not_active")
        state = compute_row_state_from_beneficiary(
            amount_final_cents=cents,
            beneficiary=ben,
            origin_kind="MANUAL_CAPTURE" if origin_kind == "MANUAL_CAPTURE" else origin_kind,
            banco_snapshot="Banorte",
        )
        if int(state.get("included") or 0) != 1:
            raise ValueError("beneficiary_not_usable")

        dup = conn.execute(
            """
            SELECT id FROM nomina_banorte_export_draft_rows
            WHERE draft_id=? AND beneficiary_id=? AND included=1
              AND COALESCE(excluded_at, '') = ''
            LIMIT 1
            """,
            (int(draft_id), int(beneficiary_id)),
        ).fetchone()
        if dup is not None and not confirm_duplicate_beneficiary:
            raise ValueError("duplicate_beneficiary_payment_confirmation_required")

        _bump_or_stale(conn, draft_id, expected_revision, user)
        pos = int(
            conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS p FROM nomina_banorte_export_draft_rows WHERE draft_id=?",
                (int(draft_id),),
            ).fetchone()["p"]
        )
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO nomina_banorte_export_draft_rows (
                draft_id, position, calculo_row_id, nombre_recibido, nss_snapshot, banco_snapshot,
                beneficiary_id, employee_number_snapshot, account_number_snapshot,
                amount_original_cents, amount_final_cents, included, match_kind, alias_id,
                row_state, warnings_json, user_decision_json, excluded_at, excluded_by, row_origin
            ) VALUES (?,?,NULL,?,NULL,?,?,?,?,?,?,1,'EXACT',NULL,?,?,?,NULL,NULL,'MANUAL_ADD')
            """,
            (
                int(draft_id),
                pos,
                str(ben["nombre_original"]),
                "Banorte",
                int(beneficiary_id),
                ben["employee_number_effective"],
                ben["account_number"],
                cents,
                cents,
                str(state["row_state"]),
                json.dumps(state.get("warnings") or [], ensure_ascii=False),
                json.dumps({"source": "MANUAL_ADD"}, ensure_ascii=False),
            ),
        )
        row_id = int(cur.lastrowid)
        after_row = conn.execute(
            "SELECT * FROM nomina_banorte_export_draft_rows WHERE id=?",
            (row_id,),
        ).fetchone()
        before = {
            "nombre_recibido": str(ben["nombre_original"]),
            "beneficiary_id": None,
            "amount_final_cents": 0,
            "included": 0,
            "row_state": "EXCLUDED",
            "excluded_at": now,
            "excluded_by": user,
        }
        _insert_draft_event(
            conn,
            draft_id=int(draft_id),
            row_id=row_id,
            action="ADD_ROW",
            reversible=1,
            target_event_id=None,
            before=before,
            after=dict(after_row),
            revision_before=int(expected_revision),
            revision_after=int(expected_revision) + 1,
            user=user,
        )
        if nonce:
            conn.execute(
                """
                INSERT INTO nomina_banorte_draft_request_nonces (
                    draft_id, request_nonce, result_draft_id, result_revision, result_row_id, created_at
                ) VALUES (?,?,?,?,?,?)
                """,
                (int(draft_id), nonce, int(draft_id), int(expected_revision) + 1, row_id, now),
            )
        conn.commit()
    except DraftStaleError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_draft(db_path, draft_id)  # type: ignore[return-value]


def undo_last_draft_mutation(
    db_path: str,
    draft_id: int,
    user: str,
    expected_revision: int,
) -> dict[str, Any]:
    """Undo last reversible draft event (append-only UNDO with target_event_id)."""
    from modules.nomina.banorte.prepare_service import compute_row_state_from_beneficiary

    undone_action: str | None = None
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        conn.execute("BEGIN IMMEDIATE")
        target = conn.execute(
            """
            SELECT e.*
            FROM nomina_banorte_draft_events AS e
            WHERE e.draft_id = ?
              AND e.reversible = 1
              AND e.action IN ('APPLY_BENEFICIARY', 'APPLY_AMOUNT', 'EXCLUDE_ROW', 'ADD_ROW')
              AND NOT EXISTS (
                SELECT 1
                FROM nomina_banorte_draft_events AS u
                WHERE u.action = 'UNDO'
                  AND u.target_event_id = e.id
              )
            ORDER BY e.id DESC
            LIMIT 1
            """,
            (int(draft_id),),
        ).fetchone()
        if target is None:
            raise ValueError("nothing_to_undo")
        tgt = dict(target)
        row_id = int(tgt["row_id"])
        before_row = conn.execute(
            "SELECT * FROM nomina_banorte_export_draft_rows WHERE id=? AND draft_id=?",
            (row_id, int(draft_id)),
        ).fetchone()
        if before_row is None:
            raise ValueError("row_not_found")
        current = dict(before_row)
        _bump_or_stale(conn, draft_id, expected_revision, user)

        draft_meta = conn.execute(
            "SELECT origin_kind FROM nomina_banorte_export_drafts WHERE id=?",
            (int(draft_id),),
        ).fetchone()
        origin_kind = str(draft_meta["origin_kind"]) if draft_meta else "MANUAL_CAPTURE"

        nombre = tgt["before_nombre_recibido"]
        bid = tgt["before_beneficiary_id"]
        cents = int(tgt["before_amount_final_cents"] or 0)
        excluded_at = tgt["before_excluded_at"]
        excluded_by = tgt["before_excluded_by"]

        ben = None
        if bid is not None:
            ben_row = conn.execute(
                "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
                (int(bid),),
            ).fetchone()
            if ben_row is not None:
                ben = dict(ben_row)

        if excluded_at is not None:
            # Restore manual exclusion snapshot without forcing OK.
            conn.execute(
                """
                UPDATE nomina_banorte_export_draft_rows
                SET nombre_recibido=?,
                    beneficiary_id=?,
                    amount_final_cents=?,
                    included=?,
                    row_state=?,
                    excluded_at=?,
                    excluded_by=?
                WHERE id=? AND draft_id=?
                """,
                (
                    nombre if nombre is not None else current["nombre_recibido"],
                    bid,
                    cents,
                    int(tgt["before_included"] or 0),
                    str(tgt["before_row_state"] or "EXCLUDED"),
                    excluded_at,
                    excluded_by,
                    row_id,
                    int(draft_id),
                ),
            )
        else:
            state = compute_row_state_from_beneficiary(
                amount_final_cents=cents,
                beneficiary=ben,
                origin_kind=origin_kind,
                banco_snapshot=current.get("banco_snapshot"),
            )
            emp = current.get("employee_number_snapshot")
            acct = current.get("account_number_snapshot")
            if ben:
                emp = ben["employee_number_effective"]
                acct = ben["account_number"]
            conn.execute(
                """
                UPDATE nomina_banorte_export_draft_rows
                SET nombre_recibido=?,
                    beneficiary_id=?,
                    employee_number_snapshot=?,
                    account_number_snapshot=?,
                    amount_final_cents=?,
                    included=?,
                    row_state=?,
                    warnings_json=?,
                    excluded_at=NULL,
                    excluded_by=NULL
                WHERE id=? AND draft_id=?
                """,
                (
                    nombre if nombre is not None else (
                        str(ben["nombre_original"]) if ben else current["nombre_recibido"]
                    ),
                    bid,
                    emp,
                    acct,
                    int(state["amount_final_cents"]),
                    int(state["included"]),
                    str(state["row_state"]),
                    json.dumps(state.get("warnings") or [], ensure_ascii=False),
                    row_id,
                    int(draft_id),
                ),
            )

        after_row = conn.execute(
            "SELECT * FROM nomina_banorte_export_draft_rows WHERE id=? AND draft_id=?",
            (row_id, int(draft_id)),
        ).fetchone()
        _insert_draft_event(
            conn,
            draft_id=int(draft_id),
            row_id=row_id,
            action="UNDO",
            reversible=0,
            target_event_id=int(tgt["id"]),
            before=current,
            after=dict(after_row),
            revision_before=int(expected_revision),
            revision_after=int(expected_revision) + 1,
            user=user,
        )
        undone_action = str(tgt["action"])
        conn.commit()
    except DraftStaleError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    out = get_draft(db_path, draft_id)  # type: ignore[return-value]
    if out is not None:
        out["last_undone_action"] = undone_action
    return out


def restore_last_excluded(
    db_path: str,
    draft_id: int,
    user: str,
    expected_revision: int,
) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        conn.execute("BEGIN IMMEDIATE")
        _bump_or_stale(conn, draft_id, expected_revision, user)
        row = conn.execute(
            """
            SELECT id, beneficiary_id, amount_final_cents
            FROM nomina_banorte_export_draft_rows
            WHERE draft_id=? AND excluded_at IS NOT NULL
            ORDER BY excluded_at DESC LIMIT 1
            """,
            (int(draft_id),),
        ).fetchone()
        if row is None:
            raise ValueError("nothing_to_restore")
        ben = None
        if row["beneficiary_id"] is not None:
            ben_row = conn.execute(
                "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
                (int(row["beneficiary_id"]),),
            ).fetchone()
            if ben_row is not None:
                ben = dict(ben_row)
        draft_meta = conn.execute(
            "SELECT origin_kind FROM nomina_banorte_export_drafts WHERE id=?",
            (int(draft_id),),
        ).fetchone()
        origin_kind = str(draft_meta["origin_kind"]) if draft_meta else "MANUAL_CAPTURE"
        from modules.nomina.banorte.prepare_service import compute_row_state_from_beneficiary

        state = compute_row_state_from_beneficiary(
            amount_final_cents=int(row["amount_final_cents"] or 0),
            beneficiary=ben,
            origin_kind=origin_kind,
            banco_snapshot=None,
        )
        conn.execute(
            """
            UPDATE nomina_banorte_export_draft_rows
            SET included=?, row_state=?, warnings_json=?,
                employee_number_snapshot=COALESCE(?, employee_number_snapshot),
                account_number_snapshot=COALESCE(?, account_number_snapshot),
                excluded_at=NULL, excluded_by=NULL
            WHERE id=? AND draft_id=?
            """,
            (
                int(state["included"]),
                str(state["row_state"]),
                json.dumps(state.get("warnings") or [], ensure_ascii=False),
                state.get("employee_number_snapshot"),
                state.get("account_number_snapshot"),
                int(row["id"]),
                int(draft_id),
            ),
        )
        conn.commit()
    except DraftStaleError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_draft(db_path, draft_id)  # type: ignore[return-value]
