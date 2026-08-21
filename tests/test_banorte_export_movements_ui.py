from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_both_histories_share_eye_action_modal_and_controller():
    embedded = (ROOT / "templates/nomina/exportaciones_banorte.html").read_text(
        encoding="utf-8"
    )
    standalone = (
        ROOT / "templates/nomina/exportaciones_banorte_historial.html"
    ).read_text(encoding="utf-8")

    for source in (embedded, standalone):
        assert "data-banorte-export-movements" in source
        assert "Ver movimientos" in source
        assert "<svg" in source
        assert '_banorte_export_movements_modal.html' in source
        assert "banorte_export_history.js" in source


def test_shared_modal_accessibility_contract():
    modal = (
        ROOT / "templates/nomina/_banorte_export_movements_modal.html"
    ).read_text(encoding="utf-8")
    for contract in (
        'role="dialog"',
        'aria-modal="true"',
        'aria-labelledby="banorte-movements-title"',
        'id="banorte-movements-close"',
        'id="banorte-movements-state"',
        'id="banorte-movements-table-wrap"',
    ):
        assert contract in modal


def test_history_controller_uses_safe_dom_and_no_live_data_contracts():
    controller = (
        ROOT / "static/nomina/banorte_export_history.js"
    ).read_text(encoding="utf-8")
    service = (
        ROOT / "modules/nomina/banorte/history_service.py"
    ).read_text(encoding="utf-8")

    assert "textContent" in controller
    assert "createElement" in controller
    assert "Escape" in controller
    assert "lastTrigger.focus" in controller
    assert "ORDER BY position" in service
    assert "nomina_banorte_exports" in service
    assert "nomina_banorte_export_items" in service
    for forbidden in (
        "nomina_banorte_beneficiaries",
        "nomina_banorte_export_drafts",
        "nomina_calculo_runs",
        "catalog_",
    ):
        assert forbidden not in service
