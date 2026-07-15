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


def compute_reconciliation(rows: list[dict[str, Any]]) -> Reconciliation:
    original_n = len(rows)
    included = [r for r in rows if int(r.get("included") or 0) == 1]
    excluded = [r for r in rows if int(r.get("included") or 0) == 0]
    total_orig = sum(int(r.get("amount_original_cents") or 0) for r in rows)
    total_final = sum(int(r.get("amount_final_cents") or 0) for r in included)
    adj_pos = 0
    adj_neg = 0
    for r in included:
        delta = int(r.get("amount_final_cents") or 0) - int(r.get("amount_original_cents") or 0)
        if delta > 0:
            adj_pos += delta
        elif delta < 0:
            adj_neg += -delta
    return Reconciliation(
        original_row_count=original_n,
        included_count=len(included),
        excluded_count=len(excluded),
        total_original_cents=total_orig,
        adjustments_positive_cents=adj_pos,
        adjustments_negative_cents=adj_neg,
        total_final_cents=total_final,
        difference_cents=total_final - total_orig,
        payment_count=len(included),
    )


def _row_dict(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["warnings"] = json.loads(d.get("warnings_json") or "[]")
    d["user_decision"] = json.loads(d.get("user_decision_json") or "{}")
    return d


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
        conn.execute("DELETE FROM nomina_banorte_export_draft_rows WHERE draft_id=?", (int(draft_id),))
        for i, r in enumerate(rows, start=1):
            included = int(r.get("included") or 0)
            final_cents = int(r.get("amount_final_cents") or 0)
            if included and final_cents <= 0:
                raise ValueError("included_requires_positive_final")
            conn.execute(
                """
                INSERT INTO nomina_banorte_export_draft_rows (
                    draft_id, position, calculo_row_id, nombre_recibido, nss_snapshot, banco_snapshot,
                    beneficiary_id, employee_number_snapshot, account_number_snapshot,
                    amount_original_cents, amount_final_cents, included, match_kind, alias_id,
                    row_state, warnings_json, user_decision_json, excluded_at, excluded_by
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(draft_id),
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
        _bump_or_stale(conn, draft_id, expected_revision, user)
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
