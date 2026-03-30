"""Reemplazo de placeholders en XML interno del DOCX (respeta encabezados/pies y celdas)."""

from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile


def _xml_escape_text(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def replace_placeholders_in_docx_bytes(docx_bytes: bytes, mapping: dict[str, str]) -> bytes:
    """
    Sustituye claves literales (p. ej. {{FECHA}}) en todos los XML de word/.
    Los valores se escapan para XML. Si Word partió un placeholder entre varios <w:t>,
    este método no lo encontrará (limitación documentada).
    """
    buf = BytesIO()
    with ZipFile(BytesIO(docx_bytes), "r") as zin:
        with ZipFile(buf, "w") as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename.endswith(".xml") and info.filename.startswith("word/"):
                    s = data.decode("utf-8")
                    for k, v in mapping.items():
                        if k in s:
                            s = s.replace(k, _xml_escape_text(v))
                    data = s.encode("utf-8")
                zout.writestr(info, data)
    return buf.getvalue()
