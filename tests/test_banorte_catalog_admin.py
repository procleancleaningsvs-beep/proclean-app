from __future__ import annotations

import io
import re
import sqlite3
from pathlib import Path

import pytest
from flask import Flask, g

from modules.nomina.banorte.beneficiary_service import (
    BeneficiaryError,
    apply_beneficiary_action,
    beneficiary_management_detail,
    list_beneficiaries,
    replace_beneficiary,
)
from modules.nomina.banorte.catalog_activation import (
    activate_catalog_version,
    catalog_activation_check,
)
from modules.nomina.banorte.catalog_admin_read_model import (
    get_catalog_comparison_row,
    list_catalog_comparison_rows,
    load_catalog_admin_overview,
)
from modules.nomina.banorte.catalog_application_plan import catalog_apply_preview
from modules.nomina.banorte.catalog_parser import CATALOG_HEADER_V1
from modules.nomina.banorte.catalog_reconciliation import pre_reconcile_catalog_version
from modules.nomina.banorte.catalog_service import (
    analyze_catalog_version,
    mark_catalog_ready_for_review,
    stage_catalog_version,
)
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.blueprint import register_nomina
from modules.nomina.db import ensure_nomina_tables


def _make_app(tmp_path: Path, role: str, *, db_path: str | None = None) -> Flask:
    repo = Path(__file__).resolve().parents[1]
    app = Flask(
        __name__,
        template_folder=str(repo / "templates"),
        static_folder=str(repo / "static"),
    )
    app.config.update(
        TESTING=True,
        SECRET_KEY="catalog-admin-test",
        DATABASE=db_path or str(tmp_path / f"{role}.db"),
    )
    conn = sqlite3.connect(app.config["DATABASE"])
    ensure_nomina_tables(conn)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, role TEXT, password_hash TEXT, created_at TEXT)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO users (id,username,role,password_hash,created_at) "
        "VALUES (1,'tester',?,'x','t')",
        (role,),
    )
    conn.commit()
    conn.close()

    @app.route("/login")
    def login():
        return "login"

    register_nomina(app)

    @app.before_request
    def _auth():
        g.user = {"id": 1, "username": "tester", "role": role}

    return app


def _token(html: bytes) -> str:
    text = html.decode("utf-8")
    for marker in ('data-csrf="', 'name="csrf_token" value="'):
        if marker in text:
            start = text.index(marker) + len(marker)
            return text[start : text.index('"', start)]
    raise AssertionError("csrf token not rendered")


def _row() -> list[str]:
    return [
        "0000000001",
        "PERSONA SINTETICA",
        "01/01/2026",
        "20/08/2026",
        "ADMIN",
        "01/01/1990",
        "SINT900101AA1",
        "1000",
        "900",
        "NUEVO LEON",
        "01/01/2020",
        "SEMANAL",
        "NUEVO LEON",
        "CUENTA BANORTE",
        "1111111111",
        "0",
        "ALTA",
        "INDIVIDUAL",
        "APLICADO",
        "REGISTRO ACEPTADO",
        "ADMIN",
        "",
        "",
        "",
    ]


def _payload() -> bytes:
    return "\n".join(
        [
            "FECHA: 20/ago./2026",
            "EMISORA: 67059 EMPRESA SINTETICA",
            "",
            "|".join(CATALOG_HEADER_V1) + "|",
            "|".join(_row()) + "|",
        ]
    ).encode("utf-8")


def _catalog_payload(
    *,
    report_header: str,
    employee: str,
    name: str,
    rfc: str,
    account: str,
) -> bytes:
    row = _row()
    row[0] = employee
    row[1] = name
    row[6] = rfc
    row[14] = account
    return "\n".join(
        [
            f"FECHA: {report_header}",
            "EMISORA: 67059 EMPRESA SINTETICA",
            "",
            "|".join(CATALOG_HEADER_V1) + "|",
            "|".join(row) + "|",
        ]
    ).encode("utf-8")


def _insert_beneficiary(
    db_path: str,
    *,
    name: str,
    employee: str,
    account: str,
    source_kind: str = "ALTA_MANUAL",
    validation_status: str = "IMPORTADO_EXITOSO",
    record_status: str = "ACTIVO",
    created_at: str = "2026-01-01T12:00:00+00:00",
) -> int:
    conn = connect(db_path)
    ensure_banorte_tables(conn)
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original,nombre_normalizado,curp,employee_number_requested,
            employee_number_effective,account_number,source_kind,validation_status,
            record_status,banorte_employee_substituted,manual_effective_from_account,
            imported_at,imported_by,replaces_id,created_at,updated_at
        ) VALUES (?,?,NULL,?,?,?,?,?,?,0,0,?,'tester',NULL,?,?)
        """,
        (
            name,
            name.upper(),
            employee,
            employee,
            account,
            source_kind,
            validation_status,
            record_status,
            created_at,
            created_at,
            created_at,
        ),
    )
    conn.commit()
    beneficiary_id = int(cur.lastrowid)
    conn.close()
    return beneficiary_id


def _activate_catalog(db_path: str) -> tuple[int, int]:
    staged = stage_catalog_version(
        db_path,
        raw=_catalog_payload(
            report_header="20/ago./2026",
            employee="0000000001",
            name="PERSONA OFICIAL",
            rfc="OFIC900101AA1",
            account="1111111111",
        ),
        filename="empleados-current.txt",
        actor="tester",
    )
    version_id = int(staged["id"])
    analyze_catalog_version(db_path, version_id, actor="tester")
    pre_reconcile_catalog_version(db_path, version_id, actor="tester")
    conn = connect(db_path)
    conn.execute(
        "UPDATE nomina_banorte_catalog_versions SET status='READY_FOR_REVIEW' WHERE id=?",
        (version_id,),
    )
    conn.commit()
    conn.close()
    activate_catalog_version(db_path, version_id, actor="tester")
    conn = connect(db_path)
    mirror_id = int(
        conn.execute(
            "SELECT beneficiary_id FROM nomina_banorte_catalog_reconciliations "
            "WHERE version_id=? AND is_current=1",
            (version_id,),
        ).fetchone()[0]
    )
    conn.close()
    return version_id, mirror_id


def _provenance_fixture(tmp_path: Path) -> tuple[str, dict[str, int]]:
    db_path = str(tmp_path / "provenance.db")
    conn = connect(db_path)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    conn.commit()
    conn.close()
    active_version_id, mirror_id = _activate_catalog(db_path)

    historical_id = _insert_beneficiary(
        db_path,
        name="PERSONA HISTORICA",
        employee="0000000002",
        account="2222222222",
    )
    historical = stage_catalog_version(
        db_path,
        raw=_catalog_payload(
            report_header="19/ago./2026",
            employee="0000000002",
            name="PERSONA HISTORICA",
            rfc="HIST900101AA1",
            account="2222222222",
        ),
        filename="empleados-superseded.txt",
        actor="tester",
    )
    analyze_catalog_version(db_path, int(historical["id"]), actor="tester")
    pre_reconcile_catalog_version(db_path, int(historical["id"]), actor="tester")
    conn = connect(db_path)
    conn.execute(
        "UPDATE nomina_banorte_catalog_versions SET status='SUPERSEDED' WHERE id=?",
        (int(historical["id"]),),
    )
    # Deliberate drift: the ACTIVE relationship must still dominate classification.
    conn.execute(
        "UPDATE nomina_banorte_beneficiaries "
        "SET nombre_original='MIRROR ALTERADO',nombre_normalizado='MIRROR ALTERADO' "
        "WHERE id=?",
        (mirror_id,),
    )
    conn.commit()
    conn.close()

    post_id = _insert_beneficiary(
        db_path,
        name="ALTA POST SNAPSHOT",
        employee="0000000003",
        account="3333333333",
        source_kind="REPORTE_DETALLADO",
        created_at="2026-08-21T12:00:00+00:00",
    )
    legacy_id = _insert_beneficiary(
        db_path,
        name="LEGACY INFORMATIVO",
        employee="0000000004",
        account="4444444444",
        created_at="2026-08-20T12:00:00+00:00",
    )
    inactive_id = _insert_beneficiary(
        db_path,
        name="VERSION INACTIVA",
        employee="0000000005",
        account="5555555555",
        record_status="INACTIVO_REEMPLAZADO",
        created_at="2026-08-22T12:00:00+00:00",
    )
    return db_path, {
        "active_version": active_version_id,
        "mirror": mirror_id,
        "post": post_id,
        "legacy": legacy_id,
        "inactive": inactive_id,
        "historical": historical_id,
    }


def test_provenance_classifies_active_post_legacy_inactive_and_superseded(tmp_path):
    db_path, ids = _provenance_fixture(tmp_path)

    mirror = beneficiary_management_detail(db_path, ids["mirror"])
    post = beneficiary_management_detail(db_path, ids["post"])
    legacy = beneficiary_management_detail(db_path, ids["legacy"])
    inactive = beneficiary_management_detail(db_path, ids["inactive"])
    historical = beneficiary_management_detail(db_path, ids["historical"])

    assert mirror["provenance"]["provenance_category"] == "A"
    assert mirror["provenance"]["catalog_scope"] == "ACTIVE"
    assert mirror["provenance"]["active_catalog_version_id"] == ids["active_version"]
    assert mirror["provenance"]["active_catalog_report_date"] == "2026-08-20"
    assert mirror["provenance"]["reconciliation_fresh"] is False
    assert mirror["beneficiary"]["display_name"] == "PERSONA OFICIAL"
    assert post["provenance"]["provenance_category"] == "B"
    assert post["provenance"]["post_snapshot"] is True
    assert legacy["provenance"]["provenance_category"] == "C"
    assert inactive["provenance"]["provenance_category"] == "D"
    assert historical["provenance"]["provenance_category"] == "C"
    assert historical["provenance"]["catalog_scope"] == "SUPERSEDED"


def test_current_active_mirror_is_fail_closed_through_both_replace_routes(tmp_path):
    db_path, ids = _provenance_fixture(tmp_path)
    mirror_id = ids["mirror"]
    conn = connect(db_path)
    before = dict(
        conn.execute(
            "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?", (mirror_id,)
        ).fetchone()
    )
    event_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM nomina_banorte_beneficiary_events WHERE beneficiary_id=?",
            (mirror_id,),
        ).fetchone()[0]
    )
    reconciliation = dict(
        conn.execute(
            "SELECT * FROM nomina_banorte_catalog_reconciliations WHERE beneficiary_id=?",
            (mirror_id,),
        ).fetchone()
    )
    beneficiary_count = int(
        conn.execute("SELECT COUNT(*) FROM nomina_banorte_beneficiaries").fetchone()[0]
    )
    conn.close()

    for action in ("mark_usable_manual", "keep_pending", "deactivate", "resolve_duplicate"):
        with pytest.raises(BeneficiaryError, match="beneficiary_action_disallowed_for_provenance"):
            apply_beneficiary_action(
                db_path,
                "tester",
                mirror_id,
                action=action,
                reason="intento bloqueado",
                winner_id=ids["post"] if action == "resolve_duplicate" else None,
            )
    with pytest.raises(BeneficiaryError, match="beneficiary_action_disallowed_for_provenance"):
        replace_beneficiary(
            db_path,
            "tester",
            mirror_id,
            nombre="NOMBRE ALTERADO",
            account="9999999999",
            employee_number_effective="9999999999",
            reason="intento directo",
        )

    client = _make_app(tmp_path, "admin", db_path=db_path).test_client()
    token = _token(client.get("/nomina/exportaciones/banorte").data)
    payload = {
        "csrf_token": token,
        "action": "replace",
        "reason": "intento route",
        "nombre": "NOMBRE ROUTE",
        "account": "8888888888",
        "employee_number_effective": "8888888888",
    }
    headers = {"X-CSRF-Token": token}
    actions_response = client.post(
        f"/nomina/exportaciones/banorte/beneficiarios/{mirror_id}/actions",
        json=payload,
        headers=headers,
    )
    direct_response = client.post(
        f"/nomina/exportaciones/banorte/beneficiarios/{mirror_id}/replace",
        json=payload,
        headers=headers,
    )
    assert actions_response.status_code == 409
    assert direct_response.status_code == 409, direct_response.get_json()

    conn = connect(db_path)
    assert dict(
        conn.execute(
            "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?", (mirror_id,)
        ).fetchone()
    ) == before
    assert int(conn.execute("SELECT COUNT(*) FROM nomina_banorte_beneficiaries").fetchone()[0]) == beneficiary_count
    assert int(
        conn.execute(
            "SELECT COUNT(*) FROM nomina_banorte_beneficiary_events WHERE beneficiary_id=?",
            (mirror_id,),
        ).fetchone()[0]
    ) == event_count
    assert dict(
        conn.execute(
            "SELECT * FROM nomina_banorte_catalog_reconciliations WHERE beneficiary_id=?",
            (mirror_id,),
        ).fetchone()
    ) == reconciliation
    conn.close()


def test_post_snapshot_actions_work_and_replace_requires_revalidation(tmp_path):
    db_path, ids = _provenance_fixture(tmp_path)
    replaced = apply_beneficiary_action(
        db_path,
        "tester",
        ids["post"],
        action="replace",
        reason="cambio de identidad operacional",
        nombre="ALTA POST CORREGIDA",
        account="3333333399",
        employee_number_effective="0000000099",
    )
    conn = connect(db_path)
    old = dict(
        conn.execute(
            "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?", (ids["post"],)
        ).fetchone()
    )
    successor = dict(
        conn.execute(
            "SELECT * FROM nomina_banorte_beneficiaries WHERE id=?", (replaced["id"],)
        ).fetchone()
    )
    events = conn.execute(
        "SELECT beneficiary_id,action,replacement_beneficiary_id FROM "
        "nomina_banorte_beneficiary_events WHERE beneficiary_id IN (?,?) ORDER BY id",
        (ids["post"], int(replaced["id"])),
    ).fetchall()
    conn.close()
    assert old["record_status"] == "INACTIVO_REEMPLAZADO"
    assert successor["source_kind"] == "ALTA_MANUAL"
    assert successor["validation_status"] == "MANUAL_PENDIENTE_VALIDACION"
    assert successor["manual_effective_from_account"] == 0
    assert successor["replaces_id"] == ids["post"]
    assert len(events) == 2

    usable_id = _insert_beneficiary(
        db_path,
        name="POST USABLE",
        employee="0000000006",
        account="6666666666",
        created_at="2026-08-22T12:00:00+00:00",
    )
    pending_id = _insert_beneficiary(
        db_path,
        name="POST PENDING",
        employee="0000000007",
        account="7777777777",
        created_at="2026-08-22T12:00:00+00:00",
    )
    deactivate_id = _insert_beneficiary(
        db_path,
        name="POST DEACTIVATE",
        employee="0000000008",
        account="8888888888",
        created_at="2026-08-22T12:00:00+00:00",
    )
    with pytest.raises(BeneficiaryError, match="identity_fields_only_allowed_for_replace"):
        apply_beneficiary_action(
            db_path,
            "tester",
            usable_id,
            action="mark_usable_manual",
            reason="payload oculto",
            nombre="CAMBIO OCULTO",
        )
    assert apply_beneficiary_action(
        db_path, "tester", usable_id, action="mark_usable_manual", reason="revisión manual"
    )["record_status"] == "ACTIVO"
    assert apply_beneficiary_action(
        db_path, "tester", pending_id, action="keep_pending", reason="continúa pendiente"
    )["validation_status"] == "MANUAL_PENDIENTE_VALIDACION"
    assert apply_beneficiary_action(
        db_path, "tester", deactivate_id, action="deactivate", reason="baja operacional"
    )["record_status"] == "INACTIVO_MANUAL"


def test_legacy_and_inactive_are_read_only_and_cannot_be_resurrected(tmp_path):
    db_path, ids = _provenance_fixture(tmp_path)
    for beneficiary_id in (ids["legacy"], ids["inactive"]):
        detail = beneficiary_management_detail(db_path, beneficiary_id)
        assert detail["action_policy"]["allowed_actions"] == []
        assert detail["chain"]
        for action in ("replace", "mark_usable_manual", "keep_pending", "deactivate", "resolve_duplicate"):
            with pytest.raises(BeneficiaryError, match="beneficiary_action_disallowed_for_provenance"):
                apply_beneficiary_action(
                    db_path,
                    "tester",
                    beneficiary_id,
                    action=action,
                    reason="intento bloqueado",
                    winner_id=ids["post"] if action == "resolve_duplicate" else None,
                )
    conn = connect(db_path)
    assert conn.execute(
        "SELECT record_status FROM nomina_banorte_beneficiaries WHERE id=?",
        (ids["legacy"],),
    ).fetchone()[0] == "ACTIVO"
    assert conn.execute(
        "SELECT COUNT(*) FROM nomina_banorte_beneficiaries WHERE replaces_id=?",
        (ids["inactive"],),
    ).fetchone()[0] == 0
    conn.close()


def test_admin_nomina_operator_parity_and_other_role_denied(tmp_path):
    db_path, ids = _provenance_fixture(tmp_path)
    second_post_id = _insert_beneficiary(
        db_path,
        name="SEGUNDA ALTA POST",
        employee="0000000009",
        account="9999999999",
        created_at="2026-08-22T12:00:00+00:00",
    )
    admin = _make_app(tmp_path, "admin", db_path=db_path).test_client()
    nomina = _make_app(tmp_path, "nomina", db_path=db_path).test_client()
    for client, beneficiary_id in ((admin, ids["post"]), (nomina, second_post_id)):
        page = client.get("/nomina/exportaciones/banorte")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert "Importar base" in html
        assert "Agregar beneficiarios" in html
        assert "Catálogo oficial" in html
        assert "banorte-ben-edit" in html
        token = _token(page.data)
        response = client.post(
            f"/nomina/exportaciones/banorte/beneficiarios/{beneficiary_id}/actions",
            json={
                "csrf_token": token,
                "action": "keep_pending",
                "reason": "paridad operativa",
            },
            headers={"X-CSRF-Token": token},
        )
        assert response.status_code == 200
    assert nomina.get("/nomina/exportaciones/banorte/catalogo").status_code == 200

    other = _make_app(tmp_path, "supervisor", db_path=db_path).test_client()
    assert other.get("/nomina/exportaciones/banorte").status_code == 403
    assert other.post(
        f"/nomina/exportaciones/banorte/beneficiarios/{second_post_id}/actions",
        json={"action": "keep_pending", "reason": "sin permiso"},
    ).status_code == 403


def test_a5c_current_scope_contains_only_current_a_and_b_before_pagination(tmp_path):
    db_path, ids = _provenance_fixture(tmp_path)
    for index in range(20):
        _insert_beneficiary(
            db_path,
            name=f"LEGACY PAGINACION {index:02d}",
            employee=f"{100 + index:010d}",
            account=f"{7000000000 + index:010d}",
            created_at="2026-08-20T12:00:00+00:00",
        )

    listing = list_beneficiaries(db_path, scope="current", page=1)

    assert listing["scope"] == "current"
    assert listing["total"] == 2
    assert {row["id"] for row in listing["rows"]} == {ids["mirror"], ids["post"]}
    assert {row["provenance_category"] for row in listing["rows"]} == {"A", "B"}
    mirror = next(row for row in listing["rows"] if row["id"] == ids["mirror"])
    assert mirror["display_name"] == "PERSONA OFICIAL"
    assert mirror["provenance"]["reconciliation_fresh"] is False
    empty = list_beneficiaries(db_path, scope="current", q_name="NO EXISTE")
    assert empty["total"] == 0
    assert empty["rows"] == []
    with pytest.raises(ValueError, match="invalid_scope"):
        list_beneficiaries(db_path, scope="mixed")


def test_a5c_historical_scope_contains_only_c_and_d(tmp_path):
    db_path, ids = _provenance_fixture(tmp_path)
    for index in range(20):
        _insert_beneficiary(
            db_path,
            name=f"ALTA VIGENTE {index:02d}",
            employee=f"{300 + index:010d}",
            account=f"{8000000000 + index:010d}",
            created_at="2026-08-22T12:00:00+00:00",
        )

    listing = list_beneficiaries(db_path, scope="historical", page=1)

    assert listing["scope"] == "historical"
    assert listing["total"] == 3
    assert {row["id"] for row in listing["rows"]} == {
        ids["legacy"],
        ids["inactive"],
        ids["historical"],
    }
    assert {row["provenance_category"] for row in listing["rows"]} == {"C", "D"}
    assert ids["mirror"] not in {row["id"] for row in listing["rows"]}
    assert ids["post"] not in {row["id"] for row in listing["rows"]}
    empty = list_beneficiaries(db_path, scope="historical", q_name="NO EXISTE")
    assert empty["total"] == 0
    assert empty["rows"] == []


def test_a5c_replacement_moves_old_b_to_history_and_keeps_successor_current(tmp_path):
    db_path, ids = _provenance_fixture(tmp_path)
    successor = apply_beneficiary_action(
        db_path,
        "tester",
        ids["post"],
        action="replace",
        reason="nueva identidad vigente",
        nombre="ALTA POST SUCESORA",
        account="3333333399",
        employee_number_effective="0000000099",
    )

    current_ids = {
        row["id"] for row in list_beneficiaries(db_path, scope="current")["rows"]
    }
    historical_ids = {
        row["id"] for row in list_beneficiaries(db_path, scope="historical")["rows"]
    }

    assert int(successor["id"]) in current_ids
    assert ids["post"] not in current_ids
    assert ids["post"] in historical_ids
    assert int(successor["id"]) not in historical_ids
    chain = beneficiary_management_detail(db_path, int(successor["id"]))["chain"]
    assert [version["id"] for version in chain] == [int(successor["id"]), ids["post"]]


def test_a5c_history_is_read_only_for_admin_and_nomina_and_other_role_denied(tmp_path):
    db_path, ids = _provenance_fixture(tmp_path)
    for role in ("admin", "nomina"):
        client = _make_app(tmp_path, role, db_path=db_path).test_client()
        page = client.get("/nomina/exportaciones/banorte")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert "Beneficiarios vigentes" in html
        assert "Legacy / Datos históricos anteriores" in html
        assert "banorte-hist-view" in html
        token = _token(page.data)
        historical = client.post(
            "/nomina/exportaciones/banorte/beneficiarios/search",
            json={"csrf_token": token, "scope": "historical", "page": 1},
            headers={"X-CSRF-Token": token},
        )
        assert historical.status_code == 200
        rows = historical.get_json()["listing"]["rows"]
        assert rows
        assert all(row["action_policy"]["allowed_actions"] == [] for row in rows)
        blocked = client.post(
            f"/nomina/exportaciones/banorte/beneficiarios/{ids['legacy']}/actions",
            json={
                "csrf_token": token,
                "action": "deactivate",
                "reason": "histórico no mutable",
            },
            headers={"X-CSRF-Token": token},
        )
        assert blocked.status_code == 409

    other = _make_app(tmp_path, "supervisor", db_path=db_path).test_client()
    assert other.get("/nomina/exportaciones/banorte").status_code == 403
    assert other.post(
        "/nomina/exportaciones/banorte/beneficiarios/search",
        json={"scope": "historical", "page": 1},
    ).status_code == 403
    editor_js = (
        Path(__file__).resolve().parents[1]
        / "static"
        / "nomina"
        / "exportaciones_banorte_editor.js"
    ).read_text(encoding="utf-8")
    assert "Cadena de versiones" in editor_js
    assert "No hay datos históricos anteriores." in editor_js


def test_a2b_current_scroll_uses_current_a_b_official_values_without_visual_pager(tmp_path):
    db_path, ids = _provenance_fixture(tmp_path)
    for index in range(3):
        _insert_beneficiary(
            db_path,
            name=f"ALTA VALIDADA RECIENTE {index}",
            employee=f"{40 + index:010d}",
            account=f"{4000000000 + index:010d}",
            created_at="2026-08-23T12:00:00+00:00",
        )
    pending_id = _insert_beneficiary(
        db_path,
        name="ALTA PENDIENTE RECIENTE",
        employee="0000000049",
        account="4900000000",
        validation_status="MANUAL_PENDIENTE_VALIDACION",
        created_at="2026-08-24T12:00:00+00:00",
    )

    page = _make_app(tmp_path, "admin", db_path=db_path).test_client().get(
        "/nomina/exportaciones/banorte"
    )
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    workspace = html.split('id="banorte-beneficiary-workspace"', 1)[1].split(
        'id="banorte-reporte-form"', 1
    )[0]
    current = workspace.split('id="banorte-beneficiary-history-viewport"', 1)[1].split(
        'id="banorte-beneficiary-pending-rows"', 1
    )[0]
    expected = list_beneficiaries(
        db_path, scope="current", page=1, sort="id_desc"
    )["rows"][:6]

    assert current.count("data-history-ben-id=") == 6
    assert [row["id"] for row in expected] == [
        pending_id,
        pending_id - 1,
        pending_id - 2,
        pending_id - 3,
        ids["post"],
        ids["mirror"],
    ]
    assert [int(value) for value in re.findall(r'data-history-ben-id="(\d+)"', current)] == [
        row["id"] for row in reversed(expected)
    ]
    assert all(f'data-history-ben-id="{row["id"]}"' in current for row in expected)
    assert "PERSONA OFICIAL" in current
    assert "TXT activo" in current
    assert "Alta posterior · Validada" in current
    assert "Alta posterior · Pendiente de validación" in current
    assert "LEGACY SIN CATALOGO" not in current
    assert "INACTIVO HISTORICO" not in current
    assert 'data-next-page="2"' in current
    assert 'data-has-next="0"' in current
    assert "Anterior" not in workspace
    assert "Siguiente" not in workspace
    assert "Página " not in workspace

    editor_js = (
        Path(__file__).resolve().parents[1]
        / "static"
        / "nomina"
        / "exportaciones_banorte_editor.js"
    ).read_text(encoding="utf-8")
    assert 'scope: "current"' in editor_js
    assert 'sort: "id_desc"' in editor_js
    assert "historyGeneration" in editor_js
    assert "historyLoadedPages" in editor_js
    assert "historyHasNext" in editor_js
    assert "banorte-beneficiary-history-viewport" in editor_js
    assert "viewport.insertBefore(fragment, viewport.firstChild)" in editor_js
    assert "viewport.scrollHeight - previousScrollHeight" in editor_js
    assert "historyViewport.scrollTop > 70" in editor_js


def test_a2b_unified_workspace_has_one_entry_and_no_legacy_manual_sections(tmp_path):
    html = _make_app(tmp_path, "admin").test_client().get(
        "/nomina/exportaciones/banorte"
    ).get_data(as_text=True)
    workspace = html.split('id="banorte-beneficiary-workspace"', 1)[1].split(
        'id="banorte-reporte-form"', 1
    )[0]

    assert "Número de empleado" in workspace
    assert "Nombre completo" in workspace
    assert "Número de cuenta" in workspace
    assert workspace.count('data-beneficiary-entry-row="1"') == 1
    assert 'id="banorte-beneficiary-history-viewport"' in workspace
    assert 'class="banorte-beneficiary-local-region"' in workspace
    assert 'id="banorte-beneficiary-pending-rows"' in workspace
    assert 'id="banorte-beneficiary-entry-row"' in workspace
    assert 'id="banorte-available-emps"' in workspace
    assert 'id="banorte-beneficiary-add">Añadir beneficiario a la lista<' in workspace
    assert 'id="banorte-beneficiary-save">Guardar beneficiarios<' in workspace
    assert ">AÑADIR BENEFICIARIO A LA LISTA<" not in workspace
    assert ">GUARDAR BENEFICIARIOS<" not in workspace
    assert "Usar cuenta como número" in workspace
    assert "Beneficiarios recientes" not in workspace
    assert "Nuevos beneficiarios pendientes" not in workspace
    assert 'id="banorte-alta-form"' not in workspace
    assert 'id="banorte-batch-table"' not in workspace
    assert 'id="banorte-ben-pager"' not in workspace


def test_a2b_local_actions_use_grid_controller_and_only_save_can_post():
    root = Path(__file__).resolve().parents[1]
    js = (
        root
        / "static"
        / "nomina"
        / "exportaciones_banorte_editor.js"
    ).read_text(encoding="utf-8")
    grid_js = (
        root / "static" / "nomina" / "banorte_beneficiary_grid.js"
    ).read_text(encoding="utf-8")

    assert "BanorteBeneficiaryGrid" in js
    assert "getPendingPayload" in js
    assert "entryHasAnyValue" in js
    assert "Añade primero el beneficiario activo a la lista antes de guardar." in js
    assert "/nomina/exportaciones/banorte/beneficiarios/manual-save" in js
    assert "/nomina/exportaciones/banorte/beneficiarios/batches/open" in js
    assert "ensureStagingBatch" not in js
    assert "/rows/" not in grid_js
    assert "fetch(" not in grid_js
    assert "locallyUsedEffectiveEmployees" in grid_js
    assert "applyAvailableNumber" in grid_js


def test_a5d_current_visible_ids_and_metric_follow_scoped_pagination(tmp_path):
    db_path, ids = _provenance_fixture(tmp_path)
    for index in range(16):
        _insert_beneficiary(
            db_path,
            name=f"ALTA VIGENTE A5D {index:02d}",
            employee=f"{500 + index:010d}",
            account=f"{8500000000 + index:010d}",
            created_at="2026-08-22T12:00:00+00:00",
        )

    first = list_beneficiaries(db_path, scope="current", page=1, sort="id_desc")
    second = list_beneficiaries(db_path, scope="current", page=2, sort="id_desc")
    filtered = list_beneficiaries(
        db_path, scope="current", page=1, q_name="A5D 0", sort="id_desc"
    )
    historical = list_beneficiaries(db_path, scope="historical", page=1)

    assert first["total"] == 18
    assert (first["start_index"], first["end_index"]) == (1, 15)
    assert (second["start_index"], second["end_index"]) == (16, 18)
    assert filtered["total"] == 10
    assert (filtered["start_index"], filtered["end_index"]) == (1, 10)
    assert historical["total"] == 3
    assert ids["legacy"] not in {row["id"] for row in first["rows"] + second["rows"]}

    html = _make_app(tmp_path, "admin", db_path=db_path).test_client().get(
        "/nomina/exportaciones/banorte"
    ).get_data(as_text=True)
    current_table = html.split('id="banorte-ben-table"', 1)[1].split("</table>", 1)[0]
    assert re.findall(r'data-visible-id="(\d+)"', current_table) == [
        str(value) for value in range(1, 16)
    ]
    assert "Beneficiarios vigentes: 18" in html

    editor_js = (
        Path(__file__).resolve().parents[1]
        / "static"
        / "nomina"
        / "exportaciones_banorte_editor.js"
    ).read_text(encoding="utf-8")
    assert "const visibleId = (listing.start_index || 0) + index" in editor_js
    assert '"Beneficiarios vigentes: " + (listing.total || 0)' in editor_js


def test_a5d_legacy_control_is_visible_for_admin_and_nomina_and_switches_scope(tmp_path):
    db_path, ids = _provenance_fixture(tmp_path)
    for role in ("admin", "nomina"):
        client = _make_app(tmp_path, role, db_path=db_path).test_client()
        page = client.get("/nomina/exportaciones/banorte")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        current_card = html.split('id="banorte-beneficiarios-embed"', 1)[1].split(
            "</section>", 1
        )[0]
        legacy_panel = html.split('id="banorte-tab-legacy-beneficiarios"', 1)[1].split(
            "</section>", 1
        )[0]
        assert 'data-banorte-tab="legacy-beneficiarios"' in current_card
        assert "Legacy / Datos históricos anteriores" in current_card
        assert 'data-banorte-tab="hub"' in legacy_panel
        assert "Volver a Beneficiarios vigentes" in legacy_panel

        token = _token(page.data)
        response = client.post(
            "/nomina/exportaciones/banorte/beneficiarios/search",
            json={"csrf_token": token, "scope": "historical", "page": 1},
            headers={"X-CSRF-Token": token},
        )
        assert response.status_code == 200
        rows = response.get_json()["listing"]["rows"]
        assert {row["provenance_category"] for row in rows} == {"C", "D"}
        assert ids["mirror"] not in {row["id"] for row in rows}
        assert ids["post"] not in {row["id"] for row in rows}


def test_a5d_historical_interface_is_detail_only_and_keeps_a5a_guard(tmp_path):
    db_path, ids = _provenance_fixture(tmp_path)
    client = _make_app(tmp_path, "admin", db_path=db_path).test_client()
    page = client.get("/nomina/exportaciones/banorte")
    html = page.get_data(as_text=True)
    legacy_panel = html.split('id="banorte-tab-legacy-beneficiarios"', 1)[1].split(
        'data-banorte-panel="import-base"', 1
    )[0]

    assert "Referencia técnica" in legacy_panel
    assert "Ver detalle" in legacy_panel
    assert "Editar" not in legacy_panel
    assert "Marcar utilizable" not in legacy_panel
    assert "Mantener pendiente" not in legacy_panel
    assert "Desactivar" not in legacy_panel
    assert "Resolver duplicado" not in legacy_panel

    token = _token(page.data)
    listing = client.post(
        "/nomina/exportaciones/banorte/beneficiarios/search",
        json={"csrf_token": token, "scope": "historical", "page": 1},
        headers={"X-CSRF-Token": token},
    ).get_json()["listing"]
    assert all(row["action_policy"]["allowed_actions"] == [] for row in listing["rows"])

    detail = client.get(
        f"/nomina/exportaciones/banorte/beneficiarios/{ids['legacy']}/history"
    )
    assert detail.status_code == 200
    detail_json = detail.get_json()
    assert detail_json["action_policy"]["allowed_actions"] == []
    assert "events" in detail_json
    assert "chain" in detail_json

    blocked = client.post(
        f"/nomina/exportaciones/banorte/beneficiarios/{ids['legacy']}/actions",
        json={
            "csrf_token": token,
            "action": "deactivate",
            "reason": "histórico no mutable",
        },
        headers={"X-CSRF-Token": token},
    )
    assert blocked.status_code == 409

    other = _make_app(tmp_path, "supervisor", db_path=db_path).test_client()
    assert other.get("/nomina/exportaciones/banorte").status_code == 403
    assert other.post(
        "/nomina/exportaciones/banorte/beneficiarios/search",
        json={"scope": "historical", "page": 1},
    ).status_code == 403


def _ready_c3b_target(
    db_path: str,
    *,
    employee: str = "0000000099",
    name: str = "IDENTIDAD RENOMBRADA",
    rfc: str = "NUEV910202AA1",
    account: str = "9999999999",
) -> int:
    staged = stage_catalog_version(
        db_path,
        raw=_catalog_payload(
            report_header="30/ago./2026",
            employee=employee,
            name=name,
            rfc=rfc,
            account=account,
        ),
        filename="empleados-c3b.txt",
        actor="tester",
    )
    version_id = int(staged["id"])
    analyze_catalog_version(db_path, version_id, actor="tester")
    mark_catalog_ready_for_review(db_path, version_id, actor="tester")
    return version_id


def test_c3b_check_1_apply_availability_comes_from_backend_and_requires_acknowledgement(
    tmp_path,
):
    app = _make_app(tmp_path, "admin")
    _activate_catalog(app.config["DATABASE"])
    version_id = _ready_c3b_target(app.config["DATABASE"])
    preview = catalog_apply_preview(app.config["DATABASE"], version_id)
    assert preview["can_apply"] is True
    assert preview["lineage_unconfirmed_count"] == 1

    page = app.test_client().get(
        f"/nomina/exportaciones/banorte/catalogo?version_id={version_id}"
    )
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert 'data-catalog-apply-form' in html
    assert 'data-apply-authorized="true"' in html
    assert 'name="preview_fingerprint"' in html
    assert preview["preview_fingerprint"] in html
    assert 'name="acknowledge_impact"' in html
    assert "Revisé el impacto y entiendo que este archivo sustituirá al catálogo vigente." in html
    assert re.search(r'data-apply-button[^>]*disabled', html)
    assert "identidades nuevas y separadas" in html

    duplicate = _row()
    duplicate[1] = "OTRA PERSONA"
    duplicate[6] = "OTRA900101AA1"
    duplicate[14] = "3333333333"
    conflict_payload = "\n".join(
        [
            "FECHA: 31/ago./2026",
            "EMISORA: 67059 EMPRESA SINTETICA",
            "",
            "|".join(CATALOG_HEADER_V1) + "|",
            "|".join(_row()) + "|",
            "|".join(duplicate) + "|",
        ]
    ).encode("utf-8")
    conflict = stage_catalog_version(
        app.config["DATABASE"], raw=conflict_payload, filename="conflict-c3b.txt", actor="tester"
    )
    analyze_catalog_version(app.config["DATABASE"], conflict["id"], actor="tester")
    mark_catalog_ready_for_review(app.config["DATABASE"], conflict["id"], actor="tester")
    blocked = app.test_client().get(
        f"/nomina/exportaciones/banorte/catalogo?version_id={conflict['id']}"
    ).get_data(as_text=True)
    assert 'data-apply-authorized="false"' in blocked
    assert "Conflictos que requieren atención" in blocked
    assert "Aplicar de todos modos" not in blocked
    assert ">Forzar<" not in blocked


def test_c3b_check_2_successful_activation_uses_c2_and_is_naturally_single_use(tmp_path):
    app = _make_app(tmp_path, "admin")
    prior_id, _ = _activate_catalog(app.config["DATABASE"])
    version_id = _ready_c3b_target(app.config["DATABASE"])
    preview = catalog_apply_preview(app.config["DATABASE"], version_id)
    client = app.test_client()
    token = _token(
        client.get(
            f"/nomina/exportaciones/banorte/catalogo?version_id={version_id}"
        ).data
    )
    exact_check = client.get(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/activation-check",
        headers={"X-Catalog-Preview-Fingerprint": preview["preview_fingerprint"]},
    )
    assert exact_check.status_code == 200
    assert exact_check.get_json()["retry_allowed"] is True
    stale_check = client.get(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/activation-check",
        headers={"X-Catalog-Preview-Fingerprint": "stale-preview"},
    )
    assert stale_check.status_code == 200
    assert stale_check.get_json()["retry_allowed"] is False
    payload = {
        "csrf_token": token,
        "preview_fingerprint": preview["preview_fingerprint"],
        "acknowledge_impact": "yes",
    }
    activated = client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/activate",
        json=payload,
        headers={"X-CSRF-Token": token, "Accept": "application/json"},
    )
    assert activated.status_code == 200
    assert activated.get_json()["message"] == "El nuevo catálogo Banorte quedó vigente."
    assert activated.get_json()["redirect_url"].endswith(
        "/nomina/exportaciones/banorte/catalogo"
    )

    conn = connect(app.config["DATABASE"])
    assert conn.execute(
        "SELECT status FROM nomina_banorte_catalog_versions WHERE id=?", (version_id,)
    ).fetchone()[0] == "ACTIVE"
    assert conn.execute(
        "SELECT status FROM nomina_banorte_catalog_versions WHERE id=?", (prior_id,)
    ).fetchone()[0] == "SUPERSEDED"
    bound = int(
        conn.execute(
            "SELECT COUNT(*) FROM nomina_banorte_catalog_reconciliations "
            "WHERE version_id=? AND reconciliation_status='CATALOG_BOUND'",
            (version_id,),
        ).fetchone()[0]
    )
    events = int(
        conn.execute(
            "SELECT COUNT(*) FROM nomina_banorte_catalog_events "
            "WHERE version_id=? AND event_type='VERSION_ACTIVATED'",
            (version_id,),
        ).fetchone()[0]
    )
    assert bound == 1
    assert events == 1
    conn.close()

    second_token = activated.get_json()["csrf_token"]
    second = client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/activate",
        json={**payload, "csrf_token": second_token},
        headers={"X-CSRF-Token": second_token, "Accept": "application/json"},
    )
    assert second.status_code == 409
    assert second.get_json()["ok"] is False
    conn = connect(app.config["DATABASE"])
    assert conn.execute(
        "SELECT COUNT(*) FROM nomina_banorte_catalog_events "
        "WHERE version_id=? AND event_type='VERSION_ACTIVATED'",
        (version_id,),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM nomina_banorte_catalog_reconciliations "
        "WHERE version_id=? AND reconciliation_status='CATALOG_BOUND'",
        (version_id,),
    ).fetchone()[0] == bound
    conn.close()

    missing_csrf = client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/activate",
        json={
            "preview_fingerprint": preview["preview_fingerprint"],
            "acknowledge_impact": "yes",
        },
    )
    assert missing_csrf.status_code == 403

    acknowledgement_token = second.get_json()["csrf_token"]
    missing_acknowledgement = client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/activate",
        json={
            "csrf_token": acknowledgement_token,
            "preview_fingerprint": preview["preview_fingerprint"],
        },
        headers={
            "X-CSRF-Token": acknowledgement_token,
            "Accept": "application/json",
        },
    )
    assert missing_acknowledgement.status_code == 400
    assert missing_acknowledgement.get_json()["code"] == (
        "impact_acknowledgement_required"
    )

    denied = _make_app(tmp_path, "supervisor", db_path=app.config["DATABASE"]).test_client()
    assert denied.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/activate",
        json=payload,
    ).status_code == 403


def test_c3b_check_3_stale_preview_and_conflict_fail_without_partial_writes(tmp_path):
    app = _make_app(tmp_path, "admin")
    prior_id, prior_beneficiary_id = _activate_catalog(app.config["DATABASE"])
    version_id = _ready_c3b_target(app.config["DATABASE"])
    preview = catalog_apply_preview(app.config["DATABASE"], version_id)
    client = app.test_client()
    token = _token(
        client.get(
            f"/nomina/exportaciones/banorte/catalogo?version_id={version_id}"
        ).data
    )
    conn = connect(app.config["DATABASE"])
    conn.execute(
        "UPDATE nomina_banorte_beneficiaries SET account_number='8888888888' WHERE id=?",
        (prior_beneficiary_id,),
    )
    conn.commit()
    before = {
        "active": conn.execute(
            "SELECT id FROM nomina_banorte_catalog_versions WHERE status='ACTIVE'"
        ).fetchone()[0],
        "beneficiaries": conn.execute(
            "SELECT COUNT(*) FROM nomina_banorte_beneficiaries"
        ).fetchone()[0],
        "bound": conn.execute(
            "SELECT COUNT(*) FROM nomina_banorte_catalog_reconciliations "
            "WHERE reconciliation_status='CATALOG_BOUND'"
        ).fetchone()[0],
    }
    conn.close()

    stale = client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/activate",
        json={
            "csrf_token": token,
            "preview_fingerprint": preview["preview_fingerprint"],
            "acknowledge_impact": "yes",
        },
        headers={"X-CSRF-Token": token, "Accept": "application/json"},
    )
    assert stale.status_code == 409
    stale_json = stale.get_json()
    assert stale_json["message"] == (
        "La información cambió después de la comparación. "
        "Vuelve a analizar el archivo antes de aplicarlo."
    )
    assert stale_json["preview_invalidated"] is True
    assert "PREVIEW_FINGERPRINT_DRIFT" not in stale.get_data(as_text=True)

    conn = connect(app.config["DATABASE"])
    after = {
        "active": conn.execute(
            "SELECT id FROM nomina_banorte_catalog_versions WHERE status='ACTIVE'"
        ).fetchone()[0],
        "beneficiaries": conn.execute(
            "SELECT COUNT(*) FROM nomina_banorte_beneficiaries"
        ).fetchone()[0],
        "bound": conn.execute(
            "SELECT COUNT(*) FROM nomina_banorte_catalog_reconciliations "
            "WHERE reconciliation_status='CATALOG_BOUND'"
        ).fetchone()[0],
    }
    assert after == before
    assert after["active"] == prior_id
    assert conn.execute(
        "SELECT status FROM nomina_banorte_catalog_versions WHERE id=?", (version_id,)
    ).fetchone()[0] == "READY_FOR_REVIEW"
    conn.close()

    duplicate = _row()
    duplicate[1] = "OTRA PERSONA"
    duplicate[6] = "OTRA900101AA1"
    duplicate[14] = "3333333333"
    conflict_payload = "\n".join(
        [
            "FECHA: 31/ago./2026",
            "EMISORA: 67059 EMPRESA SINTETICA",
            "",
            "|".join(CATALOG_HEADER_V1) + "|",
            "|".join(_row()) + "|",
            "|".join(duplicate) + "|",
        ]
    ).encode("utf-8")
    conflict = stage_catalog_version(
        app.config["DATABASE"],
        raw=conflict_payload,
        filename="blocked-c3b.txt",
        actor="tester",
    )
    analyze_catalog_version(app.config["DATABASE"], conflict["id"], actor="tester")
    mark_catalog_ready_for_review(app.config["DATABASE"], conflict["id"], actor="tester")
    blocked_preview = catalog_apply_preview(app.config["DATABASE"], conflict["id"])
    assert blocked_preview["can_apply"] is False
    blocked = client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{conflict['id']}/activate",
        json={
            "csrf_token": stale_json["csrf_token"],
            "preview_fingerprint": blocked_preview["preview_fingerprint"],
            "acknowledge_impact": "yes",
        },
        headers={
            "X-CSRF-Token": stale_json["csrf_token"],
            "Accept": "application/json",
        },
    )
    assert blocked.status_code == 409
    assert blocked.get_json()["message"] == "No fue posible aplicar el catálogo. Corrige el archivo y vuelve a analizarlo."

    base_changed_path = tmp_path / "base-changed"
    base_changed_path.mkdir()
    base_changed_app = _make_app(base_changed_path, "admin")
    _activate_catalog(base_changed_app.config["DATABASE"])
    viewed_target_id = _ready_c3b_target(base_changed_app.config["DATABASE"])
    viewed_preview = catalog_apply_preview(
        base_changed_app.config["DATABASE"], viewed_target_id
    )
    base_changed_client = base_changed_app.test_client()
    viewed_token = _token(
        base_changed_client.get(
            f"/nomina/exportaciones/banorte/catalogo?version_id={viewed_target_id}"
        ).data
    )
    concurrent_target_id = _ready_c3b_target(
        base_changed_app.config["DATABASE"],
        employee="0000000088",
        name="OTRA BASE VIGENTE",
        rfc="OTRA920303BB2",
        account="8888888888",
    )
    concurrent_preview = catalog_apply_preview(
        base_changed_app.config["DATABASE"], concurrent_target_id
    )
    activate_catalog_version(
        base_changed_app.config["DATABASE"],
        concurrent_target_id,
        actor="other-operator",
        expected_preview_fingerprint=concurrent_preview["preview_fingerprint"],
    )
    base_changed = base_changed_client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{viewed_target_id}/activate",
        json={
            "csrf_token": viewed_token,
            "preview_fingerprint": viewed_preview["preview_fingerprint"],
            "acknowledge_impact": "yes",
        },
        headers={"X-CSRF-Token": viewed_token, "Accept": "application/json"},
    )
    assert base_changed.status_code == 409
    assert "Vuelve a analizar" in base_changed.get_json()["message"]
    conn = connect(base_changed_app.config["DATABASE"])
    assert conn.execute(
        "SELECT id FROM nomina_banorte_catalog_versions WHERE status='ACTIVE'"
    ).fetchone()[0] == concurrent_target_id
    assert conn.execute(
        "SELECT status FROM nomina_banorte_catalog_versions WHERE id=?",
        (viewed_target_id,),
    ).fetchone()[0] == "READY_FOR_REVIEW"
    conn.close()


def test_c3b_check_4_manual_continuity_review_is_authorized_audited_and_refreshes_preview(
    tmp_path,
):
    app = _make_app(tmp_path, "admin")
    _activate_catalog(app.config["DATABASE"])
    version_id = _ready_c3b_target(app.config["DATABASE"])
    before_preview = catalog_apply_preview(app.config["DATABASE"], version_id)
    target_action = before_preview["actions"][0]
    assert target_action["lineage_status"] == "UNCONFIRMED"
    row_key = f"target-{target_action['person_id']}"
    comparison = list_catalog_comparison_rows(app.config["DATABASE"], version_id)
    row = next(item for item in comparison.items if item["row_key"] == row_key)
    assert row["resolution_available"] is True

    client = app.test_client()
    page = client.get(
        f"/nomina/exportaciones/banorte/catalogo?version_id={version_id}"
    )
    token = _token(page.data)
    candidates = client.get(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/comparison/{row_key}/lineage-candidates",
        headers={"X-Catalog-Search": "PERSONA OFICIAL", "Accept": "application/json"},
    )
    assert candidates.status_code == 200
    candidate_json = candidates.get_json()
    assert candidate_json["total"] == 1
    candidate = candidate_json["items"][0]
    assert candidate["person"]["account_masked"].endswith("1111")
    assert candidate["person"]["account_masked"] != "1111111111"
    assert "1111111111" not in candidates.get_data(as_text=True)
    assert "material_fingerprint" not in candidates.get_data(as_text=True)
    assert set(candidate["evidence"]) == {"name", "employee", "account", "rfc", "birth_date"}

    missing_csrf = client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/comparison/{row_key}/confirm-continuity",
        json={
            "candidate_id": candidate["candidate_id"],
            "reason": "La mutación sin CSRF debe rechazarse",
        },
    )
    assert missing_csrf.status_code == 403

    unauthorized = client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/comparison/{row_key}/confirm-continuity",
        json={
            "csrf_token": token,
            "candidate_id": 999999,
            "reason": "No debe aceptar un candidato ajeno al plan",
        },
        headers={"X-CSRF-Token": token, "Accept": "application/json"},
    )
    assert unauthorized.status_code == 409
    assert unauthorized.get_json()["code"] == "relation_changed"
    token = unauthorized.get_json()["csrf_token"]

    confirmed = client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/comparison/{row_key}/confirm-continuity",
        json={
            "csrf_token": token,
            "candidate_id": candidate["candidate_id"],
            "reason": "Expediente interno revisado por Nómina",
        },
        headers={"X-CSRF-Token": token, "Accept": "application/json"},
    )
    assert confirmed.status_code == 200
    assert confirmed.get_json()["message"] == "Relación confirmada. Los datos del nuevo archivo serán los vigentes."
    assert confirmed.get_json()["preview_fingerprint"] != before_preview["preview_fingerprint"]

    conn = connect(app.config["DATABASE"])
    reconciliation = conn.execute(
        "SELECT * FROM nomina_banorte_catalog_reconciliations "
        "WHERE version_id=? AND person_id=? AND is_current=1",
        (version_id, target_action["person_id"]),
    ).fetchone()
    assert reconciliation["match_method"] == "MANUAL_CONTINUITY_CONFIRMED"
    assert reconciliation["lineage_status"] == "CONFIRMED"
    assert reconciliation["manual_reason"] == "Expediente interno revisado por Nómina"
    assert reconciliation["created_by"] == "tester"
    assert reconciliation["lineage_evidence_sha256"]
    assert conn.execute(
        "SELECT status FROM nomina_banorte_catalog_versions WHERE id=?", (version_id,)
    ).fetchone()[0] == "READY_FOR_REVIEW"
    conn.close()

    stale_candidate = client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/comparison/{row_key}/confirm-continuity",
        json={
            "csrf_token": confirmed.get_json()["csrf_token"],
            "candidate_id": candidate["candidate_id"],
            "reason": "Segundo intento no permitido",
        },
        headers={
            "X-CSRF-Token": confirmed.get_json()["csrf_token"],
            "Accept": "application/json",
        },
    )
    assert stale_candidate.status_code == 409
    assert "MANUAL_CONTINUITY" not in stale_candidate.get_data(as_text=True)
    denied = _make_app(
        tmp_path,
        "supervisor",
        db_path=app.config["DATABASE"],
    ).test_client()
    assert denied.get(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/comparison/{row_key}/lineage-candidates"
    ).status_code == 403
    assert denied.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/comparison/{row_key}/confirm-continuity",
        json={"candidate_id": candidate["candidate_id"], "reason": "No autorizado"},
    ).status_code == 403
    source = Path(__file__).resolve().parents[1].joinpath(
        "modules", "nomina", "banorte", "routes.py"
    ).read_text(encoding="utf-8")
    assert "confirm-distinct" not in source
    assert "manual-distinct" not in source


def test_c3a_check_1_overview_status_mapping_and_retired_engineering_copy(tmp_path):
    app = _make_app(tmp_path, "admin")
    client = app.test_client()
    page = client.get("/nomina/exportaciones/banorte/catalogo")
    assert page.status_code == 200
    assert page.headers["Cache-Control"] == "private, no-store"
    html = page.data.decode("utf-8")
    assert "Catálogo Banorte" in html
    assert "Aún no hay un catálogo Banorte vigente." in html
    assert "Actualizar catálogo" in html
    assert "Historial de versiones" in html
    retired = (
        "Release 2A",
        "Release 2B",
        "infraestructura dormida",
        ">STAGED<",
        ">READY_FOR_REVIEW<",
        ">ACTIVE<",
        ">SUPERSEDED<",
        "Reconciliación manual controlada",
        "Person ID",
        "Beneficiary ID",
        "Activation-check",
        "reconciliation_pending",
        "fingerprint",
        "0 ACTIVE",
    )
    assert all(copy not in html for copy in retired)

    active_version_id, _ = _activate_catalog(app.config["DATABASE"])
    active_page = client.get("/nomina/exportaciones/banorte/catalogo")
    active_html = active_page.get_data(as_text=True)
    assert active_page.status_code == 200
    assert "CATÁLOGO VIGENTE" in active_html
    assert "Fecha del archivo" in active_html
    assert "Aplicado el" in active_html
    assert ">Vigente<" in active_html
    overview = load_catalog_admin_overview(app.config["DATABASE"])
    assert overview.active_version_id == active_version_id
    assert overview.active["status_label"] == "Vigente"


def test_c3a_check_2_upload_analyze_orchestration_never_activates_and_failure_is_safe(
    tmp_path, monkeypatch
):
    import modules.nomina.banorte.routes as banorte_routes

    app = _make_app(tmp_path, "admin")
    active_version_id, _ = _activate_catalog(app.config["DATABASE"])
    client = app.test_client()
    token = _token(client.get("/nomina/exportaciones/banorte/catalogo").data)
    conn = connect(app.config["DATABASE"])
    before_bound = int(
        conn.execute(
            "SELECT COUNT(*) FROM nomina_banorte_catalog_reconciliations "
            "WHERE reconciliation_status='CATALOG_BOUND'"
        ).fetchone()[0]
    )
    conn.close()

    uploaded = client.post(
        "/nomina/exportaciones/banorte/catalogo/versions",
        data={
            "csrf_token": token,
            "file": (
                io.BytesIO(
                    _catalog_payload(
                        report_header="30/ago./2026",
                        employee="0000000099",
                        name="PERSONA NUEVA",
                        rfc="PNUE900101AA1",
                        account="9999999999",
                    )
                ),
                "empleados-new.txt",
            ),
        },
        content_type="multipart/form-data",
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
    )
    assert uploaded.status_code == 200
    version_id = uploaded.get_json()["version_id"]
    conn = connect(app.config["DATABASE"])
    assert conn.execute(
        "SELECT status FROM nomina_banorte_catalog_versions WHERE id=?", (version_id,)
    ).fetchone()[0] == "READY_FOR_REVIEW"
    assert conn.execute(
        "SELECT id FROM nomina_banorte_catalog_versions WHERE status='ACTIVE'"
    ).fetchone()[0] == active_version_id
    assert conn.execute(
        "SELECT COUNT(*) FROM nomina_banorte_catalog_reconciliations "
        "WHERE reconciliation_status='CATALOG_BOUND'"
    ).fetchone()[0] == before_bound
    assert conn.execute(
        "SELECT COUNT(*) FROM nomina_banorte_catalog_events WHERE event_type='VERSION_ACTIVATED'"
    ).fetchone()[0] == 1
    conn.close()

    def fail_analysis(*_args, **_kwargs):
        raise RuntimeError("synthetic analysis failure")

    monkeypatch.setattr(banorte_routes, "analyze_catalog_version", fail_analysis)
    failed = client.post(
        "/nomina/exportaciones/banorte/catalogo/versions",
        data={
            "csrf_token": uploaded.get_json()["csrf_token"],
            "file": (
                io.BytesIO(
                    _catalog_payload(
                        report_header="31/ago./2026",
                        employee="0000000100",
                        name="PERSONA ERROR",
                        rfc="PERR900101AA1",
                        account="1010101010",
                    )
                ),
                "empleados-error.txt",
            ),
        },
        content_type="multipart/form-data",
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
    )
    assert failed.status_code == 422
    assert failed.get_json()["message"] == (
        "No se pudo completar el análisis. El catálogo vigente no fue modificado."
    )
    conn = connect(app.config["DATABASE"])
    assert conn.execute(
        "SELECT id FROM nomina_banorte_catalog_versions WHERE status='ACTIVE'"
    ).fetchone()[0] == active_version_id
    assert conn.execute(
        "SELECT status FROM nomina_banorte_catalog_versions WHERE id=?",
        (failed.get_json()["version_id"],),
    ).fetchone()[0] == "STAGED"
    conn.close()


def test_c3a_check_3_comparison_keeps_unconfirmed_neutral_and_conflicts_blocking(tmp_path):
    app = _make_app(tmp_path, "admin")
    _activate_catalog(app.config["DATABASE"])
    staged = stage_catalog_version(
        app.config["DATABASE"],
        raw=_payload().replace(b"0000000001", b"0000000002").replace(
            b"1111111111", b"2222222222"
        ),
        filename="comparison.txt",
        actor="admin",
    )
    analyze_catalog_version(app.config["DATABASE"], staged["id"], actor="admin")
    mark_catalog_ready_for_review(app.config["DATABASE"], staged["id"], actor="admin")
    preview = catalog_apply_preview(app.config["DATABASE"], staged["id"])
    overview = load_catalog_admin_overview(
        app.config["DATABASE"], selected_version_id=staged["id"]
    )
    assert preview["lineage_unconfirmed_count"] == 1
    assert preview["operational_conflict_count"] == 0
    assert overview.selected["status_label"] == "Pendiente"
    rows = list_catalog_comparison_rows(app.config["DATABASE"], staged["id"])
    unconfirmed = [item for item in rows.items if item["lineage_status"] == "UNCONFIRMED"]
    assert len(unconfirmed) == 1
    assert unconfirmed[0]["operational_conflict"] is False
    assert unconfirmed[0]["lineage_label"] == "Relación histórica no confirmada"

    duplicate = _row()
    duplicate[1] = "OTRA PERSONA"
    duplicate[6] = "OTRA900101AA1"
    duplicate[14] = "3333333333"
    conflict_payload = "\n".join(
        [
            "FECHA: 31/ago./2026",
            "EMISORA: 67059 EMPRESA SINTETICA",
            "",
            "|".join(CATALOG_HEADER_V1) + "|",
            "|".join(_row()) + "|",
            "|".join(duplicate) + "|",
        ]
    ).encode("utf-8")
    conflict = stage_catalog_version(
        app.config["DATABASE"], raw=conflict_payload, filename="conflict.txt", actor="admin"
    )
    analyze_catalog_version(app.config["DATABASE"], conflict["id"], actor="admin")
    mark_catalog_ready_for_review(app.config["DATABASE"], conflict["id"], actor="admin")
    conflict_overview = load_catalog_admin_overview(
        app.config["DATABASE"], selected_version_id=conflict["id"]
    )
    assert conflict_overview.selected["status_label"] == "Requiere atención"
    conflicts = list_catalog_comparison_rows(
        app.config["DATABASE"], conflict["id"], filter_name="conflict"
    )
    assert conflicts.total >= 1
    assert all(item["operational_conflict"] for item in conflicts.items)
    assert all(item["target_person"] for item in conflicts.items)
    assert catalog_apply_preview(app.config["DATABASE"], conflict["id"])["can_apply"] is False


def test_catalog_projection_blocker_exposes_the_sanitized_target_and_action(tmp_path):
    app = _make_app(tmp_path, "admin")
    _activate_catalog(app.config["DATABASE"])
    blocked_row = _row()
    blocked_row[0] = "0000000443"
    blocked_row[1] = "PERSONA PENDIENTE"
    blocked_row[6] = "PEND900101AA1"
    blocked_row[14] = "9876545180"
    blocked_row[18] = "CAPTURADO"
    payload = "\n".join(
        [
            "FECHA: 02/sep./2026",
            "EMISORA: 67059 EMPRESA SINTETICA",
            "",
            "|".join(CATALOG_HEADER_V1) + "|",
            "|".join(blocked_row) + "|",
        ]
    ).encode("utf-8")
    staged = stage_catalog_version(
        app.config["DATABASE"], raw=payload, filename="empleados-real-sanitized.txt", actor="admin"
    )
    analyzed = analyze_catalog_version(app.config["DATABASE"], staged["id"], actor="admin")
    mark_catalog_ready_for_review(app.config["DATABASE"], staged["id"], actor="admin")

    assert analyzed["persons_by_status"] == {"NO_ELIGIBLE_ROW": 1}
    preview = catalog_apply_preview(app.config["DATABASE"], staged["id"])
    assert preview["operational_blockers"] == [{"code": "PROJECTION_BLOCKERS", "count": 1}]
    assert preview["can_apply"] is False

    conflicts = list_catalog_comparison_rows(
        app.config["DATABASE"], staged["id"], filter_name="conflict"
    )
    assert conflicts.total == 1
    item = conflicts.items[0]
    assert item["row_key"].startswith("conflict-projection-")
    assert item["target_person"]["name"] == "PERSONA PENDIENTE"
    assert item["target_person"]["employee"] == "0000000443"
    assert item["target_person"]["account_masked"] == "******5180"
    assert item["target_person"]["account_masked"] != "9876545180"
    assert "Capturado" in item["business_reason"]
    assert item["recommended_action"] == (
        "Corrige el estado de esta persona en Empleados.txt y vuelve a analizarlo."
    )
    assert item["current_person"] is None
    serialized = str(item)
    assert "PROJECTION_BLOCKERS" not in serialized
    assert "NO_ELIGIBLE_ROW" not in serialized
    assert "STATUS_NOT_APLICADO" not in serialized

    detail = get_catalog_comparison_row(
        app.config["DATABASE"], staged["id"], item["row_key"]
    )
    assert detail["target_person"] == item["target_person"]
    assert detail["recommended_action"] == item["recommended_action"]


def test_global_blocker_is_separate_and_never_becomes_an_empty_person_row(tmp_path):
    app = _make_app(tmp_path, "admin")
    _activate_catalog(app.config["DATABASE"])
    staged = stage_catalog_version(
        app.config["DATABASE"],
        raw=_catalog_payload(
            report_header="02/sep./2026",
            employee="0000000555",
            name="PERSONA GLOBAL",
            rfc="GLOB900101AA1",
            account="5555555555",
        ),
        filename="global-conflict.txt",
        actor="admin",
    )
    analyze_catalog_version(app.config["DATABASE"], staged["id"], actor="admin")
    conn = connect(app.config["DATABASE"])
    conn.execute(
        "UPDATE nomina_banorte_catalog_versions SET catalog_ready_count=0 WHERE id=?",
        (staged["id"],),
    )
    conn.commit()
    conn.close()
    mark_catalog_ready_for_review(app.config["DATABASE"], staged["id"], actor="admin")

    comparison = list_catalog_comparison_rows(
        app.config["DATABASE"], staged["id"], filter_name="conflict"
    )
    assert comparison.total == 0
    assert comparison.items == ()
    assert len(comparison.global_conflicts) == 1
    general = comparison.global_conflicts[0]
    assert general["title"] == "Conflicto general del archivo"
    assert "proyección" in general["reason"]
    assert "vuelve a analizarlo" in general["recommended_action"]
    assert "person_id" not in general
    assert "target_person" not in general

    page = app.test_client().get(
        f"/nomina/exportaciones/banorte/catalogo?version_id={staged['id']}"
    )
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Conflicto general del archivo" in html
    assert "PROJECTION_COUNT_MISMATCH" not in html


def test_c3a_check_4_rows_search_masking_detail_pagination_and_history(tmp_path):
    app = _make_app(tmp_path, "admin")
    _activate_catalog(app.config["DATABASE"])
    rows = []
    for index in range(1, 31):
        row = _row()
        row[0] = f"{index + 1000:010d}"
        row[1] = f"PERSONA PAGINADA {index:02d}"
        row[6] = f"PAGI900101A{index:02d}"
        row[14] = f"777777{index:04d}"
        rows.append(row)
    payload = "\n".join(
        [
            "FECHA: 30/ago./2026",
            "EMISORA: 67059 EMPRESA SINTETICA",
            "",
            "|".join(CATALOG_HEADER_V1) + "|",
            *("|".join(row) + "|" for row in rows),
        ]
    ).encode("utf-8")
    staged = stage_catalog_version(
        app.config["DATABASE"], raw=payload, filename="long.txt", actor="admin"
    )
    analyze_catalog_version(app.config["DATABASE"], staged["id"], actor="admin")
    mark_catalog_ready_for_review(app.config["DATABASE"], staged["id"], actor="admin")

    first = list_catalog_comparison_rows(
        app.config["DATABASE"], staged["id"], page=1, page_size=10
    )
    assert first.total >= 30
    assert len(first.items) == 10
    assert first.has_next is True
    searched = list_catalog_comparison_rows(
        app.config["DATABASE"],
        staged["id"],
        search="7777770017",
    )
    assert searched.total == 1
    item = searched.items[0]
    assert item["target_person"]["account_masked"].endswith("0017")
    assert item["target_person"]["account_masked"] != "7777770017"
    assert "_search_text_private" not in item
    detail = get_catalog_comparison_row(
        app.config["DATABASE"], staged["id"], item["row_key"]
    )
    assert detail["target_person"]["account_masked"] == item["target_person"]["account_masked"]

    client = app.test_client()
    conn = connect(app.config["DATABASE"])
    before_gets = tuple(
        int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in (
            "nomina_banorte_catalog_versions",
            "nomina_banorte_beneficiaries",
            "nomina_banorte_catalog_reconciliations",
            "nomina_banorte_catalog_events",
        )
    )
    conn.close()
    response = client.get(
        f"/nomina/exportaciones/banorte/catalogo/versions/{staged['id']}/comparison?page=1&page_size=10&filter=all",
        headers={"X-Catalog-Search": "7777770017"},
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.get_json()["total"] == 1
    assert "7777770017" not in response.get_data(as_text=True)
    history = client.get("/nomina/exportaciones/banorte/catalogo/history?page=1&page_size=20")
    assert history.status_code == 200
    assert history.get_json()["total"] >= 2
    assert {item["status_label"] for item in history.get_json()["items"]} >= {
        "Vigente",
        "Pendiente",
    }
    detail_response = client.get(
        f"/nomina/exportaciones/banorte/catalogo/versions/{staged['id']}/comparison/{item['row_key']}"
    )
    assert detail_response.status_code == 200
    assert "7777770017" not in detail_response.get_data(as_text=True)
    conn = connect(app.config["DATABASE"])
    after_gets = tuple(
        int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in (
            "nomina_banorte_catalog_versions",
            "nomina_banorte_beneficiaries",
            "nomina_banorte_catalog_reconciliations",
            "nomina_banorte_catalog_events",
        )
    )
    conn.close()
    assert after_gets == before_gets

    denied = _make_app(tmp_path, "supervisor", db_path=app.config["DATABASE"]).test_client()
    assert denied.get("/nomina/exportaciones/banorte/catalogo").status_code == 403
    assert denied.get(
        f"/nomina/exportaciones/banorte/catalogo/versions/{staged['id']}/comparison"
    ).status_code == 403
    assert denied.get("/nomina/exportaciones/banorte/catalogo/history").status_code == 403


def test_activation_check_counts_only_open_legacy_drafts_and_never_activates(tmp_path):
    app = _make_app(tmp_path, "admin")
    version = stage_catalog_version(
        app.config["DATABASE"], raw=_payload(), filename="synthetic.txt", actor="admin"
    )
    analyze_catalog_version(app.config["DATABASE"], version["id"], actor="admin")
    conn = sqlite3.connect(app.config["DATABASE"])
    draft_values = (
        "admin",
        "admin",
        "2026-08-21T00:00:00",
        "2026-08-21T00:00:00",
        "MANUAL_CAPTURE",
        None,
        None,
        "synthetic-origin",
        None,
        None,
    )
    conn.execute(
        """
        INSERT INTO nomina_banorte_export_drafts (
            created_by,updated_by,created_at,updated_at,origin_kind,calculo_id,
            origin_updated_at,origin_hash,status,consecutive_pref,layout_date_pref
        ) VALUES (?,?,?,?,?,?,?,?, 'OPEN',?,?)
        """,
        draft_values,
    )
    conn.execute(
        """
        INSERT INTO nomina_banorte_export_drafts (
            created_by,updated_by,created_at,updated_at,origin_kind,calculo_id,
            origin_updated_at,origin_hash,status,consecutive_pref,layout_date_pref
        ) VALUES (?,?,?,?,?,?,?,?, 'ABANDONED',?,?)
        """,
        draft_values,
    )
    conn.commit()
    conn.close()
    check = catalog_activation_check(app.config["DATABASE"], version["id"])
    assert check["legacy_open_draft_blockers"] == 1
    assert check["active_version_id"] is None
    assert "LEGACY_OPEN_DRAFTS" not in check["blocker_codes"]
    assert check["can_activate"] is False
