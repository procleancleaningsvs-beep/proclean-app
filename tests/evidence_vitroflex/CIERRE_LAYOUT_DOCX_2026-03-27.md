# Cierre maquetación Vitroflex (DOCX → LibreOffice → PDF)

Fecha de regeneración: 2026-03-27 (script `scripts/run_vitroflex_real_evidence.py`).

## PDFs solicitados (después de los ajustes)

| Caso | Ruta |
|------|------|
| Multipágina MEMO | `tests/evidence_vitroflex/real_run/pdfs/07_caso_multiples_hojas.pdf` |
| Una hoja MEMO | `tests/evidence_vitroflex/real_run/pdfs/06_caso_una_hoja.pdf` |
| CR Mercado Moderno | `tests/evidence_vitroflex/real_run/pdfs/05_cr_mercado_moderno.pdf` |

Renders PNG (últimas páginas MEMO multipágina): `tests/evidence_vitroflex/real_run/renders/07_caso_multiples_hojas_p2.png`, etc.

## Antes vs después (resumen)

| Aspecto | Antes | Después |
|--------|--------|---------|
| Firma «Validación / Autorización» | El trazo y las etiquetas podían partirse entre páginas. | `w:keepNext` en el párrafo del trazo y `w:keepLines` en el de las etiquetas (python-docx: `keep_with_next` / `keep_together`). |
| Tabla trabajadores | `add_row()` + `cell.text` eliminaba `tcPr`/bordes; se perdía la fila de encabezados en MEMO al borrar desde el final. | Se detecta la fila de encabezados en cualquier índice; solo se borran filas bajo ella; cada fila de datos es `deepcopy` de la fila modelo del DOCX; el texto usa el primer run sin destruir `rPr`. Bordes con color `000000` donde ya existían trazos. |
| CR tipografía / NO. IMSS | Cuerpo con corridos > 9 pt; columna IMSS estrecha en DXA; `gridCol` sin `w:type` hacía que el ensanchado no se aplicara. | Todo `w:sz` / `w:szCs` > 18 medios puntos en el cuerpo pasa a 18 (9 pt); columna IMSS +560 DXA desde la primera columna (tratando `w:type` ausente como DXA); `w:noWrap` en celdas de datos de IMSS; menos espacio vertical en párrafos de celdas de esa tabla. |

## Qué archivos se tocaron

- **Solo pipeline Vitroflex DOCX** (plantillas `.docx` oficiales **sin** modificar):
  - `modules/vitroflex_docs/docx_table_workers.py` — ajustes 1 (indirecto al conservar estructura), 2.
  - `modules/vitroflex_docs/docx_layout_memo.py` — ajuste 1 (MEMO).
  - `modules/vitroflex_docs/docx_layout_cr.py` — ajuste 3 (CR).
  - `modules/vitroflex_docs/build_document.py` — enlaza los pasos anteriores tras `fill_worker_table`.

No se cambió la arquitectura DOCX + LibreOffice ni se reintrodujo HTML para PDF.

## Confirmación de alcance

No se modificaron rutas IMSS, CheckID, historial ni lógica de negocio fuera de `modules/vitroflex_docs/` y `build_document.py` (Vitroflex).
