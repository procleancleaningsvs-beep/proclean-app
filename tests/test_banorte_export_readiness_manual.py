"""Regression: resolved manual-capture rows must not block with rows_require_review."""

from __future__ import annotations

import pytest

from modules.nomina.banorte.draft_repository import (
    apply_draft_row,
    create_manual_draft_shell,
    save_draft_rows,
)
from modules.nomina.banorte.export_readiness import evaluate_pag_export_blockers
from modules.nomina.banorte.export_service import (
    ExportBlockedError,
    generate_from_persistent_draft,
)
from modules.nomina.banorte.prepare_service import prepare_draft_rows
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.db import ensure_nomina_tables


def _seed_manual_effective_ben(db: str, *, nombre: str = "JUAN PEREZ") -> int:
    conn = connect(db)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, employee_number_effective, account_number,
            source_kind, validation_status, record_status, manual_effective_from_account,
            imported_at, imported_by, created_at, updated_at
        ) VALUES (?,?,?,?,'ALTA_MANUAL','IMPORTADO_EXITOSO','ACTIVO',1,'t','u','t','t')
        """,
        (nombre, nombre.upper(), "1234567890", "1234567890"),
    )
    conn.commit()
    bid = int(cur.lastrowid)
    conn.close()
    return bid


def _manual_draft_with_incidents(db: str) -> dict:
    _seed_manual_effective_ben(db, nombre="JUAN PEREZ")
    shell = create_manual_draft_shell(
        db, "u", names_text="JUAN PEREZ\nUNKNOWN ONE\nUNKNOWN TWO", amounts_text="100\n200\n300"
    )
    draft = shell["draft"]
    prepared = prepare_draft_rows(
        db,
        [
            {
                "position": i,
                "nombre_recibido": name,
                "amount_original_cents": cents,
                "amount_final_cents": cents,
                "included": 1,
                "match_kind": "NONE",
                "row_state": "OK",
                "warnings": [],
                "user_decision": {},
            }
            for i, (name, cents) in enumerate(
                [("JUAN PEREZ", 10000), ("UNKNOWN ONE", 20000), ("UNKNOWN TWO", 30000)],
                start=1,
            )
        ],
        origin_kind="MANUAL_CAPTURE",
    )
    return save_draft_rows(db, int(draft["id"]), "u", int(draft["revision"]), prepared)


def test_unresolved_manual_effective_auto_match_blocks_before_apply(tmp_path):
    db = str(tmp_path / "pre.db")
    draft = _manual_draft_with_incidents(db)
    matched = next(r for r in draft["rows"] if r["nombre_recibido"] == "JUAN PEREZ")
    assert matched["row_state"] == "NEEDS_REVIEW"
    assert "manual_effective_confirmation_required" in (matched.get("warnings") or [])
    with pytest.raises(ExportBlockedError):
        generate_from_persistent_draft(
            db, "u", int(draft["id"]), expected_revision=int(draft["revision"]), consecutive="01"
        )


def test_apply_all_incidents_allows_generate(tmp_path):
    db = str(tmp_path / "post.db")
    draft = _manual_draft_with_incidents(db)
    conn = connect(db)
    bid = int(conn.execute("SELECT id FROM nomina_banorte_beneficiaries LIMIT 1").fetchone()[0])
    conn.close()

    rev = int(draft["revision"])
    out = draft
    for row in draft["rows"]:
        if row["row_state"] != "OK" or not row.get("beneficiary_id"):
            out = apply_draft_row(
                db,
                int(draft["id"]),
                int(row["id"]),
                "u",
                rev,
                beneficiary_id=bid,
                amount_final=f"{int(row['amount_final_cents']) / 100:.2f}",
            )
            rev = int(out["revision"])

    assert all(r["row_state"] == "OK" for r in out["rows"])
    conn = connect(db)
    ensure_banorte_tables(conn)
    assert evaluate_pag_export_blockers(conn, out["rows"]) == []
    conn.close()

    result = generate_from_persistent_draft(
        db, "u", int(out["id"]), expected_revision=rev, consecutive="01"
    )
    assert result.payment_count == 3
    assert result.total_cents == 60000


def test_apply_sets_manual_effective_confirmation_flag(tmp_path):
    db = str(tmp_path / "flag.db")
    draft = _manual_draft_with_incidents(db)
    unresolved = next(r for r in draft["rows"] if r["nombre_recibido"].startswith("UNKNOWN"))
    conn = connect(db)
    bid = int(conn.execute("SELECT id FROM nomina_banorte_beneficiaries LIMIT 1").fetchone()[0])
    conn.close()

    out = apply_draft_row(
        db,
        int(draft["id"]),
        int(unresolved["id"]),
        "u",
        int(draft["revision"]),
        beneficiary_id=bid,
        amount_final="200.00",
    )
    row = next(r for r in out["rows"] if r["id"] == unresolved["id"])
    assert row["row_state"] == "OK"
    ud = row.get("user_decision") or {}
    assert ud.get("confirm_manual_effective_from_account") is True


def test_row_state_ok_never_conflicts_with_readiness_after_apply(tmp_path):
    db = str(tmp_path / "sync.db")
    draft = _manual_draft_with_incidents(db)
    conn = connect(db)
    bid = int(conn.execute("SELECT id FROM nomina_banorte_beneficiaries LIMIT 1").fetchone()[0])
    conn.close()
    rev = int(draft["revision"])
    out = draft
    for row in draft["rows"]:
        out = apply_draft_row(
            db,
            int(draft["id"]),
            int(row["id"]),
            "u",
            rev,
            beneficiary_id=bid,
            amount_final=f"{int(row['amount_final_cents']) / 100:.2f}",
        )
        rev = int(out["revision"])

    for row in out["rows"]:
        assert row["row_state"] == "OK"
        conn = connect(db)
        ensure_banorte_tables(conn)
        blockers = evaluate_pag_export_blockers(conn, [row])
        conn.close()
        assert blockers == [], f"row {row['position']} blocked: {blockers}"

    result = generate_from_persistent_draft(
        db, "u", int(out["id"]), expected_revision=rev, consecutive="07"
    )
    assert result.payment_count == 3
