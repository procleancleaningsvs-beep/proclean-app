"""
Comprueba alineación del pipeline finiquito (misma ruta app vs script).

Uso (raíz del repo):
  python scripts/verify_finiquito_pipeline_alignment.py

Imprime rutas absolutas, SHA256 del template en disco, y compara DOCX intermedio
(igual mapping) entre ruta bundle y ruta que usaría la app (DOCX_TEMPLATES_DIR).
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main() -> None:
    import app as proclean_app
    from modules.finiquitos.export_docx import (
        build_finiquito_placeholders,
        finiquito_docx_template_bundle_path,
        render_finiquito_docx,
    )
    from modules.finiquitos.finiquito_template_patch import patch_finiquito_docx_template_bytes

    bundle = finiquito_docx_template_bundle_path().resolve()
    app_tpl = (Path(proclean_app.DOCX_TEMPLATES_DIR) / "FINIQUITO FORMATO.docx").resolve()

    print("=== Rutas template ===")
    print("bundle (repo):     ", bundle)
    print("app DOCX_TEMPLATES:", app_tpl)
    print("¿mismo path?:      ", bundle == app_tpl)

    br = bundle.read_bytes() if bundle.is_file() else b""
    ar = app_tpl.read_bytes() if app_tpl.is_file() else b""
    print("\n=== SHA256 archivo en disco (sin leer Flask request) ===")
    print("bundle raw: ", _sha256(br) if br else "(missing)")
    print("app path raw:", _sha256(ar) if ar else "(missing)")

    print("\n=== SHA256 tras patch en memoria (lo que usa render) ===")
    print("patch(bundle):", _sha256(patch_finiquito_docx_template_bytes(br)) if br else "-")
    print("patch(app):   ", _sha256(patch_finiquito_docx_template_bytes(ar)) if ar else "-")

    # Mapping mínimo fijo para diff estable
    from datetime import date
    from decimal import Decimal

    from modules.finiquitos.calc import calcular_finiquito

    calc = calcular_finiquito(
        ingreso=date(2024, 10, 15),
        baja=date(2026, 3, 26),
        fecha_emision=date(2026, 3, 26),
        salario_diario=Decimal("315.04"),
        zona="general",
        periodicidad_isr="semanal_mensualizada",
        modo="correcto_fiscal",
        dias_sueldo_pendientes=Decimal("6"),
        septimos_pendientes=Decimal("1"),
        dias_aguinaldo_politica=Decimal("15"),
        prima_vacacional_pct=Decimal("25"),
        vacaciones_ya_usadas=Decimal("0"),
        aguinaldo_ya_pagado=Decimal("0"),
        prima_vac_ya_pagada=Decimal("0"),
        incluir_prima_antiguedad=False,
        motivo_baja="despido",
    )
    mapping = build_finiquito_placeholders(
        lugar_emision="X",
        estado_emision="Y",
        fecha_emision=date(2026, 3, 26),
        fecha_baja=date(2026, 3, 26),
        empleado_nombre="Nombre Prueba Pipeline",
        calc=calc,
        incluir_prima_antig=False,
    )

    if bundle.is_file():
        dxb = render_finiquito_docx(bundle, mapping)
    else:
        dxb = b""
    if app_tpl.is_file():
        dxa = render_finiquito_docx(app_tpl, mapping)
    else:
        dxa = b""

    print("\n=== DOCX intermedio (mismo mapping, render_finiquito_docx) ===")
    print("SHA256 desde bundle:", _sha256(dxb) if dxb else "-")
    print("SHA256 desde app tpl:", _sha256(dxa) if dxa else "-")
    print("¿DOCX idénticos?:   ", dxb == dxa and bool(dxb))

    if dxb and dxa and dxb != dxa:
        import zipfile
        from io import BytesIO

        def part(zb: bytes, name: str) -> str:
            z = zipfile.ZipFile(BytesIO(zb))
            return z.read(name).decode("utf-8")

        for name in ("word/header1.xml", "word/document.xml", "word/footer1.xml"):
            pb, pa = part(dxb, name), part(dxa, name)
            print(f"\n--- diff {name} igual: {pb == pa} ---")


if __name__ == "__main__":
    main()
