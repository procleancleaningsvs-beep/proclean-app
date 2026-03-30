# Plantillas DOCX oficiales (fuente de verdad)

Coloca aquí **exactamente** estos archivos (tal como los entrega negocio / Word):

| Archivo | Uso |
|---------|-----|
| `MEMO MENSUAL FORMATO.docx` | MEMO mensual |
| `CR MENSUAL FORMATO.docx` | CR mensual |

Los nombres deben coincidir **literalmente** (incluidos espacios y mayúsculas).

## Placeholders que sustituye la app

**MEMO**

- `{{FECHA}}` — texto libre (p. ej. “Garcia, N. L. a …”)
- `{{PERMISO_1}}` — fecha en español (desde el selector de fecha)
- `{{PERMISO_2}}` — fecha en español

**CR**

- `{{FECHA}}` — texto libre
- `{{PLANTA}}` — se inserta en **MAYÚSCULAS** en todas las apariciones del XML

**Tabla de trabajadores**

La app detecta la tabla cuyo encabezado incluye nombre, IMSS / NSS, actividad y teléfono; borra filas de datos y vuelve a insertar **una fila por trabajador** (columnas en orden: nombre, IMSS, actividad, teléfono).

> Si Word partió un placeholder entre varios fragmentos (`<w:t>`), el reemplazo global puede fallar; en ese caso unifica el placeholder en una sola ejecución en Word.

## PDF

La conversión a PDF usa **LibreOffice** en modo headless (`soffice`), igual que en `start_local.bat` (variable `PROCLEAN_LIBREOFFICE` opcional).

## Causa de PDFs en blanco (anterior)

La exportación solo en navegador (`html2pdf` + nodo oculto / `html2canvas`) podía generar PDFs vacíos o inconsistentes. La generación actual **no usa HTML** para el PDF final: **DOCX oficial → LibreOffice → PDF**.
