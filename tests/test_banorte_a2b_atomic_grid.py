from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from flask import Flask, g

from modules.nomina.banorte.batch_service import (
    BatchAccessError,
    BatchStaleError,
    ManualBatchValidationError,
    add_batch_row,
    confirm_batch,
    create_batch,
    find_open_manual_batch,
    get_batch,
    save_manual_beneficiaries,
)
from modules.nomina.banorte.repository import connect
from modules.nomina.banorte.schema import ensure_banorte_tables
from modules.nomina.blueprint import register_nomina
from modules.nomina.db import ensure_nomina_tables


def _db(tmp_path: Path) -> str:
    path = str(tmp_path / "a2b.db")
    conn = connect(path)
    ensure_nomina_tables(conn)
    ensure_banorte_tables(conn)
    conn.commit()
    conn.close()
    return path


def _app(tmp_path: Path, role: str, db_path: str) -> Flask:
    root = Path(__file__).resolve().parents[1]
    app = Flask(
        __name__,
        template_folder=str(root / "templates"),
        static_folder=str(root / "static"),
    )
    app.config.update(TESTING=True, SECRET_KEY="a2b-test", DATABASE=db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users "
        "(id INTEGER PRIMARY KEY, username TEXT, role TEXT, password_hash TEXT, created_at TEXT)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO users (id,username,role,password_hash,created_at) "
        "VALUES (1,?,?, 'x','t')",
        (f"{role}-user", role),
    )
    conn.commit()
    conn.close()

    @app.route("/login")
    def login():
        return "login"

    register_nomina(app)

    @app.before_request
    def _auth():
        g.user = {"id": 1, "username": f"{role}-user", "role": role}

    return app


def _csrf(client) -> str:
    html = client.get("/nomina/exportaciones/banorte").get_data(as_text=True)
    marker = 'data-csrf="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


def test_a2b_keyboard_paste_and_exactly_one_entry_are_local_and_atomic():
    root = Path(__file__).resolve().parents[1]
    module_path = root / "static" / "nomina" / "banorte_beneficiary_grid.js"
    script = r"""
const assert = require('assert');
const grid = require(process.argv[1]);
let seq = 0;
const model = grid.createLocalModel({ keyFactory: () => 'row-' + (++seq) });

assert.strictEqual(model.entryCount(), 1);
assert.deepStrictEqual(grid.resolveEntryKey('Tab', 'employee'), { action: 'focus', field: 'name' });
assert.deepStrictEqual(grid.resolveEntryKey('Tab', 'name'), { action: 'focus', field: 'account' });
assert.deepStrictEqual(grid.resolveEntryKey('Tab', 'account'), { action: 'focus_add' });
assert.deepStrictEqual(grid.resolveEntryKey('Enter', 'account'), { action: 'add' });

let one = model.applyPaste('0000000101\tPERSONA UNO\t1111111111', { field: 'employee' });
assert.strictEqual(one.ok, true);
assert.strictEqual(model.entry.employee_number, '0000000101');
assert.strictEqual(model.pendingRows.length, 0);

let added = model.addEntry();
assert.strictEqual(added.ok, true);
assert.strictEqual(model.pendingRows.length, 1);
assert.strictEqual(model.entryCount(), 1);
assert.strictEqual(model.entry.employee_number, '');

let multi = model.applyPaste(
  '0000000102\tPERSONA DOS\t2222222222\n0000000103\tPERSONA TRES\t3333333333',
  { field: 'employee' }
);
assert.strictEqual(multi.ok, true);
assert.strictEqual(model.pendingRows.length, 3);
assert.strictEqual(model.entryCount(), 1);

const before = JSON.stringify(model.snapshot());
let rejected = model.applyPaste('1\tDOS\t3\tCUATRO', { field: 'employee' });
assert.strictEqual(rejected.ok, false);
assert.strictEqual(rejected.code, 'paste_too_many_columns');
assert.strictEqual(JSON.stringify(model.snapshot()), before);

model.applyPaste('0000000104\n0000000105', { field: 'employee' });
assert.strictEqual(model.pendingRows.length, 5);
assert.strictEqual(model.entryCount(), 1);
assert.ok(model.locallyUsedEffectiveEmployees().has('0000000101'));
model.selectPending(model.pendingRows[0].client_row_key, 'employee');
model.applyAvailableNumber('0000000999');
assert.strictEqual(model.pendingRows[0].employee_number, '0000000999');
model.removePending(model.pendingRows[0].client_row_key);
assert.ok(!model.locallyUsedEffectiveEmployees().has('0000000999'));
assert.strictEqual(model.entryCount(), 1);

console.log(JSON.stringify({ok: true, pending: model.pendingRows.length}));
"""
    completed = subprocess.run(
        ["node", "-e", script, str(module_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["ok"] is True


def test_a2b_atomic_save_success_failure_competition_ownership_routes_and_reporte(tmp_path):
    db = _db(tmp_path)
    rows = [
        {
            "client_row_key": "local-1",
            "employee_number": "0000000101",
            "nombre": "PERSONA UNO",
            "account": "1111111111",
            "use_account_as_employee_number": False,
        },
        {
            "client_row_key": "local-2",
            "employee_number": "2222222222",
            "nombre": "PERSONA DOS",
            "account": "2222222222",
            "use_account_as_employee_number": True,
        },
    ]
    saved = save_manual_beneficiaries(db, "owner", rows)
    assert saved["status"] == "CONFIRMED"
    assert saved["created_by"] == "owner"
    assert len(saved["rows"]) == 2

    conn = connect(db)
    beneficiaries = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM nomina_banorte_beneficiaries ORDER BY id"
        )
    ]
    assert len(beneficiaries) == 2
    assert all(row["source_kind"] == "ALTA_MANUAL" for row in beneficiaries)
    assert all(
        row["validation_status"] == "MANUAL_PENDIENTE_VALIDACION"
        for row in beneficiaries
    )
    assert beneficiaries[1]["manual_effective_from_account"] == 1
    assert all(row["banorte_comment"] == f"batch:{saved['id']}" for row in beneficiaries)
    before_batches = conn.execute(
        "SELECT COUNT(*) FROM nomina_banorte_beneficiary_batches"
    ).fetchone()[0]
    before_rows = conn.execute(
        "SELECT COUNT(*) FROM nomina_banorte_beneficiary_batch_rows"
    ).fetchone()[0]
    conn.close()

    with pytest.raises(ManualBatchValidationError) as invalid:
        save_manual_beneficiaries(
            db,
            "invalid",
            [
                {
                    "client_row_key": "bad-employee",
                    "employee_number": "0000000101",
                    "nombre": "OCUPADO",
                    "account": "3333333333",
                },
                {
                    "client_row_key": "bad-account-a",
                    "employee_number": "0000000104",
                    "nombre": "DUP A",
                    "account": "4444444444",
                },
                {
                    "client_row_key": "bad-account-b",
                    "employee_number": "0000000105",
                    "nombre": "DUP B",
                    "account": "4444444444",
                },
            ],
        )
    assert {error["client_row_key"] for error in invalid.value.errors} == {
        "bad-employee",
        "bad-account-b",
    }
    assert all(
        {"row_index", "client_row_key", "field", "error_code", "message"}
        <= set(error)
        for error in invalid.value.errors
    )

    conn = connect(db)
    assert conn.execute(
        "SELECT COUNT(*) FROM nomina_banorte_beneficiaries"
    ).fetchone()[0] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM nomina_banorte_beneficiary_batches"
    ).fetchone()[0] == before_batches
    assert conn.execute(
        "SELECT COUNT(*) FROM nomina_banorte_beneficiary_batch_rows"
    ).fetchone()[0] == before_rows
    conn.close()

    transition = create_batch(db, "transition-owner", origin_kind="MANUAL")
    transition = add_batch_row(
        db,
        int(transition["id"]),
        "transition-owner",
        int(transition["revision"]),
        nombre="TRANSICION",
        cuenta="5555555555",
        employee_number="0000000106",
    )
    assert find_open_manual_batch(db, "transition-owner")["id"] == transition["id"]
    with pytest.raises(BatchAccessError):
        save_manual_beneficiaries(
            db,
            "intruder",
            [
                {
                    "client_row_key": "intruder",
                    "employee_number": "0000000107",
                    "nombre": "INTRUSO",
                    "account": "6666666666",
                }
            ],
            batch_id=int(transition["id"]),
            expected_revision=int(transition["revision"]),
        )
    with pytest.raises(BatchStaleError):
        save_manual_beneficiaries(
            db,
            "transition-owner",
            [
                {
                    "client_row_key": "stale",
                    "employee_number": "0000000108",
                    "nombre": "STALE",
                    "account": "7777777777",
                }
            ],
            batch_id=int(transition["id"]),
            expected_revision=int(transition["revision"]) - 1,
        )
    assert get_batch(db, int(transition["id"]))["rows"][0]["nombre"] == "TRANSICION"

    app = _app(tmp_path, "admin", db)
    client = app.test_client()
    token = _csrf(client)
    old_manual = client.post(
        "/nomina/exportaciones/banorte/beneficiarios/batches",
        json={"csrf_token": token, "origin_kind": "MANUAL"},
        headers={"X-CSRF-Token": token},
    )
    assert old_manual.status_code == 409
    assert old_manual.get_json()["code"] == "manual_batch_atomic_save_required"

    open_response = client.get(
        "/nomina/exportaciones/banorte/beneficiarios/batches/open"
    )
    assert open_response.status_code == 200
    assert open_response.get_json()["batch"] is None

    route_row = {
        "client_row_key": "route-admin",
        "employee_number": "0000000110",
        "nombre": "RUTA ADMIN",
        "account": "1100000000",
    }
    assert client.post(
        "/nomina/exportaciones/banorte/beneficiarios/manual-save",
        json={"rows": [route_row]},
    ).status_code == 403
    route_save = client.post(
        "/nomina/exportaciones/banorte/beneficiarios/manual-save",
        json={"csrf_token": token, "rows": [route_row]},
        headers={"X-CSRF-Token": token},
    )
    assert route_save.status_code == 200
    assert route_save.get_json()["batch"]["status"] == "CONFIRMED"

    nomina = _app(tmp_path, "nomina", db).test_client()
    nomina_token = _csrf(nomina)
    nomina_save = nomina.post(
        "/nomina/exportaciones/banorte/beneficiarios/manual-save",
        json={
            "csrf_token": nomina_token,
            "rows": [
                {
                    "client_row_key": "route-nomina",
                    "employee_number": "0000000111",
                    "nombre": "RUTA NOMINA",
                    "account": "1110000000",
                }
            ],
        },
        headers={"X-CSRF-Token": nomina_token},
    )
    assert nomina_save.status_code == 200
    assert nomina_save.get_json()["batch"]["created_by"] == "nomina-user"

    supervisor = _app(tmp_path, "supervisor", db).test_client()
    assert supervisor.post(
        "/nomina/exportaciones/banorte/beneficiarios/manual-save",
        json={"rows": rows},
    ).status_code == 403

    report = create_batch(db, "admin-user", origin_kind="REPORTE_DETALLADO")
    report = add_batch_row(
        db,
        int(report["id"]),
        "admin-user",
        int(report["revision"]),
        nombre="REPORTE OK",
        cuenta="8888888888",
        employee_number="0000000109",
    )
    token = _csrf(client)
    report_confirm = client.post(
        f"/nomina/exportaciones/banorte/beneficiarios/batches/{report['id']}/confirm",
        json={"csrf_token": token, "expected_revision": report["revision"]},
        headers={"X-CSRF-Token": token},
    )
    assert report_confirm.status_code == 200
    assert report_confirm.get_json()["batch"]["status"] == "CONFIRMED"
