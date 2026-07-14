from __future__ import annotations

import sqlite3
from pathlib import Path

from modules.nomina.banorte.calculo_adapter import build_draft_rows_from_calculo, neto_final_to_decimal
from modules.nomina.banorte.calculo_queries import list_exportable_calculo_runs, neto_to_cents
from modules.nomina.banorte.repository import connect
from modules.nomina.db import ensure_nomina_tables


def _seed_asistencia(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        """
        INSERT INTO nomina_asistencia_imports (
            semana, fecha_inicio, fecha_fin, cliente, coordinador, filename,
            status, total_rows, created_at, updated_at
        ) VALUES ('2026-W28','2026-07-06','2026-07-12','CLIENTE A','COORD','f.xlsx',
                  'ready',1,'2026-07-13T00:00:00','2026-07-13T00:00:00')
        """
    )
    import_id = int(cur.lastrowid)
    conn.execute(
        """
        INSERT INTO nomina_asistencia_rows (
            import_id, row_number, nss, nombre_empleado, cliente, planta, banco, cuenta
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (import_id, 1, "12345678901", "ANA DEMO", "CLIENTE A", "P1", "BANORTE", "1234567890"),
    )
    return import_id


def seed_calculo(
    db_path: Path,
    *,
    netos: list[float],
    status: str = "revisado",
    bancos: list[str] | None = None,
    cuentas: list[str] | None = None,
    numeros: list[str] | None = None,
) -> int:
    conn = connect(db_path)
    ensure_nomina_tables(conn)
    import_id = _seed_asistencia(conn)
    # ensure enough asistencia rows
    for i in range(1, len(netos)):
        conn.execute(
            """
            INSERT INTO nomina_asistencia_rows (
                import_id, row_number, nss, nombre_empleado, cliente, planta, banco, cuenta
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                import_id,
                i + 1,
                f"1234567890{i}",
                f"PERSONA {i}",
                "CLIENTE A",
                "P1",
                (bancos[i] if bancos else "BANORTE"),
                (cuentas[i] if cuentas else f"123456789{i}"),
            ),
        )
    asis = conn.execute(
        "SELECT id FROM nomina_asistencia_rows WHERE import_id=? ORDER BY id",
        (import_id,),
    ).fetchall()
    cur = conn.execute(
        """
        INSERT INTO nomina_calculo_runs (
            asistencia_import_id, cliente, clientes_json, fecha_inicio, fecha_fin,
            config_json, status, total_empleados, warning_count, block_count,
            created_by, created_at, updated_at, raw_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            import_id,
            "CLIENTE A",
            "[]",
            "2026-07-06",
            "2026-07-12",
            "{}",
            status,
            len(netos),
            0,
            0,
            1,
            "2026-07-13T10:00:00",
            "2026-07-13T10:00:00",
            "{}",
        ),
    )
    calculo_id = int(cur.lastrowid)
    for i, neto in enumerate(netos):
        aid = int(asis[i]["id"]) if i < len(asis) else int(asis[0]["id"])
        banco = bancos[i] if bancos else "BANORTE"
        cuenta = cuentas[i] if cuentas else "1234567890"
        num = numeros[i] if numeros else f"EMP{i+1}"
        conn.execute(
            """
            INSERT INTO nomina_calculo_rows (
                calculo_id, asistencia_row_id, nss, numero_empleado, nombre_empleado,
                cliente, planta, banco, cuenta, neto_a_pagar_final, row_status,
                updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                calculo_id,
                aid,
                f"NSS{i}",
                num,
                f"TRABAJADOR {i+1}",
                "CLIENTE A",
                "P1",
                banco,
                cuenta,
                neto,
                "calculado",
                "2026-07-13T10:00:00",
            ),
        )
    conn.commit()
    conn.close()
    return calculo_id


def test_list_exportable_positive(tmp_path):
    db = tmp_path / "a.db"
    cid = seed_calculo(db, netos=[100.20, 0.0, 50.0])
    runs = list_exportable_calculo_runs(str(db))
    assert len(runs) == 1
    assert runs[0].id == cid
    assert runs[0].positive_neto_count == 2
    assert runs[0].total_exportable_cents == 15020


def test_list_skips_all_zero(tmp_path):
    db = tmp_path / "b.db"
    seed_calculo(db, netos=[0.0, 0.0])
    assert list_exportable_calculo_runs(str(db)) == []


def test_neto_conversion_deterministic():
    assert neto_to_cents(100.2) == 10020
    assert str(neto_final_to_decimal(100.2)) == "100.20"


def test_adapter_preserves_neto_and_excludes_zero(tmp_path):
    db = tmp_path / "c.db"
    cid = seed_calculo(db, netos=[2300.60, 0.0])
    adapted = build_draft_rows_from_calculo(str(db), cid)
    assert adapted.calculo_id == cid
    assert adapted.origin_hash
    assert len(adapted.rows) == 2
    assert adapted.rows[0].amount_final_cents == 230060
    assert adapted.rows[0].included == 1
    assert adapted.rows[1].included == 0
    # ensure adapter module does not import calc engine
    import modules.nomina.banorte.calculo_adapter as mod

    assert "calc_nomina" not in (mod.__dict__.get("__name__", "") or "")
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "calc_nomina" not in src
    assert "build_calculo_payload" not in src
