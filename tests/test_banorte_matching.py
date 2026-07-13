from __future__ import annotations

from modules.nomina.banorte.matching_service import deactivate_alias, match_name, save_alias
from modules.nomina.banorte.paste_service import parse_paste_lists
from modules.nomina.banorte.repository import connect
from modules.nomina.db import ensure_nomina_tables


def _seed_active(conn, *, nombre, emp, account, validation="IMPORTADO_EXITOSO"):
    cur = conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, employee_number_effective, account_number,
            source_kind, validation_status, record_status, imported_at, imported_by, created_at, updated_at
        ) VALUES (?,?,?,?,'ALTA_MANUAL',?,'ACTIVO','t','u','t','t')
        """,
        (nombre, nombre.upper(), emp, account, validation),
    )
    return int(cur.lastrowid)


def test_paste_unequal_lengths_preserve_empty_and_headers():
    result = parse_paste_lists(
        "Nombre\nAna\n\nBeto\n",
        "Importe\n100\n200\n",
    )
    assert result.length_mismatch
    assert result.name_headers_detected
    assert result.amount_headers_detected
    # After headers: names Ana, '', Beto, ''  vs amounts 100, 200
    assert any(r.incomplete for r in result.rows)
    assert result.rows[0].raw_name == "Ana"


def test_matching_exact_alias_fuzzy_ambiguous(tmp_path):
    db = tmp_path / "m.db"
    conn = connect(db)
    ensure_nomina_tables(conn)
    a = _seed_active(conn, nombre="ANA DEMO UNO", emp="1", account="1001")
    _seed_active(conn, nombre="ANA DEMO UNO", emp="2", account="1002")  # ambiguous exact
    conn.commit()
    conn.close()

    # ambiguous exact
    amb = match_name(str(db), "ANA DEMO UNO")
    assert amb.kind == "AMBIGUOUS"
    assert amb.auto_selected is False

    db2 = tmp_path / "m2.db"
    conn = connect(db2)
    ensure_nomina_tables(conn)
    a = _seed_active(conn, nombre="BETO DEMO DOS", emp="3", account="1003")
    conn.commit()
    conn.close()
    exact = match_name(str(db2), "BETO DEMO DOS")
    assert exact.kind == "EXACT" and exact.auto_selected and exact.selected_id == a

    alias_id = save_alias(str(db2), "Beto", a, "tester")
    al = match_name(str(db2), "Beto")
    assert al.kind == "ALIAS" and al.auto_selected and al.alias_id == alias_id

    fuzzy = match_name(str(db2), "BETO DEMO DDOS")
    assert fuzzy.kind == "FUZZY_RECOMMENDATION"
    assert fuzzy.auto_selected is False


def test_alias_inactive_recommends_successor(tmp_path):
    db = tmp_path / "m3.db"
    conn = connect(db)
    ensure_nomina_tables(conn)
    old = _seed_active(conn, nombre="CARLA OLD", emp="10", account="2001")
    conn.execute(
        "UPDATE nomina_banorte_beneficiaries SET record_status='INACTIVO_REEMPLAZADO' WHERE id=?",
        (old,),
    )
    new = conn.execute(
        """
        INSERT INTO nomina_banorte_beneficiaries (
            nombre_original, nombre_normalizado, employee_number_effective, account_number,
            source_kind, validation_status, record_status, imported_at, imported_by,
            created_at, updated_at, replaces_id
        ) VALUES ('CARLA NEW','CARLA NEW','11','2001','ALTA_MANUAL','IMPORTADO_EXITOSO','ACTIVO','t','u','t','t',?)
        """,
        (old,),
    ).lastrowid
    conn.execute(
        """
        INSERT INTO nomina_banorte_aliases (
            alias_original, alias_normalizado, beneficiary_id, is_active, created_by, created_at
        ) VALUES ('Carla','CARLA',?,1,'tester','t')
        """,
        (old,),
    )
    conn.commit()
    conn.close()
    result = match_name(str(db), "Carla")
    assert result.kind == "ALIAS_INACTIVE_RESOLVED"
    assert result.auto_selected is False
    assert result.candidates[0].beneficiary_id == new
    assert result.alias_pointed_inactive_id == old
