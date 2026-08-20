from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_save_module_contract_and_no_user_agent_branching():
    source = (ROOT / "static/nomina/banorte_pag_save.js").read_text(encoding="utf-8")
    for contract in (
        "showDirectoryPicker",
        "indexedDB",
        "queryPermission",
        "requestPermission",
        "createWritable",
        "crypto.subtle.digest",
        ".canShare",
        ".share",
        "data-banorte-pag-save",
    ):
        assert contract in source
    assert "userAgent" not in source
    assert "localStorage" not in source
    assert "sessionStorage" not in source


def test_three_save_triggers_keep_raw_href_as_progressive_fallback():
    index = (ROOT / "templates/nomina/exportaciones_banorte.html").read_text(
        encoding="utf-8"
    )
    history = (
        ROOT / "templates/nomina/exportaciones_banorte_historial.html"
    ).read_text(encoding="utf-8")
    editor = (ROOT / "static/nomina/exportaciones_banorte_editor.js").read_text(
        encoding="utf-8"
    )

    assert "data-banorte-pag-save" in index
    assert "banorte_download" in index
    assert "data-banorte-pag-save" in history
    assert "banorte_download" in history
    assert "BanortePagSave.saveExport" in editor
    assert "banorte_download" in editor or "/download" in editor
