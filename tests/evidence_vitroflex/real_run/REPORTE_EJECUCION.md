# Evidencia real Vitroflex — 2026-03-30T12:48:34

## Entorno
- LibreOffice: `C:\Program Files\LibreOffice\program\soffice.exe`
- MEMO template: `C:\Users\Yahir\Downloads\proclean-app\vitroflex_templates\MEMO MENSUAL FORMATO.docx` exists=True
- CR template: `C:\Users\Yahir\Downloads\proclean-app\vitroflex_templates\CR MENSUAL FORMATO.docx` exists=True

## Import Excel
- Ejemplo 1 (2 cols): filas=10 needs_defaults=True err=None
- Ejemplo 2 (4 cols): filas=10 needs_defaults=False err=None

## Resultados por caso
### 01_memo_manual_3: **OK**
```json
{
  "pdf": "C:\\Users\\Yahir\\Downloads\\proclean-app\\tests\\evidence_vitroflex\\real_run\\pdfs\\01_memo_manual_3.pdf",
  "notes": [
    "páginas=1, chars≈728",
    "aviso: no se encontró 'ATENT' en texto"
  ],
  "pngs": [
    "C:\\Users\\Yahir\\Downloads\\proclean-app\\tests\\evidence_vitroflex\\real_run\\renders\\01_memo_manual_3_p1.png"
  ]
}
```

### 02_memo_import_ejemplo1: **OK**
```json
{
  "pdf": "C:\\Users\\Yahir\\Downloads\\proclean-app\\tests\\evidence_vitroflex\\real_run\\pdfs\\02_memo_import_ejemplo1.pdf",
  "notes": [
    "páginas=1, chars≈1189",
    "aviso: no se encontró 'ATENT' en texto"
  ],
  "pngs": [
    "C:\\Users\\Yahir\\Downloads\\proclean-app\\tests\\evidence_vitroflex\\real_run\\renders\\02_memo_import_ejemplo1_p1.png"
  ]
}
```

### 03_memo_import_ejemplo2: **OK**
```json
{
  "pdf": "C:\\Users\\Yahir\\Downloads\\proclean-app\\tests\\evidence_vitroflex\\real_run\\pdfs\\03_memo_import_ejemplo2.pdf",
  "notes": [
    "páginas=1, chars≈1148",
    "aviso: no se encontró 'ATENT' en texto"
  ],
  "pngs": [
    "C:\\Users\\Yahir\\Downloads\\proclean-app\\tests\\evidence_vitroflex\\real_run\\renders\\03_memo_import_ejemplo2_p1.png"
  ]
}
```

### 04_cr_vitroflex: **OK**
```json
{
  "pdf": "C:\\Users\\Yahir\\Downloads\\proclean-app\\tests\\evidence_vitroflex\\real_run\\pdfs\\04_cr_vitroflex.pdf",
  "notes": [
    "páginas=1, chars≈2271"
  ],
  "pngs": [
    "C:\\Users\\Yahir\\Downloads\\proclean-app\\tests\\evidence_vitroflex\\real_run\\renders\\04_cr_vitroflex_p1.png"
  ]
}
```

### 05_cr_mercado_moderno: **OK**
```json
{
  "pdf": "C:\\Users\\Yahir\\Downloads\\proclean-app\\tests\\evidence_vitroflex\\real_run\\pdfs\\05_cr_mercado_moderno.pdf",
  "notes": [
    "páginas=1, chars≈2285"
  ],
  "pngs": [
    "C:\\Users\\Yahir\\Downloads\\proclean-app\\tests\\evidence_vitroflex\\real_run\\renders\\05_cr_mercado_moderno_p1.png"
  ]
}
```

### 06_caso_una_hoja: **OK**
```json
{
  "pdf": "C:\\Users\\Yahir\\Downloads\\proclean-app\\tests\\evidence_vitroflex\\real_run\\pdfs\\06_caso_una_hoja.pdf",
  "notes": [
    "páginas=1, chars≈690",
    "aviso: no se encontró 'ATENT' en texto"
  ],
  "pngs": [
    "C:\\Users\\Yahir\\Downloads\\proclean-app\\tests\\evidence_vitroflex\\real_run\\renders\\06_caso_una_hoja_p1.png"
  ]
}
```

### 07_caso_multiples_hojas: **OK**
```json
{
  "pdf": "C:\\Users\\Yahir\\Downloads\\proclean-app\\tests\\evidence_vitroflex\\real_run\\pdfs\\07_caso_multiples_hojas.pdf",
  "notes": [
    "páginas=2, chars≈2169",
    "aviso: no se encontró 'ATENT' en texto"
  ],
  "pngs": [
    "C:\\Users\\Yahir\\Downloads\\proclean-app\\tests\\evidence_vitroflex\\real_run\\renders\\07_caso_multiples_hojas_p1.png",
    "C:\\Users\\Yahir\\Downloads\\proclean-app\\tests\\evidence_vitroflex\\real_run\\renders\\07_caso_multiples_hojas_p2.png"
  ]
}
```

## Comparación plantilla MEMO (sin llenar)
- PDF referencia: `C:\Users\Yahir\Downloads\proclean-app\tests\evidence_vitroflex\real_run\compare_template\A_MEMO_plantilla_sin_llenar.pdf`

## API backend (Flask test_client)
- GET /vitroflex/api/status → 302
- Con sesión: 200
```json
{
  "libreoffice": true,
  "ok": true,
  "templates": {
    "cr_docx": true,
    "cr_path": "C:\\Users\\Yahir\\Downloads\\proclean-app\\vitroflex_templates\\CR MENSUAL FORMATO.docx",
    "memo_docx": true,
    "memo_path": "C:\\Users\\Yahir\\Downloads\\proclean-app\\vitroflex_templates\\MEMO MENSUAL FORMATO.docx"
  }
}
```
- POST generate-pdf (PDF): 200, bytes=96120
- PDF guardado en `real_run/api_smoke_generate.pdf`
- POST generate-pdf (DOCX): 200, bytes=14796
- DOCX guardado en `real_run/api_smoke_generate.docx`

## Temporales LibreOffice
Los temporales usados por `docx_bytes_to_pdf_bytes` están en `tempfile.TemporaryDirectory` y se eliminan al salir del contexto (ver `libreoffice_pdf.py`).