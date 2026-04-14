from modules.carrier.export_naming import (
    curso_export_pdf_display_name,
    first_worker_name_from_payload_json,
    worker_name_from_payload_json,
)


def test_first_worker_name_from_payload():
    payload = '[{"tipo":"alta","nss":"123","nombre":"RAMIREZ MATA YAHIR RAMON","fecha":"01/01/2026","salario":"330.57","causa_baja":"0"}]'
    assert first_worker_name_from_payload_json(payload) == "RAMIREZ MATA YAHIR RAMON"


def test_worker_name_by_index():
    payload = (
        '[{"nombre":"AAA A"},{"nombre":"BBB B"}]'
    )
    assert worker_name_from_payload_json(payload, 0) == "AAA A"
    assert worker_name_from_payload_json(payload, 1) == "BBB B"


def test_display_name_from_alta():
    n = curso_export_pdf_display_name(
        worker_name_from_alta="RAMIREZ MATA YAHIR RAMON",
        expediente_nombre="OTRO NOMBRE",
    )
    assert n == "RAMIREZ MATA YAHIR RAMON.pdf"


def test_display_name_fallback_expediente():
    assert curso_export_pdf_display_name(worker_name_from_alta=None, expediente_nombre="JUAN PEREZ") == "JUAN PEREZ.pdf"


def test_sanitize_invalid_chars():
    assert "RAMIREZ" in curso_export_pdf_display_name(
        worker_name_from_alta='RAMIREZ/TEST:YAHIR',
        expediente_nombre="X",
    )
