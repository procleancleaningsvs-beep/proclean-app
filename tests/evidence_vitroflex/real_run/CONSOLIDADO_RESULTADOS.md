# Resultados consolidados (ejecución real por el agente)

Fecha: 2026-03-27

## 1) Plantillas reales colocadas y usadas

| Archivo | Ruta en el proyecto |
|---------|---------------------|
| MEMO MENSUAL FORMATO.docx | `vitroflex_templates/MEMO MENSUAL FORMATO.docx` (copiado desde `C:\Users\Yahir\Downloads\MEMO MENSUAL FORMATO.docx`) |
| CR MENSUAL FORMATO.docx | `vitroflex_templates/CR MENSUAL FORMATO.docx` (copiado desde `C:\Users\Yahir\Downloads\CR MENSUAL FORMATO.docx`) |

## 2) Resultado por caso (generación DOCX → LibreOffice → PDF)

| Caso | Estado | PDF generado |
|------|--------|--------------|
| MEMO manual 3 personas | **OK** | `real_run/pdfs/01_memo_manual_3.pdf` |
| MEMO import tipo ejemplo 1 (2 col + defaults) | **OK** | `real_run/pdfs/02_memo_import_ejemplo1.pdf` |
| MEMO import tipo ejemplo 2 (4 col) | **OK** | `real_run/pdfs/03_memo_import_ejemplo2.pdf` |
| CR Vitroflex | **OK** | `real_run/pdfs/04_cr_vitroflex.pdf` |
| CR Mercado Moderno | **OK** | `real_run/pdfs/05_cr_mercado_moderno.pdf` |
| Caso una hoja | **OK** | `real_run/pdfs/06_caso_una_hoja.pdf` |
| Caso múltiples hojas (29 filas) | **OK** | `real_run/pdfs/07_caso_multiples_hojas.pdf` (2 páginas) |

Ningún PDF quedó en blanco (texto extraído ≥ 80 caracteres; sin `{{` sin reemplazar).

## 3) Validación de contenido (extracto real MEMO manual)

Texto extraído del PDF (PyMuPDF) incluye, entre otros:

- `Para:` / `Departamento de Vigilancia`, `De:` / `Relaciones Laborales`
- `ProClean`, domicilio, responsable y teléfono
- Permisos sustituidos: fechas 10 de marzo / 27 de abril de 2026
- Tres trabajadores con NSS y datos
- Cierre: `Sin otro asunto...`
- `A T E N T A M E N T E` y líneas Validación / Autorización

## 4) Evidencia visual generada en disco

| Tipo | Ubicación |
|------|-----------|
| PDFs | `tests/evidence_vitroflex/real_run/pdfs/*.pdf` |
| Renders página PDF | `tests/evidence_vitroflex/real_run/renders/*_p*.png` |
| Plantilla MEMO sin llenar (PDF + render) | `compare_template/A_MEMO_plantilla_sin_llenar.pdf`, `renders/A_plantilla_memo_ref_p1.png` |
| UI formulario MEMO | `real_run/ui/memo_form.png` |
| API smoke PDF | `real_run/api_smoke_generate.pdf` |

## 5) Backend: status y conversión

- `GET /vitroflex/api/status` sin sesión → 302 (esperado).
- Con sesión → 200, `templates.memo_docx: true`, `templates.cr_docx: true`, `libreoffice: true`.
- `POST /vitroflex/api/generate-pdf` → 200, PDF válido (`%PDF`), guardado como `api_smoke_generate.pdf`.
- Temporales: generados dentro de `tempfile.TemporaryDirectory` en `docx_bytes_to_pdf_bytes` y eliminados al terminar.

## 6) Local vs Railway

| Entorno | ¿Funciona generación PDF? | Notas |
|---------|---------------------------|--------|
| **Local (Windows)** | **Sí** | Evidencia: ejecución anterior; LibreOffice en `C:\Program Files\LibreOffice\program\soffice.exe`. |
| **Railway / Docker** | **Debería**, si el despliegue incluye plantillas y LibreOffice | El `Dockerfile` ya instala `libreoffice` vía `apt-get`. Falta asegurar que `vitroflex_templates/*.docx` estén en la imagen o en volumen persistente. En Linux, `soffice` suele estar en `PATH` (`/usr/bin/soffice`). Si no se detecta, definir `PROCLEAN_LIBREOFFICE=/usr/bin/soffice`. |

## 7) Corrección aplicada en esta sesión

Los placeholders en el MEMO estaban **partidos en XML**; el reemplazo solo por ZIP no era fiable. Se sustituye ahora con **python-docx** en párrafos y celdas (`docx_replace_body.py`), que usa el texto fusionado y coincide con lo que muestra Word.
