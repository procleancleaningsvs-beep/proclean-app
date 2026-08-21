from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from modules.nomina.banorte.beneficiary_material import beneficiary_material_fingerprint
from modules.nomina.banorte.repository import connect


def catalog_activation_check(db_path: str | Path, version_id: int) -> dict[str, Any]:
    """Read-only Release 2A preflight. It deliberately cannot activate a version."""
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
        # Release 2A deliberately keeps activation unavailable even if all data gates pass.
        reasons.append("RELEASE_2B_REQUIRED")
        return {
            "version_id": int(version_id),
            "version_status": str(version["status"]),
            "active_version_id": int(active["id"]) if active is not None else None,
            "projection_blockers": projection_blockers,
            "reconciliation_pending": reconciliation_pending,
            "stale_reconciliations": stale_count,
            "legacy_open_draft_blockers": legacy_open_draft_blockers,
            "can_activate": False,
            "blocker_codes": reasons,
        }
    finally:
        conn.close()
