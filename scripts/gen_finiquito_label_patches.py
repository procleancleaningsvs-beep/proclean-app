"""Generate replacement tuples for finiquito_template_patch (run manually if template changes)."""
from __future__ import annotations

import zipfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    x = zipfile.ZipFile(root / "docx_templates" / "FINIQUITO FORMATO.docx").read("word/document.xml").decode("utf-8")

    def between(marker_start: str, marker_end: str) -> str:
        a = x.find(marker_start)
        b = x.find(marker_end, a)
        if a < 0 or b < 0:
            raise SystemExit(f"missing {marker_start!r} or {marker_end!r}")
        return x[a:b]

    # Row2 concept paragraph: from <w:p w14:paraId="7BBE51AF" to end of concept cell (before amount cell for {t2})
    t2i = x.find("{t2}")
    if t2i < 0:
        raise SystemExit("no {t2}")
    end_close = "</w:p></w:tc>"
    end_i = x.rfind(end_close, 0, t2i) + len(end_close)
    s2a = x.find('<w:p w14:paraId="7BBE51AF"', 0, t2i)
    s2 = x[s2a:end_i]
    print("ROW2_P_LEN", len(s2))
    Path(root / "modules/finiquitos/_snippet_row2_p.xml").write_text(s2, encoding="utf-8")

    # Row3: find paraId near Vacaciones - locate Vacaciones w:t and go back to <w:p
    vi = x.find("<w:t>Vacaciones</w:t>")
    vp = x.rfind("<w:p ", 0, vi)
    t3i = x.find("{t3}")
    j = x.rfind(end_close, 0, t3i) + len(end_close)
    s3 = x[vp:j]
    Path(root / "modules/finiquitos/_snippet_row3_p.xml").write_text(s3, encoding="utf-8")
    print("ROW3_P_LEN", len(s3))

    # Row4: find 22
    i22 = x.find("<w:t>22</w:t>")
    vp = x.rfind("<w:p ", 0, i22)
    t5i = x.find("{t5}")
    j = x.rfind(end_close, 0, t5i) + len(end_close)
    s4 = x[vp:j]
    Path(root / "modules/finiquitos/_snippet_row4_p.xml").write_text(s4, encoding="utf-8")
    print("ROW4_P_LEN", len(s4))

    print("Wrote snippets to modules/finiquitos/_snippet_row*.xml")


if __name__ == "__main__":
    main()
