# Causa raíz de PDFs en blanco (implementación anterior)

## Qué ocurría

La exportación usaba **html2pdf.js** (html2canvas + jsPDF) sobre un nodo del DOM:

- El nodo objetivo a menudo estaba **fuera de pantalla** (`opacity:0`, `position:fixed`, `left:-12000px`) para no mostrar el “lienzo” al usuario.
- **html2canvas** puede devolver un lienzo vacío o incompleto con tablas largas, fuentes aún cargándose, o cuando el layout no ha terminado de pintarse.
- Con **muchas filas** (import ejemplo 1/2 o 29 trabajadores), el rasterizado fallaba de forma intermitente → **PDF con páginas en blanco o sin contenido visible**.

## Solución aplicada

1. **Fuente de verdad**: los archivos **MEMO MENSUAL FORMATO.docx** y **CR MENSUAL FORMATO.docx** en `vitroflex_templates/`.
2. **Sustitución**: placeholders `{{...}}` en el XML del paquete Office + tabla de trabajadores vía **python-docx**.
3. **PDF**: conversión **DOCX → PDF** con **LibreOffice** en modo headless (`soffice`), igual que el flujo documentado en `start_local.bat`.

La vista previa en el navegador muestra **el mismo PDF** que se descarga (blob `application/pdf` en un `<iframe>`), no una maquetación HTML paralela.

## Cómo no “inventamos” una plantilla nueva

No se diseña un documento legal en HTML/CSS. El contenido fijo (encabezados, textos legales, firmas, estilos) permanece **dentro del .docx**; la aplicación solo reemplaza marcadores y filas de tabla. Si falta el .docx oficial, la API responde error explícito.
