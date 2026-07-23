"""Regression: excluded / non-export rows must not block .pag generation."""

from __future__ import annotations

import pytest

from modules.nomina.banorte.draft_repository import (
    create_manual_draft_shell,
    exclude_draft_row,
    get_draft,
    save_draft_rows,
    undo_last_draft_mutation,
)
from modules.nomina.banorte.export_service import (
    ExportBlockedError,
    generate_from_persistent_draft,
)
from modules.nomina.banorte.prepare_service import prepare_draft_rows
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.db import ensure_nomina_tables


def _seed_ben(db: str, *, emp: str, account: str, nombre: str) -> int:
    conn = connect(db)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, employee_number_effective, account_number,
            source_kind, validation_status, record_status,
            imported_at, imported_by, created_at, updated_at
        ) VALUES (?,?,?,?,'ALTA_MANUAL','IMPORTADO_EXITOSO','ACTIVO','t','u','t','t')
        """,
        (nombre, nombre.upper(), emp, account),
    )
    conn.commit()
    bid = int(cur.lastrowid)
    conn.close()
    return bid


def _draft_with_ok_and_unresolved(db: str) -> dict:
    _seed_ben(db, emp="10", account="1234567890", nombre="WORKER OK")
    shell = create_manual_draft_shell(db, "u", names_text="WORKER OK\nUNKNOWN BAD", amounts_text="100\n300")
    draft = shell["draft"]
    prepared = prepare_draft_rows(
        db,
        [
            {
                "position": 1,
                "nombre_recibido": "WORKER OK",
                "amount_original_cents": 10000,
                "amount_final_cents": 10000,
                "included": 1,
                "match_kind": "NONE",
                "row_state": "OK",
                "warnings": [],
                "user_decision": {},
            },
            {
                "position": 2,
                "nombre_recibido": "UNKNOWN BAD",
                "amount_original_cents": 30000,
                "amount_final_cents": 30000,
                "included": 1,
                "match_kind": "NONE",
                "row_state": "NEEDS_REVIEW",
                "warnings": ["manual_unresolved"],
                "user_decision": {},
            },
        ],
        origin_kind="MANUAL_CAPTURE",
    )
    return save_draft_rows(db, int(draft["id"]), "u", int(draft["revision"]), prepared)


def test_included_unresolved_row_blocks_generate(tmp_path):
    db = str(tmp_path / "block.db")
    draft = _draft_with_ok_and_unresolved(db)
    bad = next(r for r in draft["rows"] if r["nombre_recibido"] == "UNKNOWN BAD")
    conn = connect(db)
    conn.execute(
        """
        UPDATE nomina_banorte_export_draft_rows
        SET included=1, row_state='NEEDS_REVIEW', beneficiary_id=NULL
        WHERE id=?
        """,
        (int(bad["id"]),),
    )
    conn.commit()
    conn.close()
    current = get_draft(db, int(draft["id"]))
    with pytest.raises(ExportBlockedError) as exc:
        generate_from_persistent_draft(
            db, "u", int(current["id"]), expected_revision=int(current["revision"]), consecutive="01"
        )
    assert exc.value.code == "rows_require_review"


def test_excluded_manual_unresolved_does_not_block(tmp_path):
    db = str(tmp_path / "excl.db")
    draft = _draft_with_ok_and_unresolved(db)
    bad = next(r for r in draft["rows"] if r["nombre_recibido"] == "UNKNOWN BAD")
    conn = connect(db)
    conn.execute(
        """
        UPDATE nomina_banorte_export_draft_rows
        SET included=1, row_state='NEEDS_REVIEW', beneficiary_id=NULL
        WHERE id=?
        """,
        (int(bad["id"]),),
    )
    conn.commit()
    conn.close()
    current = get_draft(db, int(draft["id"]))
    excluded = exclude_draft_row(
        db, int(current["id"]), int(bad["id"]), "u", int(current["revision"])
    )
    ex_row = next(r for r in excluded["rows"] if r["id"] == bad["id"])
    assert ex_row["included"] == 0
    assert ex_row["row_state"] == "EXCLUDED"
    assert "manual_unresolved" in (ex_row.get("warnings") or [])

    result = generate_from_persistent_draft(
        db,
        "u",
        int(excluded["id"]),
        expected_revision=int(excluded["revision"]),
        consecutive="02",
    )
    assert result.payment_count == 1
    assert result.total_cents == 10000
    assert b"UNKNOWN BAD" not in result.file_bytes


def test_inconsistent_excluded_flag_does_not_block(tmp_path):
    """row_state=EXCLUDED must never participate in export readiness."""
    db = str(tmp_path / "incon.db")
    draft = _draft_with_ok_and_unresolved(db)
    bad = next(r for r in draft["rows"] if r["nombre_recibido"] == "UNKNOWN BAD")
    corrupt = []
    for r in draft["rows"]:
        if r["id"] == bad["id"]:
            corrupt.append(
                {
                    **r,
                    "included": 1,
                    "row_state": "EXCLUDED",
                    "beneficiary_id": None,
                    "excluded_at": "2026-07-23T12:00:00",
                    "excluded_by": "u",
                    "warnings": ["manual_unresolved"],
                }
            )
        else:
            corrupt.append(r)
    saved = save_draft_rows(db, int(draft["id"]), "u", int(draft["revision"]), corrupt)
    bad_saved = next(r for r in saved["rows"] if r["id"] == bad["id"])
    assert bad_saved["row_state"] == "EXCLUDED"
    assert bad_saved["included"] == 0
    assert saved["reconciliation"]["included_count"] == 1

    result = generate_from_persistent_draft(
        db,
        "u",
        int(saved["id"]),
        expected_revision=int(saved["revision"]),
        consecutive="03",
    )
    assert result.payment_count == 1
    assert result.total_cents == 10000


def test_undo_exclude_restores_needs_review_without_blocking(tmp_path):
    db = str(tmp_path / "rest.db")
    draft = _draft_with_ok_and_unresolved(db)
    bad = next(r for r in draft["rows"] if r["nombre_recibido"] == "UNKNOWN BAD")
    excluded = exclude_draft_row(
        db, int(draft["id"]), int(bad["id"]), "u", int(draft["revision"])
    )
    restored = undo_last_draft_mutation(
        db, int(draft["id"]), "u", int(excluded["revision"])
    )
    rest_row = next(r for r in restored["rows"] if r["id"] == bad["id"])
    assert rest_row["row_state"] == "NEEDS_REVIEW"
    assert rest_row["included"] == 0
    assert rest_row["excluded_at"] is None
    result = generate_from_persistent_draft(
        db,
        "u",
        int(restored["id"]),
        expected_revision=int(restored["revision"]),
        consecutive="05",
    )
    assert result.payment_count == 1


def test_included_after_undo_blocks_when_still_unresolved(tmp_path):
    db = str(tmp_path / "undo-block.db")
    draft = _draft_with_ok_and_unresolved(db)
    bad = next(r for r in draft["rows"] if r["nombre_recibido"] == "UNKNOWN BAD")
    conn = connect(db)
    conn.execute(
        """
        UPDATE nomina_banorte_export_draft_rows
        SET included=1, row_state='NEEDS_REVIEW', beneficiary_id=NULL
        WHERE id=?
        """,
        (int(bad["id"]),),
    )
    conn.commit()
    conn.close()
    current = get_draft(db, int(draft["id"]))
    excluded = exclude_draft_row(
        db, int(current["id"]), int(bad["id"]), "u", int(current["revision"])
    )
    restored = undo_last_draft_mutation(
        db, int(current["id"]), "u", int(excluded["revision"])
    )
    conn = connect(db)
    conn.execute(
        """
        UPDATE nomina_banorte_export_draft_rows
        SET included=1, row_state='NEEDS_REVIEW', beneficiary_id=NULL
        WHERE id=?
        """,
        (int(bad["id"]),),
    )
    conn.commit()
    conn.close()
    again = get_draft(db, int(draft["id"]))
    with pytest.raises(ExportBlockedError) as exc:
        generate_from_persistent_draft(
            db,
            "u",
            int(again["id"]),
            expected_revision=int(again["revision"]),
            consecutive="06",
        )
    assert exc.value.code == "rows_require_review"


def test_many_ok_rows_one_excluded_unresolved(tmp_path):
    db = str(tmp_path / "many.db")
    conn = connect(db)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    for i in range(94):
        conn.execute(
            """
            INSERT INTO nomina_banorte_beneficiaries (
                nombre_original, nombre_normalizado, employee_number_effective, account_number,
                source_kind, validation_status, record_status,
                imported_at, imported_by, created_at, updated_at
            ) VALUES (?,?,?,?,'ALTA_MANUAL','IMPORTADO_EXITOSO','ACTIVO','t','u','t','t')
            """,
            (f"EMP {i}", f"EMP {i}", str(100 + i), f"1234567{i:03d}"),
        )
    conn.commit()
    conn.close()

    names = "\n".join(f"EMP {i}" for i in range(94)) + "\nBAD ONE"
    amounts = "\n".join("100.00" for _ in range(94)) + "\n50.00"
    shell = create_manual_draft_shell(db, "u", names_text=names, amounts_text=amounts)
    draft = shell["draft"]
    rows_in = []
    for pos, name in enumerate(names.strip().split("\n"), start=1):
        rows_in.append(
            {
                "position": pos,
                "nombre_recibido": name,
                "amount_original_cents": 10000,
                "amount_final_cents": 10000,
                "included": 1,
                "match_kind": "NONE",
                "row_state": "OK",
                "warnings": [],
                "user_decision": {},
            }
        )
    prepared = prepare_draft_rows(db, rows_in, origin_kind="MANUAL_CAPTURE")
    saved = save_draft_rows(db, int(draft["id"]), "u", int(draft["revision"]), prepared)
    bad = next(r for r in saved["rows"] if r["nombre_recibido"] == "BAD ONE")
    excluded = exclude_draft_row(
        db, int(saved["id"]), int(bad["id"]), "u", int(saved["revision"])
    )
    assert excluded["reconciliation"]["included_count"] == 94
    assert excluded["reconciliation"]["payment_count"] == 94
    assert excluded["reconciliation"]["total_final_cents"] == 94 * 10000

    result = generate_from_persistent_draft(
        db,
        "u",
        int(excluded["id"]),
        expected_revision=int(excluded["revision"]),
        consecutive="06",
    )
    assert result.payment_count == 94
    assert result.total_cents == 94 * 10000
    assert b"BAD ONE" not in result.file_bytes
