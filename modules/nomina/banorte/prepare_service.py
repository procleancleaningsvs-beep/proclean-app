"""Prepare Banorte draft rows: matching cascade + bank inclusion rules.

Preflight finding (A5): ``nomina_calculo_rows.numero_empleado`` is copied from
``nomina_empleado_parametros.numero_empleado`` (ProClean salary parameters), not from
Banorte ``employee_number_effective``. Therefore the approved default cascade is:

cuenta exacta → alias → nombre exacto → employee as secondary signal (never auto) → fuzzy confirm.

"""

from __future__ import annotations

import json
from typing import Any

from modules.nomina.banorte.matching_service import MatchResult, match_name
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.export_readiness import manual_effective_confirmed
from modules.nomina.banorte.validators import (
    is_exact_banorte_bank,
    is_valid_account_number,
    is_valid_employee_number,
    normalize_banco,
    normalize_name,
)


# Documented preflight conclusion for Gate A / reviewers.
EMPLOYEE_NUMBER_SEMANTICS = "PROCLEAN_PARAMETROS_NOT_BANORTE"


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def match_account_exact(db_path: str, account: str) -> MatchResult:
    acct = _digits(account)
    if not acct:
        return MatchResult(kind="NONE", message="empty_account")
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT * FROM nomina_banorte_beneficiaries
            WHERE record_status='ACTIVO' AND account_number=?
            """,
            (acct,),
        ).fetchall()
        if not rows:
            # also try raw string equality
            rows = conn.execute(
                """
                SELECT * FROM nomina_banorte_beneficiaries
                WHERE record_status='ACTIVO' AND account_number=?
                """,
                (str(account).strip(),),
            ).fetchall()
        if len(rows) == 1:
            ben = rows[0]
            from modules.nomina.banorte.matching_service import MatchCandidate

            return MatchResult(
                kind="EXACT",
                selected_id=int(ben["id"]),
                auto_selected=True,
                candidates=[
                    MatchCandidate(
                        int(ben["id"]),
                        ben["nombre_original"],
                        ben["employee_number_effective"],
                        ben["account_number"],
                        ben["curp"],
                        ben["validation_status"],
                        ben["record_status"],
                        1.0,
                        "account",
                    )
                ],
            )
        if len(rows) > 1:
            return MatchResult(kind="AMBIGUOUS", message="duplicate_active_account")
        return MatchResult(kind="NONE")
    finally:
        conn.close()


def match_employee_signal(db_path: str, employee_number: str) -> MatchResult:
    """Secondary signal only — never auto_selected under default semantics."""
    emp = _digits(employee_number) or str(employee_number or "").strip()
    if not emp:
        return MatchResult(kind="NONE")
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT * FROM nomina_banorte_beneficiaries
            WHERE record_status='ACTIVO' AND employee_number_effective=?
            """,
            (emp,),
        ).fetchall()
        from modules.nomina.banorte.matching_service import MatchCandidate

        cands = [
            MatchCandidate(
                int(b["id"]),
                b["nombre_original"],
                b["employee_number_effective"],
                b["account_number"],
                b["curp"],
                b["validation_status"],
                b["record_status"],
                0.5,
                "employee_secondary",
            )
            for b in rows
        ]
        if len(cands) == 1:
            return MatchResult(
                kind="EMPLOYEE_SECONDARY",
                selected_id=None,
                auto_selected=False,
                candidates=cands,
                message="employee_signal_requires_confirmation",
            )
        if len(cands) > 1:
            return MatchResult(kind="AMBIGUOUS", auto_selected=False, candidates=cands)
        return MatchResult(kind="NONE")
    finally:
        conn.close()


def resolve_row_match(db_path: str, row: dict[str, Any]) -> MatchResult:
    """Cascada segura (employee NOT proven Banorte)."""
    account = row.get("account_number_snapshot") or ""
    m = match_account_exact(db_path, str(account))
    if m.auto_selected and m.selected_id:
        return m

    name = str(row.get("nombre_recibido") or "")
    # alias + exact name via existing match_name (alias first, then exact, then fuzzy recommend)
    nm = match_name(db_path, name)
    if nm.kind in {"ALIAS", "EXACT"} and nm.auto_selected and nm.selected_id:
        return nm
    if nm.kind in {"FUZZY_RECOMMENDATION", "AMBIGUOUS", "ALIAS_INACTIVE_RESOLVED"}:
        # keep for UI confirmation; try employee secondary as extra candidates
        emp = match_employee_signal(db_path, str(row.get("employee_number_snapshot") or ""))
        if emp.candidates:
            merged = list(nm.candidates) + list(emp.candidates)
            return MatchResult(
                kind=nm.kind if nm.kind != "NONE" else "EMPLOYEE_SECONDARY",
                selected_id=None,
                auto_selected=False,
                candidates=merged,
                message=nm.message or emp.message,
            )
        return nm

    emp = match_employee_signal(db_path, str(row.get("employee_number_snapshot") or ""))
    if emp.candidates:
        return emp
    return nm if nm.kind != "NONE" else MatchResult(kind="NONE")


def apply_bank_rules(
    row: dict[str, Any],
    match: MatchResult,
    *,
    origin_kind: str = "CALCULO_RUN",
) -> dict[str, Any]:
    """Mutates a copy of row fields for inclusion / warnings / state."""
    out = dict(row)
    warnings = list(out.get("warnings") or [])
    banco_norm = normalize_banco(out.get("banco_snapshot"))
    cents = int(out.get("amount_final_cents") or 0)

    if match.auto_selected and match.selected_id:
        out["beneficiary_id"] = match.selected_id
        out["match_kind"] = match.kind if match.kind != "EXACT" else (
            "EXACT" if (match.candidates and match.candidates[0].via == "account") else match.kind
        )
        if match.candidates and match.candidates[0].via == "account":
            out["match_kind"] = "EXACT"
        out["employee_number_snapshot"] = match.candidates[0].employee_number_effective
        out["account_number_snapshot"] = match.candidates[0].account_number
        out["alias_id"] = match.alias_id

    if cents < 0:
        out["included"] = 0
        out["row_state"] = "EXCLUDED"
        out["amount_final_cents"] = 0
        warnings.append("amount_invalid")
        out["warnings"] = warnings
        out["warnings_json"] = json.dumps(warnings, ensure_ascii=False)
        return out

    if cents == 0:
        out["included"] = 0
        out["row_state"] = "EXCLUDED"
        out["amount_final_cents"] = 0
        if "amount_zero" not in warnings:
            warnings.append("amount_zero")
        out["warnings"] = warnings
        out["warnings_json"] = json.dumps(warnings, ensure_ascii=False)
        return out

    if origin_kind == "MANUAL_CAPTURE":
        if match.auto_selected and match.selected_id:
            emp_ok = is_valid_employee_number(out.get("employee_number_snapshot"))
            acct_ok = is_valid_account_number(out.get("account_number_snapshot"))
            if emp_ok and acct_ok:
                out["included"] = 1
                out["row_state"] = "OK"
            else:
                out["included"] = 0
                out["row_state"] = "NEEDS_REVIEW"
                warnings.append("manual_beneficiary_incomplete")
        elif match.kind in {"FUZZY_RECOMMENDATION", "EMPLOYEE_SECONDARY", "AMBIGUOUS"}:
            out["included"] = 0
            out["row_state"] = "NEEDS_REVIEW"
            warnings.append(f"match_{match.kind.lower()}")
        else:
            out["included"] = 0
            out["row_state"] = "NEEDS_REVIEW"
            warnings.append("manual_unresolved")
        out["warnings"] = warnings
        out["warnings_json"] = json.dumps(warnings, ensure_ascii=False)
        out["user_decision"] = {
            **(out.get("user_decision") or {}),
            "candidate_ids": [c.beneficiary_id for c in match.candidates[:5]],
            "match_message": match.message,
        }
        return out

    # CALCULO_RUN / EXCEL_NOMINA — defense in depth (adapter should already filter)
    if not is_exact_banorte_bank(out.get("banco_snapshot")):
        out["included"] = 0
        out["row_state"] = "EXCLUDED"
        warnings.append("banco_no_banorte" if banco_norm else "banco_vacio")
        out["warnings"] = warnings
        out["warnings_json"] = json.dumps(warnings, ensure_ascii=False)
        return out

    if match.auto_selected and match.selected_id:
        emp_ok = is_valid_employee_number(out.get("employee_number_snapshot"))
        acct_ok = is_valid_account_number(out.get("account_number_snapshot"))
        if emp_ok and acct_ok:
            out["included"] = 1
            out["row_state"] = "OK"
        else:
            out["included"] = 0
            out["row_state"] = "NEEDS_REVIEW"
            warnings.append("beneficiary_incomplete")
    else:
        out["included"] = 0
        out["row_state"] = "NEEDS_REVIEW"
        if match.kind in {"FUZZY_RECOMMENDATION", "EMPLOYEE_SECONDARY", "AMBIGUOUS"}:
            warnings.append(f"match_{match.kind.lower()}")
        else:
            warnings.append("banorte_sin_match")

    if match.kind in {"FUZZY_RECOMMENDATION", "EMPLOYEE_SECONDARY", "AMBIGUOUS"} and not match.auto_selected:
        out["row_state"] = "NEEDS_REVIEW"
        out["included"] = 0

    out["warnings"] = warnings
    out["warnings_json"] = json.dumps(warnings, ensure_ascii=False)
    out["user_decision"] = {
        **(out.get("user_decision") or {}),
        "candidate_ids": [c.beneficiary_id for c in match.candidates[:5]],
        "match_message": match.message,
    }
    return out


def prepare_draft_rows(
    db_path: str,
    rows: list[dict[str, Any]],
    *,
    origin_kind: str = "CALCULO_RUN",
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    conn = connect(db_path)
    try:
        for row in rows:
            m = resolve_row_match(db_path, row)
            kind = str(row.get("origin_kind") or origin_kind)
            out = apply_bank_rules(row, m, origin_kind=kind)
            bid = out.get("beneficiary_id")
            beneficiary = None
            if bid is not None:
                ben_row = conn.execute(
                    "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?",
                    (int(bid),),
                ).fetchone()
                if ben_row is not None:
                    beneficiary = dict(ben_row)
            prepared.append(_apply_manual_effective_gate(out, beneficiary))
    finally:
        conn.close()
    return prepared


def _apply_manual_effective_gate(
    row: dict[str, Any],
    beneficiary: dict[str, Any] | None,
) -> dict[str, Any]:
    out = dict(row)
    if beneficiary is None or int(beneficiary.get("manual_effective_from_account") or 0) != 1:
        return out
    if str(out.get("row_state") or "") != "OK" or manual_effective_confirmed(out):
        return out
    warnings = list(out.get("warnings") or [])
    if "manual_effective_confirmation_required" not in warnings:
        warnings.append("manual_effective_confirmation_required")
    out["warnings"] = warnings
    out["warnings_json"] = json.dumps(warnings, ensure_ascii=False)
    out["row_state"] = "NEEDS_REVIEW"
    out["included"] = 0
    return out


def compute_row_state_from_beneficiary(
    *,
    amount_final_cents: int,
    beneficiary: dict[str, Any] | None,
    origin_kind: str = "MANUAL_CAPTURE",
    banco_snapshot: str | None = None,
    user_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Authoritative state for apply/restore using canonical validators."""
    warnings: list[str] = []
    if amount_final_cents < 0:
        return {
            "included": 0,
            "row_state": "EXCLUDED",
            "amount_final_cents": 0,
            "warnings": ["amount_invalid"],
        }
    if amount_final_cents == 0:
        return {
            "included": 0,
            "row_state": "EXCLUDED",
            "amount_final_cents": 0,
            "warnings": ["amount_zero"],
        }
    if origin_kind in {"CALCULO_RUN", "EXCEL_NOMINA"} and not is_exact_banorte_bank(banco_snapshot):
        return {
            "included": 0,
            "row_state": "EXCLUDED",
            "amount_final_cents": amount_final_cents,
            "warnings": ["banco_no_banorte"],
        }
    if beneficiary is None:
        return {
            "included": 0,
            "row_state": "NEEDS_REVIEW",
            "amount_final_cents": amount_final_cents,
            "warnings": ["manual_unresolved"],
        }
    if beneficiary.get("record_status") != "ACTIVO":
        return {
            "included": 0,
            "row_state": "NEEDS_REVIEW",
            "amount_final_cents": amount_final_cents,
            "warnings": ["beneficiary_not_active"],
        }
    emp_ok = is_valid_employee_number(beneficiary.get("employee_number_effective"))
    acct_ok = is_valid_account_number(beneficiary.get("account_number"))
    if not emp_ok or not acct_ok:
        return {
            "included": 0,
            "row_state": "NEEDS_REVIEW",
            "amount_final_cents": amount_final_cents,
            "warnings": ["beneficiary_incomplete"],
        }
    if beneficiary.get("validation_status") == "MANUAL_PENDIENTE_VALIDACION":
        # usable structurally if emp/acct ok — still LISTO for pag with confirm_manuals at generate
        pass
    if int(beneficiary.get("manual_effective_from_account") or 0) == 1:
        ud = user_decision or {}
        if not ud.get("confirm_manual_effective_from_account"):
            return {
                "included": 0,
                "row_state": "NEEDS_REVIEW",
                "amount_final_cents": amount_final_cents,
                "warnings": ["manual_effective_confirmation_required"],
                "employee_number_snapshot": beneficiary.get("employee_number_effective"),
                "account_number_snapshot": beneficiary.get("account_number"),
            }
    return {
        "included": 1,
        "row_state": "OK",
        "amount_final_cents": amount_final_cents,
        "warnings": warnings,
        "employee_number_snapshot": beneficiary.get("employee_number_effective"),
        "account_number_snapshot": beneficiary.get("account_number"),
    }
