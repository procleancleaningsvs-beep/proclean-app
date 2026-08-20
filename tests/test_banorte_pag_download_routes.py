from __future__ import annotations

import io
import sqlite3
import zipfile
from pathlib import Path

import pytest
from flask import Flask, g

from modules.nomina.banorte.export_service import DraftPaymentRow, generate_export
from modules.nomina.banorte.repository import connect
from modules.nomina.blueprint import register_nomina
from modules.nomina.db import ensure_nomina_tables


def _make_app(tmp_path: Path, role: str = "admin") -> tuple[Flask, str]:
    repo = Path(__file__).resolve().parents[1]
    app = Flask(
        __name__,
        template_folder=str(repo / "templates"),
        static_folder=str(repo / "static"),
    )
    db_path = str(tmp_path / "proclean.db")
    app.config.update(TESTING=True, SECRET_KEY="download-test", DATABASE=db_path)
    conn = sqlite3.connect(db_path)
    try:
        ensure_nomina_tables(conn)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users "
            "(id INTEGER PRIMARY KEY, username TEXT, role TEXT, password_hash TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO users (id, username, role, password_hash, created_at) "
            "VALUES (1,?,?,?,?)",
            ("tester", role, "x", "t"),
        )
        conn.commit()
    finally:
        conn.close()

    @app.route("/login")
    def login():
        return "login"

    register_nomina(app)

    @app.before_request
    def _auth():
        g.user = {"id": 1, "username": "tester", "role": role}

    return app, db_path


def _seed_export(db_path: str):
    conn = connect(db_path)
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original,nombre_normalizado,employee_number_effective,account_number,
            source_kind,validation_status,record_status,imported_at,imported_by,
            created_at,updated_at
        ) VALUES (
            'ANA','ANA','11','1321431243','ALTA_MANUAL','IMPORTADO_EXITOSO',
            'ACTIVO','t','u','t','t'
        )
        """
    )
    beneficiary_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return generate_export(
        db_path,
        "tester",
        [
            DraftPaymentRow(
                1,
                "Ana",
                beneficiary_id,
                "2700.00",
                "EXACT",
                client_account_number="1321431243",
                client_employee_number="11",
            )
        ],
        consecutive="07",
        layout_date="20260115",
        confirm_date_override=True,
    )


def test_metadata_and_raw_reuse_exact_historical_blob(tmp_path):
    app, db_path = _make_app(tmp_path)
    exported = _seed_export(db_path)
    client = app.test_client()

    metadata = client.get(
        f"/nomina/exportaciones/banorte/historial/{exported.export_id}/metadata"
    )
    assert metadata.status_code == 200
    assert metadata.headers["Cache-Control"] == "private, no-store"
    body = metadata.get_json()
    assert body == {
        "ok": True,
        "export_id": exported.export_id,
        "filename": exported.filename,
        "size_bytes": len(exported.file_bytes),
        "sha256": exported.file_sha256,
        "raw_url": f"/nomina/exportaciones/banorte/historial/{exported.export_id}/download",
        "zip_url": f"/nomina/exportaciones/banorte/historial/{exported.export_id}/zip",
    }

    raw = client.get(body["raw_url"])
    assert raw.status_code == 200
    assert raw.data == exported.file_bytes
    assert raw.headers["Cache-Control"] == "private, no-store"
    assert exported.filename in raw.headers["Content-Disposition"]


def test_zip_has_one_exact_filename_and_exact_historical_bytes(tmp_path):
    app, db_path = _make_app(tmp_path)
    exported = _seed_export(db_path)
    response = app.test_client().get(
        f"/nomina/exportaciones/banorte/historial/{exported.export_id}/zip"
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.mimetype == "application/zip"
    assert f"{exported.filename}.zip" in response.headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        assert archive.namelist() == [exported.filename]
        assert archive.read(exported.filename) == exported.file_bytes


@pytest.mark.parametrize("suffix", ["metadata", "zip"])
def test_missing_export_is_no_store_404(tmp_path, suffix):
    app, _db_path = _make_app(tmp_path)
    response = app.test_client().get(
        f"/nomina/exportaciones/banorte/historial/999999/{suffix}"
    )
    assert response.status_code == 404
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.get_json() == {"ok": False, "code": "export_not_found"}


@pytest.mark.parametrize("role", ["coordinador", "usuario", "cobranza"])
@pytest.mark.parametrize("suffix", ["metadata", "zip", "download"])
def test_download_endpoints_preserve_banorte_permissions(tmp_path, role, suffix):
    app, db_path = _make_app(tmp_path, role=role)
    exported = _seed_export(db_path)
    response = app.test_client().get(
        f"/nomina/exportaciones/banorte/historial/{exported.export_id}/{suffix}"
    )
    assert response.status_code == 403


@pytest.mark.parametrize("role", ["admin", "nomina"])
@pytest.mark.parametrize("suffix", ["metadata", "zip", "download"])
def test_download_endpoints_allow_banorte_roles(tmp_path, role, suffix):
    app, db_path = _make_app(tmp_path, role=role)
    exported = _seed_export(db_path)
    response = app.test_client().get(
        f"/nomina/exportaciones/banorte/historial/{exported.export_id}/{suffix}"
    )
    assert response.status_code == 200


@pytest.mark.parametrize("suffix", ["metadata", "zip"])
def test_stored_hash_mismatch_blocks_new_save_endpoints(tmp_path, suffix):
    app, db_path = _make_app(tmp_path)
    exported = _seed_export(db_path)
    conn = connect(db_path)
    conn.execute(
        "UPDATE nomina_banorte_exports SET file_sha256=? WHERE id=?",
        ("0" * 64, exported.export_id),
    )
    conn.commit()
    conn.close()

    response = app.test_client().get(
        f"/nomina/exportaciones/banorte/historial/{exported.export_id}/{suffix}"
    )
    assert response.status_code == 409
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.get_json() == {"ok": False, "code": "export_integrity_mismatch"}
