from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from modules.nomina.banorte.catalog_parser import (
    CATALOG_NORMALIZATION_VERSION,
    CATALOG_PARSER_VERSION,
    CATALOG_PROJECTION_VERSION,
    CATALOG_ROW_FIELD_NAMES,
    parse_catalog_txt,
)
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables


class CatalogVersionError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _open(db_path: str | Path) -> sqlite3.Connection:
    conn = connect(db_path)
    ensure_banorte_tables(conn)
    conn.commit()
    return conn


def _event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    actor: str,
    version_id: int | None = None,
    person_id: int | None = None,
    reconciliation_id: int | None = None,
    reason_code: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO nomina_banorte_catalog_events (
            version_id,person_id,reconciliation_id,event_type,reason_code,
            metadata_json,actor,created_at
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            version_id,
            person_id,
            reconciliation_id,
            event_type,
            reason_code,
            _json(metadata or {}),
            actor,
            _now(),
        ),
    )


def stage_catalog_version(
    db_path: str | Path,
    *,
    raw: bytes,
    filename: str,
    actor: str,
) -> dict[str, Any]:
    parsed = parse_catalog_txt(raw, filename=filename)
    conn = _open(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                """
                INSERT INTO nomina_banorte_catalog_versions (
                    source_filename,file_sha256,file_size_bytes,encoding,delimiter,
                    report_date,issuer_original,issuer_normalized,source_line_count,
                    data_row_count,useful_column_count,parser_version,
                    normalization_version,projection_version,created_by,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    parsed.source_filename,
                    parsed.file_sha256,
                    parsed.file_size_bytes,
                    parsed.encoding,
                    parsed.delimiter,
                    parsed.report_date,
                    parsed.issuer_original,
                    parsed.issuer_normalized,
                    parsed.source_line_count,
                    parsed.data_row_count,
                    parsed.useful_column_count,
                    CATALOG_PARSER_VERSION,
                    CATALOG_NORMALIZATION_VERSION,
                    CATALOG_PROJECTION_VERSION,
                    actor,
                    _now(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed: nomina_banorte_catalog_versions.file_sha256" in str(exc):
                raise CatalogVersionError("duplicate_file") from exc
            raise
        version_id = int(cur.lastrowid)
        original_columns = ",".join(CATALOG_ROW_FIELD_NAMES)
        placeholders = ",".join("?" for _ in range(43))
        for row in parsed.rows:
            errors = [row.eligibility_reason] if row.eligibility_reason else []
            conn.execute(
                f"""
                INSERT INTO nomina_banorte_catalog_rows (
                    version_id,source_position,row_content_sha256,row_business_status,
                    error_codes_json,warning_codes_json,{original_columns},
                    employee_number_normalized,name_normalized,name_controlled_key,
                    record_created_date_iso,last_modified_date_iso,birth_date_iso,
                    rfc_normalized,account_number_normalized,internal_status_normalized,
                    result_normalized,eligibility,eligibility_reason,created_at
                ) VALUES ({placeholders})
                """,
                (
                    version_id,
                    row.source_position,
                    row.row_content_sha256,
                    "VALID" if row.eligibility == "ELIGIBLE" else "BLOCKED",
                    _json(errors),
                    "[]",
                    *row.original_fields,
                    row.employee_number_normalized,
                    row.name_normalized,
                    row.name_controlled_key,
                    row.record_created_date_iso,
                    row.last_modified_date_iso,
                    row.birth_date_iso,
                    row.rfc_normalized,
                    row.account_number_normalized,
                    row.internal_status_normalized,
                    row.result_normalized,
                    row.eligibility,
                    row.eligibility_reason,
                    _now(),
                ),
            )
        _event(
            conn,
            version_id=version_id,
            event_type="VERSION_STAGED",
            actor=actor,
            metadata={"data_row_count": parsed.data_row_count},
        )
        conn.commit()
        return get_catalog_version(db_path, version_id, include_persons=False)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _select_current(eligible: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
    if len(eligible) == 1:
        return eligible[0], "SINGLE_ELIGIBLE"
    max_modified = max(str(row["last_modified_date_iso"]) for row in eligible)
    modified = [row for row in eligible if str(row["last_modified_date_iso"]) == max_modified]
    if len(modified) == 1:
        return modified[0], "LATEST_MODIFIED"
    max_created = max(str(row["record_created_date_iso"]) for row in modified)
    created = [row for row in modified if str(row["record_created_date_iso"]) == max_created]
    if len(created) == 1:
        return created[0], "LATEST_CREATED_TIEBREAK"
    accounts = {str(row["account_number_normalized"]) for row in created}
    if len(accounts) != 1:
        return None, None
    return min(created, key=lambda row: int(row["source_position"])), "TIED_SAME_ACCOUNT"


def analyze_catalog_version(
    db_path: str | Path,
    version_id: int,
    *,
    actor: str,
) -> dict[str, Any]:
    conn = _open(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        version = conn.execute(
            "SELECT * FROM nomina_banorte_catalog_versions WHERE id=?", (int(version_id),)
        ).fetchone()
        if version is None:
            raise CatalogVersionError("version_not_found")
        if version["status"] != "STAGED":
            raise CatalogVersionError("transition_invalid")
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM nomina_banorte_catalog_rows WHERE version_id=? ORDER BY source_position",
                (int(version_id),),
            )
        ]
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(row["rfc_normalized"])].append(row)

        counts: Counter[str] = Counter()
        ready_count = 0
        for rfc, person_rows in sorted(grouped.items()):
            eligible = [row for row in person_rows if row["eligibility"] == "ELIGIBLE"]
            all_births = {str(row["birth_date_iso"]) for row in person_rows}
            all_names = {str(row["name_controlled_key"]) for row in person_rows}
            identity_conflict = len(all_births) != 1 or len(all_names) != 1
            current: dict[str, Any] | None = None
            method: str | None = None
            if identity_conflict:
                status = "IDENTITY_CONFLICT"
                observations = ["IDENTITY_CONFLICT"]
            elif not eligible:
                status = "NO_ELIGIBLE_ROW"
                observations = ["NO_ELIGIBLE_ROW"]
            else:
                current, method = _select_current(eligible)
                if current is None:
                    status = "AMBIGUOUS_CURRENT_ACCOUNT"
                    observations = ["AMBIGUOUS_CURRENT_ACCOUNT"]
                else:
                    status = "CATALOG_READY"
                    observations = [] if len(eligible) == 1 else [f"CURRENT_{method}"]
                    ready_count += 1
            representative = current or person_rows[0]
            cur = conn.execute(
                """
                INSERT INTO nomina_banorte_catalog_persons (
                    version_id,issuer_normalized,rfc_normalized,birth_date_iso,
                    name_normalized,name_controlled_key,person_status,current_row_id,
                    current_selection_method,observation_codes_json,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(version_id),
                    str(version["issuer_normalized"]),
                    rfc,
                    str(representative["birth_date_iso"]),
                    str(representative["name_normalized"]),
                    str(representative["name_controlled_key"]),
                    status,
                    int(current["id"]) if current is not None else None,
                    method,
                    _json(observations),
                    _now(),
                ),
            )
            person_id = int(cur.lastrowid)
            ranked = sorted(
                eligible,
                key=lambda row: (
                    str(row["last_modified_date_iso"]),
                    str(row["record_created_date_iso"]),
                    -int(row["source_position"]),
                ),
                reverse=True,
            )
            rank_by_id = {int(row["id"]): rank for rank, row in enumerate(ranked, start=1)}
            for row in person_rows:
                is_current = current is not None and int(row["id"]) == int(current["id"])
                conn.execute(
                    """
                    INSERT INTO nomina_banorte_catalog_person_rows (
                        person_id,row_id,version_id,is_eligible,recency_rank,is_current,
                        exclusion_reason,created_at
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        person_id,
                        int(row["id"]),
                        int(version_id),
                        1 if row["eligibility"] == "ELIGIBLE" else 0,
                        rank_by_id.get(int(row["id"])),
                        1 if is_current else 0,
                        None if is_current else (row["eligibility_reason"] or status),
                        _now(),
                    ),
                )
            counts[status] += 1

        eligible_row_count = sum(1 for row in rows if row["eligibility"] == "ELIGIBLE")
        blocked_count = len(grouped) - ready_count
        summary = {
            "eligible_row_count": eligible_row_count,
            "person_count": len(grouped),
            "catalog_ready_count": ready_count,
            "blocked_person_count": blocked_count,
            "persons_by_status": dict(sorted(counts.items())),
        }
        conn.execute(
            """
            UPDATE nomina_banorte_catalog_versions
            SET status='ANALYZED',eligible_row_count=?,person_count=?,catalog_ready_count=?,
                blocked_person_count=?,analysis_summary_json=?,analyzed_by=?,analyzed_at=?
            WHERE id=? AND status='STAGED'
            """,
            (
                eligible_row_count,
                len(grouped),
                ready_count,
                blocked_count,
                _json(summary),
                actor,
                _now(),
                int(version_id),
            ),
        )
        _event(
            conn,
            version_id=int(version_id),
            event_type="VERSION_ANALYZED",
            actor=actor,
            metadata=summary,
        )
        conn.commit()
        result = get_catalog_version(db_path, int(version_id), include_persons=False)
        result["persons_by_status"] = summary["persons_by_status"]
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_catalog_ready_for_review(
    db_path: str | Path, version_id: int, *, actor: str
) -> dict[str, Any]:
    conn = _open(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status FROM nomina_banorte_catalog_versions WHERE id=?", (int(version_id),)
        ).fetchone()
        if row is None:
            raise CatalogVersionError("version_not_found")
        if row["status"] != "ANALYZED":
            raise CatalogVersionError("transition_invalid")
        conn.execute(
            "UPDATE nomina_banorte_catalog_versions SET status='READY_FOR_REVIEW',ready_by=?,ready_at=? WHERE id=?",
            (actor, _now(), int(version_id)),
        )
        _event(
            conn,
            version_id=int(version_id),
            event_type="VERSION_READY",
            actor=actor,
        )
        conn.commit()
        return get_catalog_version(db_path, int(version_id), include_persons=False)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _active_version_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT id FROM nomina_banorte_catalog_versions WHERE status='ACTIVE'"
    ).fetchone()
    return int(row["id"]) if row is not None else None


def get_catalog_version(
    db_path: str | Path,
    version_id: int,
    *,
    include_persons: bool = True,
) -> dict[str, Any]:
    conn = _open(db_path)
    try:
        version = conn.execute(
            "SELECT * FROM nomina_banorte_catalog_versions WHERE id=?", (int(version_id),)
        ).fetchone()
        if version is None:
            raise CatalogVersionError("version_not_found")
        result = dict(version)
        result["active_version_id"] = _active_version_id(conn)
        if include_persons:
            result["persons"] = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT p.*,r.employee_number_normalized,r.account_number_normalized
                    FROM nomina_banorte_catalog_persons p
                    LEFT JOIN nomina_banorte_catalog_rows r ON r.id=p.current_row_id
                    WHERE p.version_id=? ORDER BY p.rfc_normalized
                    """,
                    (int(version_id),),
                )
            ]
        return result
    finally:
        conn.close()


def list_catalog_versions(db_path: str | Path) -> list[dict[str, Any]]:
    conn = _open(db_path)
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM nomina_banorte_catalog_versions ORDER BY id DESC"
            )
        ]
    finally:
        conn.close()


def _person_projection(conn: sqlite3.Connection, version_id: int) -> dict[str, dict[str, Any]]:
    return {
        str(row["rfc_normalized"]): dict(row)
        for row in conn.execute(
            """
            SELECT p.rfc_normalized,p.person_status,r.employee_number_normalized,
                   r.account_number_normalized,r.row_content_sha256
            FROM nomina_banorte_catalog_persons p
            LEFT JOIN nomina_banorte_catalog_rows r ON r.id=p.current_row_id
            WHERE p.version_id=?
            """,
            (int(version_id),),
        )
    }


def catalog_version_diff(
    db_path: str | Path,
    version_id: int,
    *,
    comparison_version_id: int | None = None,
) -> dict[str, Any]:
    conn = _open(db_path)
    try:
        current = _person_projection(conn, int(version_id))
        compare_id = comparison_version_id
        if compare_id is None:
            compare_id = _active_version_id(conn)
        previous = _person_projection(conn, int(compare_id)) if compare_id is not None else {}
        added = len(current.keys() - previous.keys())
        removed = len(previous.keys() - current.keys())
        changed = 0
        unchanged = 0
        fields = Counter()
        for rfc in current.keys() & previous.keys():
            left = previous[rfc]
            right = current[rfc]
            field_changes: list[str] = []
            if left["employee_number_normalized"] != right["employee_number_normalized"]:
                field_changes.append("employee_number")
            if left["account_number_normalized"] != right["account_number_normalized"]:
                field_changes.append("account_number")
            if left["person_status"] != right["person_status"]:
                field_changes.append("person_status")
            if field_changes:
                changed += 1
                fields.update(field_changes)
            else:
                unchanged += 1
        return {
            "version_id": int(version_id),
            "comparison_version_id": int(compare_id) if compare_id is not None else None,
            "added": added,
            "removed": removed,
            "changed": changed,
            "unchanged": unchanged,
            "changes_by_field": dict(sorted(fields.items())),
        }
    finally:
        conn.close()
