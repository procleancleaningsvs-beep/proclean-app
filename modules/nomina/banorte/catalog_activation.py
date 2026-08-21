from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from modules.nomina.banorte.beneficiary_material import beneficiary_material_fingerprint
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables


class CatalogActivationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _record_event(
    conn,
    *,
    version_id: int,
    actor: str,
    event_type: str,
    reason_code: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO nomina_banorte_catalog_events (
            version_id,person_id,reconciliation_id,event_type,reason_code,
            metadata_json,actor,created_at
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (version_id, None, None, event_type, reason_code, "{}", actor, _now()),
    )


def catalog_activation_check(db_path: str | Path, version_id: int) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        version = conn.execute(
            "SELECT * FROM nomina_banorte_catalog_versions WHERE id=?", (int(version_id),)
        ).fetchone()
        if version is None:
            raise ValueError("version_not_found")
        active = conn.execute(
            "SELECT id FROM nomina_banorte_catalog_versions WHERE status='ACTIVE'"
        ).fetchone()
        projection_blockers = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM nomina_banorte_catalog_persons
                WHERE version_id=? AND person_status IN (
                    'IDENTITY_CONFLICT','AMBIGUOUS_CURRENT_ACCOUNT','INVALID_CURRENT_ROW'
                )
                """,
                (int(version_id),),
            ).fetchone()[0]
        )
        reconciliation_counts = Counter(
            {
                str(status): int(count)
                for status, count in conn.execute(
                    """
                    SELECT r.reconciliation_status,COUNT(*)
                    FROM nomina_banorte_catalog_persons p
                    LEFT JOIN nomina_banorte_catalog_reconciliations r
                      ON r.person_id=p.id AND r.is_current=1
                    WHERE p.version_id=? AND p.person_status='CATALOG_READY'
                    GROUP BY r.reconciliation_status
                    """,
                    (int(version_id),),
                )
            }
        )
        catalog_ready = int(version["catalog_ready_count"] or 0)
        reconciled = reconciliation_counts["AUTO_MATCHED"] + reconciliation_counts["MANUAL_MATCHED"]
        reconciliation_pending = max(0, catalog_ready - reconciled)
        stale_count = 0
        for row in conn.execute(
            """
            SELECT r.beneficiary_material_fingerprint,b.*
            FROM nomina_banorte_catalog_reconciliations r
            JOIN nomina_banorte_beneficiaries b ON b.id=r.beneficiary_id
            WHERE r.version_id=? AND r.is_current=1
              AND r.reconciliation_status IN ('AUTO_MATCHED','MANUAL_MATCHED')
            """,
            (int(version_id),),
        ):
            beneficiary = dict(row)
            if (
                beneficiary_material_fingerprint(beneficiary).sha256
                != row["beneficiary_material_fingerprint"]
            ):
                stale_count += 1
        legacy_open_draft_blockers = 0
        if active is None:
            legacy_open_draft_blockers = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM nomina_banorte_export_drafts
                    WHERE status='OPEN' AND catalog_mode='LEGACY'
                    """
                ).fetchone()[0]
            )
        incompatible_open = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM nomina_banorte_export_drafts
                WHERE status='OPEN'
                  AND catalog_version_id IS NOT NULL
                  AND catalog_version_id <> ?
                """,
                (int(version_id),),
            ).fetchone()[0]
        )
        reasons: list[str] = []
        if version["status"] != "READY_FOR_REVIEW":
            reasons.append("VERSION_NOT_READY_FOR_REVIEW")
        if projection_blockers:
            reasons.append("PROJECTION_BLOCKERS")
        if reconciliation_pending:
            reasons.append("RECONCILIATION_PENDING")
        if stale_count:
            reasons.append("STALE_RECONCILIATIONS")
        if legacy_open_draft_blockers:
            reasons.append("LEGACY_OPEN_DRAFTS")
        if incompatible_open:
            reasons.append("CATALOG_VERSION_CHANGED")
        return {
            "version_id": int(version_id),
            "version_status": str(version["status"]),
            "active_version_id": int(active["id"]) if active is not None else None,
            "projection_blockers": projection_blockers,
            "reconciliation_pending": reconciliation_pending,
            "stale_reconciliations": stale_count,
            "legacy_open_draft_blockers": legacy_open_draft_blockers,
            "can_activate": not reasons,
            "blocker_codes": reasons,
        }
    finally:
        conn.close()


def activate_catalog_version(db_path: str | Path, version_id: int, *, actor: str) -> dict[str, Any]:
    check = catalog_activation_check(db_path, version_id)
    if not check["can_activate"]:
        raise CatalogActivationError(check["blocker_codes"][0] if check["blocker_codes"] else "blocked")
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        conn.execute("BEGIN IMMEDIATE")
        prior = conn.execute(
            "SELECT id FROM nomina_banorte_catalog_versions WHERE status='ACTIVE'"
        ).fetchone()
        if prior is not None and int(prior["id"]) != int(version_id):
            conn.execute(
                """
                UPDATE nomina_banorte_catalog_versions
                SET status='SUPERSEDED',superseded_by=?,superseded_at=?
                WHERE id=? AND status='ACTIVE'
                """,
                (actor, _now(), int(prior["id"])),
            )
        now = _now()
        cur = conn.execute(
            """
            UPDATE nomina_banorte_catalog_versions
            SET status='ACTIVE', activated_by=?, activated_at=?
            WHERE id=? AND status='READY_FOR_REVIEW'
            """,
            (actor, now, int(version_id)),
        )
        if cur.rowcount != 1:
            raise CatalogActivationError("version_not_ready")
        _record_event(conn, version_id=int(version_id), actor=actor, event_type="VERSION_ACTIVATED")
        conn.commit()
    except CatalogActivationError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return catalog_activation_check(db_path, version_id)


def rollback_catalog_activation(db_path: str | Path, version_id: int, *, actor: str) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        ensure_banorte_tables(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id,status FROM nomina_banorte_catalog_versions WHERE id=?",
            (int(version_id),),
        ).fetchone()
        if row is None:
            raise CatalogActivationError("version_not_found")
        if row["status"] != "ACTIVE":
            raise CatalogActivationError("version_not_active")
        now = _now()
        conn.execute(
            """
            UPDATE nomina_banorte_catalog_versions
            SET status='READY_FOR_REVIEW', activated_by=NULL, activated_at=NULL,
                superseded_by=?, superseded_at=?
            WHERE id=? AND status='ACTIVE'
            """,
            (actor, now, int(version_id)),
        )
        _record_event(
            conn,
            version_id=int(version_id),
            actor=actor,
            event_type="VERSION_SUPERSEDED",
            reason_code="ROLLBACK",
        )
        conn.commit()
    except CatalogActivationError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return catalog_activation_check(db_path, version_id)
