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
from modules.nomina.banorte.catalog_parser import CATALOG_HEADER_V1
from modules.nomina.banorte.catalog_reconciliation import pre_reconcile_catalog_version
from modules.nomina.banorte.catalog_service import (
    analyze_catalog_version,
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


def test_catalog_admin_ui_and_workflow_have_no_activation_route(tmp_path):
    app = _make_app(tmp_path, "admin")
    client = app.test_client()
    page = client.get("/nomina/exportaciones/banorte/catalogo")
    assert page.status_code == 200
    assert page.headers["Cache-Control"] == "private, no-store"
    html = page.data.decode("utf-8")
    assert "Catálogo oficial Banorte" in html
    assert "Activación disponible después de Release 2B" in html
    token = _token(page.data)

    uploaded = client.post(
        "/nomina/exportaciones/banorte/catalogo/versions",
        data={"csrf_token": token, "file": (io.BytesIO(_payload()), "synthetic.txt")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert uploaded.status_code == 302

    conn = sqlite3.connect(app.config["DATABASE"])
    version_id = conn.execute("SELECT id FROM nomina_banorte_catalog_versions").fetchone()[0]
    conn.close()
    detail = client.get(f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}")
    assert detail.status_code == 200
    assert "beneficiary_material_state_json" not in detail.get_data(as_text=True)

    analyzed = client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/analyze",
        data={"csrf_token": token},
    )
    assert analyzed.status_code == 302
    pre = client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/pre-reconcile",
        data={"csrf_token": token},
    )
    assert pre.status_code == 302
    ready = client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/ready",
        data={"csrf_token": token},
    )
    assert ready.status_code == 302
    diff = client.get(f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/diff")
    assert diff.status_code == 200
    check = client.get(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/activation-check"
    )
    assert check.status_code == 200
    assert check.get_json()["active_version_id"] is None

    activation_rules = sorted(
        rule.rule
        for rule in app.url_map.iter_rules()
        if "banorte" in rule.rule and ("activate" in rule.rule or "rollback" in rule.rule)
    )
    assert "/nomina/exportaciones/banorte/catalogo/versions/<int:version_id>/activate" in activation_rules
    assert "/nomina/exportaciones/banorte/catalogo/versions/<int:version_id>/rollback" in activation_rules
    activate = client.post(
        f"/nomina/exportaciones/banorte/catalogo/versions/{version_id}/activate",
        data={"csrf_token": token},
    )
    assert activate.status_code in {200, 400}
    if activate.status_code == 200:
        assert activate.get_json()["active_version_id"] == version_id


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
