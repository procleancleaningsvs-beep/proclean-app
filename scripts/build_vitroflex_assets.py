"""Genera static/vitroflex_docs/assets/descargable_de_ejemplo.xlsx (plantilla descargable)."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "static" / "vitroflex_docs" / "assets" / "descargable_de_ejemplo.xlsx"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Trabajadores"
    ws.append(["NOMBRE TRABAJADOR", "NO. IMSS", "ACTIVIDAD A REALIZAR", "TEL. EMERGENCIA"])
    ws.append(["Ejemplo Uno", "12345678901", "Aux. de limpieza", "81 2183 9413"])
    wb.save(OUT)
    print("Escrito:", OUT)


if __name__ == "__main__":
    main()
