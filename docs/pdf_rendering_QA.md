# QA visual — renderizado DOCX → PDF (LibreOffice)

Este checklist aplica después de cambios en **Dockerfile**, **fuentes**, **fontconfig** o **`libreoffice_pdf.py`**.

## Alcance

Documentos generados vía LibreOffice headless:

| Módulo | Plantilla / salida |
|--------|-------------------|
| Finiquitos | `docx_templates/FINIQUITO FORMATO.docx` |
| MEMO mensual | `vitroflex_templates/MEMO MENSUAL FORMATO.docx` |
| CR mensual | `vitroflex_templates/CR MENSUAL FORMATO.docx` |
| Constancias IMSS | `docx_templates/` (vía `generator.py`) |
| Exámenes médicos | `examenes_medicos_templates/*.docx` |

## Validación técnica (obligatoria)

1. **Build Railway/Docker** sin errores (`Dockerfile` instala LO + fuentes + `fc-cache`).
2. **Diagnóstico** dentro del contenedor o local Linux:
   ```bash
   python scripts/check_pdf_engine.py
   python scripts/check_pdf_engine.py --convert
   ```
3. Confirmar:
   - `soffice --version` responde.
   - `fc-match Arial` → `Liberation Sans` (o alias equivalente).
   - `fc-match Times New Roman` → `Liberation Serif`.
   - `fc-match Calibri` → `Carlito` (si aplica).
   - Conversión de prueba genera PDF con tamaño > 0 bytes.

## Validación visual (obligatoria para Finiquito)

Generar PDF de prueba (mismo pipeline que producción):

```bash
python scripts/render_finiquito_prueba_ejemplo1.py
```

Comparar contra PDF de referencia **local** (no versionar PDFs con datos reales). Usar carpeta temporal:

```
tmp/pdf_regression/
```

### Checklist por documento

Para cada PDF, revisar **página 1** (y páginas adicionales si aplica):

- [ ] Fuente aparente igual o muy similar (sin sustitución obvia tipo DejaVu donde antes era Arial/Times).
- [ ] Saltos de línea iguales o aceptablemente similares al render anterior.
- [ ] Tablas compactas; conceptos largos no partidos indebidamente.
- [ ] **Finiquito:** `I.S.R. antes de Subs al empleo` en **una sola línea** (caso crítico).
- [ ] Totales e importes alineados con columnas.
- [ ] Firma / bloque ATENTAMENTE en la misma zona (sin salto de página extra).
- [ ] No hay páginas en blanco adicionales.
- [ ] Bordes y estilos de tabla intactos.
- [ ] Párrafos justificados sin espacios inter-palabra exagerados.

### Finiquitos — criterios específicos

- Tabla percepciones/deducciones compacta.
- Encabezado con logo sin recorte.
- Misma cantidad de páginas que el PDF de referencia correcto.
- Montos y totales legibles y sin desbordes.

### Vitroflex (MEMO / CR)

- Encabezados y tablas de montos alineados.
- Sin saltos de fila inesperados en conceptos largos.

### Constancias IMSS

- Bloques de movimientos legibles.
- Sin fuentes monospace o sustitutas evidentes.

## Regresión futura

Si se reinstala o “optimiza” LibreOffice en Docker:

1. Ejecutar `scripts/check_pdf_engine.py --convert`.
2. Regenerar finiquito de prueba.
3. Comparar visualmente contra referencia en `tmp/pdf_regression/`.
4. No reducir paquetes de fuentes (`fonts-crosextra-*`, `fonts-liberation2`) sin repetir QA.

## Instalación esperada en Docker

Paquetes mínimos documentados en `Dockerfile`:

- `libreoffice-core`, `libreoffice-writer`
- `fontconfig`
- `fonts-crosextra-carlito`, `fonts-crosextra-caladea`
- `fonts-liberation2`, `fonts-dejavu`, `fonts-noto-core`
- Alias en `docker/fontconfig/61-proclean-office-substitutions.conf`

Conversión central: `modules/vitroflex_docs/libreoffice_pdf.py` (perfil LibreOffice aislado por conversión, filtro `writer_pdf_Export`).
