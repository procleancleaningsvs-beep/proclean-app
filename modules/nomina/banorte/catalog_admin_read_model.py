"""Read-only presentation model for Banorte Catalog Admin V2 (C3a/C3b)."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from modules.nomina.banorte.catalog_application_plan import (
    NewBaselinePlan,
    PriorCurrentCandidate,
    build_new_baseline_plan,
)
from modules.nomina.banorte.post_catalog_authority import (
    evaluate_post_catalog_addition,
    load_active_catalog_context,
)
from modules.nomina.banorte.repository import connect


_PENDING_STATUSES = frozenset({"STAGED", "ANALYZED", "READY_FOR_REVIEW"})
_FILTERS = frozenset(
    {
        "all",
        "added",
        "removed",
        "account_changed",
        "employee_changed",
        "lineage_unconfirmed",
        "conflict",
    }
)
_OPERATIONAL_CONFLICT_CODES = frozenset(
    {
        "PROJECTION_BLOCKERS",
        "PROJECTION_COUNT_MISMATCH",
        "TARGET_CURRENT_ROW_INVALID",
        "TARGET_EMPLOYEE_INVALID",
        "TARGET_ACCOUNT_INVALID",
        "TARGET_EMPLOYEE_DUPLICATE",
        "TARGET_ACCOUNT_DUPLICATE",
        "SPLIT_PRIOR_CURRENT_IDENTIFIERS",
        "PREDECESSOR_REUSED",
        "ACTIVE_NON_CURRENT_IDENTIFIER_COLLISION",
    }
)


class CatalogAdminReadError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CatalogAdminOverview:
    active_version_id: int | None
    active: dict[str, Any] | None
    pending: dict[str, Any] | None
    selected: dict[str, Any] | None
    history: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "active_version_id": self.active_version_id,
            "active": self.active,
            "pending": self.pending,
            "selected": self.selected,
            "history": list(self.history),
        }


@dataclass(frozen=True)
class CatalogComparisonRows:
    version_id: int
    items: tuple[dict[str, Any], ...]
    page: int
    page_size: int
    total: int
    total_pages: int
    filter_name: str
    has_previous: bool
    has_next: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "items": list(self.items),
            "page": self.page,
            "page_size": self.page_size,
            "total": self.total,
            "total_pages": self.total_pages,
            "filter": self.filter_name,
            "has_previous": self.has_previous,
            "has_next": self.has_next,
        }


@dataclass(frozen=True)
class CatalogLineageCandidates:
    version_id: int
    row_key: str
    target_person: dict[str, str]
    items: tuple[dict[str, Any], ...]
    page: int
    page_size: int
    total: int
    total_pages: int
    has_previous: bool
    has_next: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "row_key": self.row_key,
            "target_person": self.target_person,
            "items": list(self.items),
            "page": self.page,
            "page_size": self.page_size,
            "total": self.total,
            "total_pages": self.total_pages,
            "has_previous": self.has_previous,
            "has_next": self.has_next,
        }


def _mask_account(value: Any) -> str:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if len(digits) <= 4:
        return digits
    return f"{'*' * (len(digits) - 4)}{digits[-4:]}"


def _person(
    *,
    name: Any,
    employee: Any,
    account: Any,
    rfc: Any,
    birth_date: Any,
) -> dict[str, str]:
    return {
        "name": str(name or ""),
        "employee": str(employee or ""),
        "account_masked": _mask_account(account),
        "rfc": str(rfc or ""),
        "birth_date": str(birth_date or ""),
    }


def _candidate_person(candidate: PriorCurrentCandidate | None) -> dict[str, str] | None:
    if candidate is None:
        return None
    return _person(
        name=candidate.name_original,
        employee=candidate.employee,
        account=candidate.account,
        rfc=candidate.rfc,
        birth_date=candidate.birth_date,
    )


def _status_presentation(status: str, *, has_conflicts: bool = False) -> dict[str, str]:
    if status == "ACTIVE":
        return {"label": "Vigente", "tone": "success", "subtext": ""}
    if status == "SUPERSEDED":
        return {"label": "Reemplazado", "tone": "muted", "subtext": ""}
    if status == "STAGED":
        return {"label": "Pendiente", "tone": "neutral", "subtext": "Análisis incompleto"}
    if status == "ANALYZED":
        if has_conflicts:
            return {"label": "Requiere atención", "tone": "danger", "subtext": ""}
        return {"label": "Pendiente", "tone": "neutral", "subtext": "Análisis completo"}
    if status == "READY_FOR_REVIEW":
        if has_conflicts:
            return {"label": "Requiere atención", "tone": "danger", "subtext": ""}
        return {"label": "Pendiente", "tone": "neutral", "subtext": "Listo para aplicar"}
    return {"label": "No disponible", "tone": "muted", "subtext": ""}


def _active_post_snapshot_count(conn, active: dict[str, Any] | None) -> int:
    if active is None:
        return 0
    context = load_active_catalog_context(conn)
    if context is None:
        return 0
    count = 0
    for row in conn.execute(
        "SELECT * FROM nomina_banorte_beneficiaries WHERE record_status='ACTIVO' ORDER BY id"
    ):
        authority = evaluate_post_catalog_addition(conn, dict(row), ctx=context)
        if authority.get("payment_enabled"):
            count += 1
    return count


def _preview_for(conn, version: dict[str, Any]) -> dict[str, Any] | None:
    if str(version.get("status") or "") not in {"ANALYZED", "READY_FOR_REVIEW"}:
        return None
    plan = build_new_baseline_plan(conn, int(version["id"]))
    preview = plan.preview()
    rows = _all_rows(plan)
    classifications = [str(row["classification"]) for row in rows]
    comparison = {
        "unchanged": classifications.count("UNCHANGED"),
        "added": classifications.count("ADDED"),
        "removed": classifications.count("REMOVED"),
        "account_changed": classifications.count("ACCOUNT_CHANGED"),
        "employee_changed": classifications.count("EMPLOYEE_CHANGED"),
        "both_changed": classifications.count("BOTH_CHANGED"),
        "post_absorbed": classifications.count("POST_ABSORBED"),
        "post_dropped": classifications.count("POST_DROPPED"),
        "lineage_confirmed": sum(
            1 for action in plan.actions if action.lineage_status == "CONFIRMED"
        ),
        "lineage_unconfirmed": int(preview["lineage_unconfirmed_count"]),
        "conflicts": int(preview["operational_conflict_count"]),
    }
    return {
        "final_current_total": int(preview["final_current_total"]),
        "leaving": int(preview["leaving"]),
        "post_additions_absorbed": int(preview["post_additions_absorbed"]),
        "post_additions_dropped": int(preview["post_additions_dropped"]),
        "lineage_unconfirmed_count": int(preview["lineage_unconfirmed_count"]),
        "operational_conflict_count": int(preview["operational_conflict_count"]),
        "can_apply": bool(preview["can_apply"]),
        "preview_fingerprint": str(preview["preview_fingerprint"]),
        "blocker_messages": [
            _conflict_reason(str(blocker.get("code") or ""))
            for blocker in preview.get("operational_blockers", [])
        ],
        "incompatible_open_operation_count": len(
            preview.get("incompatible_open_operations", [])
        ),
        "comparison": comparison,
    }


def _version_summary(
    version: dict[str, Any],
    *,
    preview: dict[str, Any] | None = None,
    active_post_snapshot_count: int | None = None,
) -> dict[str, Any]:
    status = str(version.get("status") or "")
    conflicts = (
        int(preview.get("operational_conflict_count") or 0)
        if preview is not None
        else int(version.get("blocked_person_count") or 0)
    )
    presentation = _status_presentation(status, has_conflicts=conflicts > 0)
    return {
        "id": int(version["id"]),
        "report_date": str(version.get("report_date") or ""),
        "person_count": version.get("person_count"),
        "data_row_count": int(version.get("data_row_count") or 0),
        "active_post_snapshot_count": active_post_snapshot_count,
        "activated_at": version.get("activated_at"),
        "activated_by": version.get("activated_by"),
        "status_label": presentation["label"],
        "status_tone": presentation["tone"],
        "status_subtext": presentation["subtext"],
        "analysis_incomplete": status == "STAGED",
        "comparison_available": status in {"ANALYZED", "READY_FOR_REVIEW"},
        "operational_conflict_count": conflicts,
        "preview": preview,
    }


def load_catalog_admin_overview(
    db_path: str | Path,
    *,
    selected_version_id: int | None = None,
) -> CatalogAdminOverview:
    """Load active, pending and basic history from one read-only snapshot."""
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        versions = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM nomina_banorte_catalog_versions ORDER BY id DESC"
            )
        ]
        by_id = {int(version["id"]): version for version in versions}
        if selected_version_id is not None and int(selected_version_id) not in by_id:
            raise CatalogAdminReadError("version_not_found")
        active_raw = next((item for item in versions if item["status"] == "ACTIVE"), None)
        pending_raw = next((item for item in versions if item["status"] in _PENDING_STATUSES), None)
        selected_raw = by_id.get(int(selected_version_id)) if selected_version_id is not None else pending_raw
        active_post_count = _active_post_snapshot_count(conn, active_raw)
        preview_cache: dict[int, dict[str, Any] | None] = {}

        def preview(version: dict[str, Any] | None) -> dict[str, Any] | None:
            if version is None:
                return None
            version_id = int(version["id"])
            if version_id not in preview_cache:
                preview_cache[version_id] = _preview_for(conn, version)
            return preview_cache[version_id]

        active = (
            _version_summary(
                active_raw,
                active_post_snapshot_count=active_post_count,
            )
            if active_raw is not None
            else None
        )
        pending = (
            _version_summary(pending_raw, preview=preview(pending_raw))
            if pending_raw is not None
            else None
        )
        selected = (
            _version_summary(selected_raw, preview=preview(selected_raw))
            if selected_raw is not None
            else None
        )
        history = tuple(
            _version_summary(
                version,
                preview=preview(version) if version["status"] in _PENDING_STATUSES else None,
                active_post_snapshot_count=(
                    active_post_count if version["status"] == "ACTIVE" else None
                ),
            )
            for version in versions
        )
        conn.commit()
        return CatalogAdminOverview(
            active_version_id=int(active_raw["id"]) if active_raw is not None else None,
            active=active,
            pending=pending,
            selected=selected,
            history=history,
        )
    finally:
        conn.close()


def _method_presentation(method: str) -> str | None:
    if method.startswith("EXACT_EMPLOYEE_ACCOUNT_"):
        return "Coincidencia de número, cuenta y nombre"
    if method.startswith("PREVIOUS_ACTIVE_RFC_BIRTH_"):
        return "Coincidencia de RFC, nacimiento y nombre"
    if method == "MANUAL_CONTINUITY_CONFIRMED":
        return "Relación confirmada previamente"
    return None


def _business_reason(classification: str) -> str:
    return {
        "UNCHANGED": "Sin cambios",
        "ADDED": "Nueva persona en Banorte",
        "REMOVED": "La persona ya no aparece en el nuevo archivo",
        "ACCOUNT_CHANGED": "Cambio de cuenta detectado",
        "EMPLOYEE_CHANGED": "Cambio de número de empleado detectado",
        "BOTH_CHANGED": "Cambió el número de empleado y la cuenta",
        "POST_ABSORBED": "La alta posterior ya aparece en el nuevo archivo",
        "POST_DROPPED": "La alta posterior no aparece en el nuevo archivo",
        "CONFLICT": "Conflicto que requiere atención",
    }[classification]


def _conflict_reason(code: str) -> str:
    return {
        "TARGET_EMPLOYEE_DUPLICATE": "El mismo número de empleado aparece asignado a más de una persona.",
        "TARGET_ACCOUNT_DUPLICATE": "La misma cuenta aparece asignada a más de una persona.",
        "SPLIT_PRIOR_CURRENT_IDENTIFIERS": "El número de empleado y la cuenta corresponden a personas vigentes distintas.",
        "ACTIVE_NON_CURRENT_IDENTIFIER_COLLISION": "Un identificador del nuevo archivo ya está asignado a otra persona vigente.",
        "PREDECESSOR_REUSED": "Una persona vigente aparece relacionada con más de una persona del nuevo archivo.",
        "TARGET_EMPLOYEE_INVALID": "El número de empleado no cumple el formato operativo requerido.",
        "TARGET_ACCOUNT_INVALID": "La cuenta no cumple el formato operativo requerido.",
        "TARGET_CURRENT_ROW_INVALID": "La fila vigente de esta persona no es válida para aplicar.",
        "PROJECTION_BLOCKERS": "El archivo contiene personas que requieren atención antes de aplicarse.",
        "PROJECTION_COUNT_MISMATCH": "El análisis del archivo ya no coincide con su proyección.",
        "INCOMPATIBLE_OPEN_OPERATIONS": "Existen operaciones Banorte abiertas que pertenecen a otro catálogo. Conclúyelas o descártalas antes de aplicar el nuevo catálogo.",
        "MATERIAL_FINGERPRINT_STALE": "La información cambió después de la comparación. Vuelve a analizar el archivo antes de aplicarlo.",
    }.get(code, "Existe un conflicto operativo que debe revisarse antes de aplicar.")


def _classification_for_action(action) -> str:
    if action.predecessor_authority_kind == "POST_CATALOG_ADDITION":
        return "POST_ABSORBED"
    if action.lineage_status != "CONFIRMED" or action.predecessor_beneficiary_id is None:
        return "ADDED"
    return {
        "ACCOUNT": "ACCOUNT_CHANGED",
        "EMPLOYEE": "EMPLOYEE_CHANGED",
        "BOTH": "BOTH_CHANGED",
    }.get(action.identifier_change, "UNCHANGED")


def _changed_fields(classification: str) -> list[str]:
    return {
        "ACCOUNT_CHANGED": ["Cuenta"],
        "EMPLOYEE_CHANGED": ["Número de empleado"],
        "BOTH_CHANGED": ["Número de empleado", "Cuenta"],
    }.get(classification, [])


def _row_for_action(
    action,
    candidate: PriorCurrentCandidate | None,
    *,
    resolution_available: bool,
) -> dict[str, Any]:
    classification = _classification_for_action(action)
    lineage_confirmed = action.lineage_status == "CONFIRMED"
    return {
        "row_key": f"target-{action.person_id}",
        "classification": classification,
        "classification_label": _business_reason(classification),
        "current_person": _candidate_person(candidate),
        "target_person": _person(
            name=action.name_original,
            employee=action.employee,
            account=action.account,
            rfc=action.rfc,
            birth_date=action.birth_date,
        ),
        "changed_fields": _changed_fields(classification),
        "business_reason": _business_reason(classification),
        "lineage_status": action.lineage_status,
        "lineage_label": "Relación identificada" if lineage_confirmed else "Relación histórica no confirmada",
        "lineage_detail": (
            "Existe evidencia suficiente para enlazar esta persona con su historial anterior."
            if lineage_confirmed
            else "La persona es válida en el nuevo archivo, pero no hay evidencia suficiente para enlazarla con una persona anterior. Se incorporará como una identidad nueva y separada."
        ),
        "lineage_method": _method_presentation(action.match_method),
        "operational_conflict": False,
        "conflict_reason": None,
        "resolution_available": resolution_available,
        "_search_text_private": " ".join(
            str(value or "")
            for value in (
                action.name_original,
                action.employee,
                action.account,
                action.rfc,
                candidate.name_original if candidate else "",
                candidate.employee if candidate else "",
                candidate.account if candidate else "",
                candidate.rfc if candidate else "",
            )
        ).casefold(),
    }


def _row_for_candidate(
    candidate: PriorCurrentCandidate,
    *,
    classification: str,
) -> dict[str, Any]:
    return {
        "row_key": f"prior-{candidate.authority_kind.lower()}-{candidate.beneficiary_id}",
        "classification": classification,
        "classification_label": _business_reason(classification),
        "current_person": _candidate_person(candidate),
        "target_person": None,
        "changed_fields": [],
        "business_reason": _business_reason(classification),
        "lineage_status": None,
        "lineage_label": None,
        "lineage_detail": None,
        "lineage_method": None,
        "operational_conflict": False,
        "conflict_reason": None,
        "resolution_available": False,
        "_search_text_private": " ".join(
            str(value or "")
            for value in (
                candidate.name_original,
                candidate.employee,
                candidate.account,
                candidate.rfc,
            )
        ).casefold(),
    }


def _row_for_conflict(blocker: dict[str, Any], index: int) -> dict[str, Any]:
    code = str(blocker.get("code") or "")
    return {
        "row_key": f"conflict-{index}",
        "classification": "CONFLICT",
        "classification_label": "Conflicto que requiere atención",
        "current_person": None,
        "target_person": None,
        "changed_fields": [],
        "business_reason": _conflict_reason(code),
        "lineage_status": None,
        "lineage_label": None,
        "lineage_detail": None,
        "lineage_method": None,
        "operational_conflict": True,
        "conflict_reason": _conflict_reason(code),
        "resolution_available": False,
        "_search_text_private": "",
    }


def _all_rows(plan: NewBaselinePlan) -> list[dict[str, Any]]:
    candidates = {item.beneficiary_id: item for item in plan.prior_candidates}
    consumed_candidates = {
        int(action.predecessor_beneficiary_id)
        for action in plan.actions
        if action.lineage_status == "CONFIRMED"
        and action.predecessor_beneficiary_id is not None
    }
    has_available_candidate = any(
        candidate.beneficiary_id not in consumed_candidates
        for candidate in plan.prior_candidates
    )
    rows = [
        _row_for_action(
            action,
            candidates.get(int(action.predecessor_beneficiary_id or 0)),
            resolution_available=(
                action.lineage_status == "UNCONFIRMED"
                and not plan.operational_blockers
                and has_available_candidate
            ),
        )
        for action in plan.actions
    ]
    matched_prior = {
        int(action.predecessor_beneficiary_id)
        for action in plan.actions
        if action.lineage_status == "CONFIRMED" and action.predecessor_beneficiary_id is not None
    }
    for candidate in plan.prior_candidates:
        if candidate.authority_kind == "CATALOG" and candidate.beneficiary_id not in matched_prior:
            rows.append(_row_for_candidate(candidate, classification="REMOVED"))
        if candidate.beneficiary_id in plan.post_additions_dropped:
            rows.append(_row_for_candidate(candidate, classification="POST_DROPPED"))
    for index, blocker in enumerate(plan.operational_blockers, start=1):
        if str(blocker.get("code") or "") in _OPERATIONAL_CONFLICT_CODES:
            rows.append(_row_for_conflict(blocker, index))
    return rows


def _search_text(row: dict[str, Any]) -> str:
    if row.get("_search_text_private"):
        return str(row["_search_text_private"])
    values: list[str] = []
    for side in (row.get("current_person"), row.get("target_person")):
        if side:
            values.extend(str(side.get(key) or "") for key in ("name", "employee", "rfc"))
            values.append(str(side.get("account_masked") or "").replace("*", ""))
    return " ".join(values).casefold()


def _matches_filter(row: dict[str, Any], filter_name: str) -> bool:
    classification = row["classification"]
    if filter_name == "all":
        return True
    if filter_name == "added":
        return classification in {"ADDED", "POST_ABSORBED"}
    if filter_name == "removed":
        return classification in {"REMOVED", "POST_DROPPED"}
    if filter_name == "account_changed":
        return classification in {"ACCOUNT_CHANGED", "BOTH_CHANGED"}
    if filter_name == "employee_changed":
        return classification in {"EMPLOYEE_CHANGED", "BOTH_CHANGED"}
    if filter_name == "lineage_unconfirmed":
        return row.get("lineage_status") == "UNCONFIRMED"
    return bool(row.get("operational_conflict"))


def _validated_page(page: int, page_size: int) -> tuple[int, int]:
    try:
        page = int(page)
        page_size = int(page_size)
    except (TypeError, ValueError) as exc:
        raise CatalogAdminReadError("invalid_pagination") from exc
    if page < 1 or page_size < 1 or page_size > 100:
        raise CatalogAdminReadError("invalid_pagination")
    return page, page_size


def list_catalog_comparison_rows(
    db_path: str | Path,
    version_id: int,
    *,
    page: int = 1,
    page_size: int = 25,
    filter_name: str = "all",
    search: str = "",
) -> CatalogComparisonRows:
    page, page_size = _validated_page(page, page_size)
    filter_name = str(filter_name or "all")
    if filter_name not in _FILTERS:
        raise CatalogAdminReadError("invalid_filter")
    search = str(search or "").strip().casefold()
    if len(search) > 120:
        raise CatalogAdminReadError("invalid_search")
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        version = conn.execute(
            "SELECT status FROM nomina_banorte_catalog_versions WHERE id=?", (int(version_id),)
        ).fetchone()
        if version is None:
            raise CatalogAdminReadError("version_not_found")
        if version["status"] not in {"ANALYZED", "READY_FOR_REVIEW"}:
            raise CatalogAdminReadError("comparison_unavailable")
        plan = build_new_baseline_plan(conn, int(version_id))
        rows = [row for row in _all_rows(plan) if _matches_filter(row, filter_name)]
        if search:
            rows = [row for row in rows if search in _search_text(row)]
        order = {
            "CONFLICT": 0,
            "BOTH_CHANGED": 1,
            "ACCOUNT_CHANGED": 2,
            "EMPLOYEE_CHANGED": 3,
            "ADDED": 4,
            "POST_ABSORBED": 5,
            "REMOVED": 6,
            "POST_DROPPED": 7,
            "UNCHANGED": 8,
        }
        rows.sort(
            key=lambda item: (
                order.get(str(item["classification"]), 99),
                str((item.get("target_person") or item.get("current_person") or {}).get("name") or "").casefold(),
                str(item["row_key"]),
            )
        )
        total = len(rows)
        total_pages = max(1, math.ceil(total / page_size))
        if page > total_pages and total:
            raise CatalogAdminReadError("page_not_found")
        start = (page - 1) * page_size
        items = tuple(
            {key: value for key, value in row.items() if not key.startswith("_")}
            for row in rows[start : start + page_size]
        )
        conn.commit()
        return CatalogComparisonRows(
            version_id=int(version_id),
            items=items,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
            filter_name=filter_name,
            has_previous=page > 1,
            has_next=page < total_pages,
        )
    finally:
        conn.close()


def get_catalog_comparison_row(
    db_path: str | Path,
    version_id: int,
    row_key: str,
) -> dict[str, Any]:
    row_key = str(row_key or "")
    if re.fullmatch(
        r"(?:target-\d+|prior-(?:catalog|post_catalog_addition)-\d+|conflict-\d+)",
        row_key,
    ) is None:
        raise CatalogAdminReadError("row_not_found")
    rows = list_catalog_comparison_rows(
        db_path,
        int(version_id),
        page=1,
        page_size=100,
    )
    matching = [item for item in rows.items if item["row_key"] == row_key]
    if not matching:
        # The complete set can exceed the maximum response page, so scan internal pages.
        for page in range(2, rows.total_pages + 1):
            part = list_catalog_comparison_rows(
                db_path,
                int(version_id),
                page=page,
                page_size=100,
            )
            matching = [item for item in part.items if item["row_key"] == row_key]
            if matching:
                break
    if not matching:
        raise CatalogAdminReadError("row_not_found")
    return matching[0]


def _lineage_review_context(
    conn,
    version_id: int,
    row_key: str,
) -> tuple[NewBaselinePlan, Any, tuple[PriorCurrentCandidate, ...]]:
    match = re.fullmatch(r"target-(\d+)", str(row_key or ""))
    if match is None:
        raise CatalogAdminReadError("lineage_not_reviewable")
    version = conn.execute(
        "SELECT status FROM nomina_banorte_catalog_versions WHERE id=?",
        (int(version_id),),
    ).fetchone()
    if version is None:
        raise CatalogAdminReadError("version_not_found")
    if version["status"] not in {"ANALYZED", "READY_FOR_REVIEW"}:
        raise CatalogAdminReadError("lineage_not_reviewable")
    plan = build_new_baseline_plan(conn, int(version_id))
    person_id = int(match.group(1))
    action = next(
        (item for item in plan.actions if item.person_id == person_id),
        None,
    )
    if action is None or action.lineage_status != "UNCONFIRMED":
        raise CatalogAdminReadError("lineage_not_reviewable")
    if plan.operational_blockers:
        raise CatalogAdminReadError("lineage_blocked")
    consumed = {
        int(item.predecessor_beneficiary_id)
        for item in plan.actions
        if item.person_id != person_id
        and item.lineage_status == "CONFIRMED"
        and item.predecessor_beneficiary_id is not None
    }
    candidates = tuple(
        candidate
        for candidate in plan.prior_candidates
        if candidate.beneficiary_id not in consumed
    )
    if not candidates:
        raise CatalogAdminReadError("lineage_no_candidates")
    return plan, action, candidates


def _evidence_signal(target_value: Any, candidate_value: Any) -> bool:
    target = " ".join(str(target_value or "").upper().split())
    candidate = " ".join(str(candidate_value or "").upper().split())
    return bool(target and candidate and target == candidate)


def _candidate_review_item(action, candidate: PriorCurrentCandidate) -> dict[str, Any]:
    return {
        "candidate_id": int(candidate.beneficiary_id),
        "person": _candidate_person(candidate),
        "evidence": {
            "name": _evidence_signal(action.name_original, candidate.name_original),
            "employee": _evidence_signal(action.employee, candidate.employee),
            "account": _evidence_signal(action.account, candidate.account),
            "rfc": _evidence_signal(action.rfc, candidate.rfc),
            "birth_date": _evidence_signal(action.birth_date, candidate.birth_date),
        },
        "_search_text_private": " ".join(
            str(value or "")
            for value in (
                candidate.name_original,
                candidate.employee,
                candidate.account,
                candidate.rfc,
                candidate.birth_date,
            )
        ).casefold(),
    }


def list_catalog_lineage_candidates(
    db_path: str | Path,
    version_id: int,
    row_key: str,
    *,
    page: int = 1,
    page_size: int = 20,
    search: str = "",
) -> CatalogLineageCandidates:
    """List only C2-authorized PRIOR CURRENT candidates with masked material."""
    page, page_size = _validated_page(page, page_size)
    if page_size > 50:
        raise CatalogAdminReadError("invalid_pagination")
    search = str(search or "").strip().casefold()
    if len(search) > 120:
        raise CatalogAdminReadError("invalid_search")
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        _plan, action, candidates = _lineage_review_context(
            conn, int(version_id), row_key
        )
        items = [_candidate_review_item(action, candidate) for candidate in candidates]
        if search:
            items = [
                item
                for item in items
                if search in str(item["_search_text_private"])
            ]
        items.sort(
            key=lambda item: (
                str(item["person"]["name"]).casefold(),
                int(item["candidate_id"]),
            )
        )
        total = len(items)
        total_pages = max(1, math.ceil(total / page_size))
        if page > total_pages and total:
            raise CatalogAdminReadError("page_not_found")
        start = (page - 1) * page_size
        public_items = tuple(
            {key: value for key, value in item.items() if not key.startswith("_")}
            for item in items[start : start + page_size]
        )
        conn.commit()
        return CatalogLineageCandidates(
            version_id=int(version_id),
            row_key=str(row_key),
            target_person=_person(
                name=action.name_original,
                employee=action.employee,
                account=action.account,
                rfc=action.rfc,
                birth_date=action.birth_date,
            ),
            items=public_items,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
            has_previous=page > 1,
            has_next=page < total_pages,
        )
    finally:
        conn.close()


def resolve_catalog_lineage_selection(
    db_path: str | Path,
    version_id: int,
    row_key: str,
    candidate_id: int,
) -> tuple[int, int]:
    """Fail closed before mutation; the C2 service revalidates again in its write tx."""
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        _plan, action, candidates = _lineage_review_context(
            conn, int(version_id), row_key
        )
        candidate = next(
            (
                item
                for item in candidates
                if item.beneficiary_id == int(candidate_id)
            ),
            None,
        )
        if candidate is None:
            raise CatalogAdminReadError("lineage_candidate_not_allowed")
        conn.commit()
        return int(action.person_id), int(candidate.beneficiary_id)
    finally:
        conn.close()


def list_catalog_history(
    db_path: str | Path,
    *,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    page, page_size = _validated_page(page, page_size)
    overview = load_catalog_admin_overview(db_path)
    history = list(overview.history)
    total = len(history)
    total_pages = max(1, math.ceil(total / page_size))
    if page > total_pages and total:
        raise CatalogAdminReadError("page_not_found")
    start = (page - 1) * page_size
    return {
        "items": history[start : start + page_size],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "has_previous": page > 1,
        "has_next": page < total_pages,
    }
