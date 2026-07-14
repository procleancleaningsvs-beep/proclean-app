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
from modules.nomina.banorte.validators import normalize_name


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


def apply_bank_rules(row: dict[str, Any], match: MatchResult) -> dict[str, Any]:
    """Mutates a copy of row fields for inclusion / warnings / state."""
    out = dict(row)
    warnings = list(out.get("warnings") or [])
    banco = normalize_name(out.get("banco_snapshot") or "").replace(" ", "")
    # normalize_name uppercases; BANORTE stays
    banco_raw = str(out.get("banco_snapshot") or "").strip().upper()
    positive = int(out.get("amount_final_cents") or 0) > 0

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

    if not positive:
        out["included"] = 0
        out["row_state"] = "EXCLUDED"
        out["amount_final_cents"] = 0
        return out

    if banco_raw == "BANORTE":
        if match.auto_selected and match.selected_id:
            out["included"] = 1
            out["row_state"] = "OK"
        else:
            out["included"] = 0
            out["row_state"] = "NEEDS_REVIEW"
            warnings.append("banorte_sin_match")
    elif banco_raw == "":
        out["included"] = 0
        out["row_state"] = "NEEDS_REVIEW"
        warnings.append("banco_vacio")
    else:
        if match.auto_selected and match.selected_id:
            out["included"] = 1
            out["row_state"] = "OK"
            warnings.append("otro_banco_con_match_banorte")
        else:
            out["included"] = 0
            out["row_state"] = "EXCLUDED"
            warnings.append("otro_banco_sin_match")

    if match.kind in {"FUZZY_RECOMMENDATION", "EMPLOYEE_SECONDARY", "AMBIGUOUS"} and not match.auto_selected:
        out["row_state"] = "NEEDS_REVIEW"
        if out.get("included") == 1:
            out["included"] = 0
            out["amount_final_cents"] = int(out.get("amount_original_cents") or 0)
        warnings.append(f"match_{match.kind.lower()}")

    out["warnings"] = warnings
    out["warnings_json"] = json.dumps(warnings, ensure_ascii=False)
    # store candidate ids for UI without accounts in logs
    out["user_decision"] = {
        **(out.get("user_decision") or {}),
        "candidate_ids": [c.beneficiary_id for c in match.candidates[:5]],
        "match_message": match.message,
    }
    return out


def prepare_draft_rows(db_path: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for row in rows:
        m = resolve_row_match(db_path, row)
        prepared.append(apply_bank_rules(row, m))
    return prepared
