# ProClean App V6

## Abrir en tu computadora
La forma más simple es esta:

1. Descomprime el ZIP.
2. Entra a la carpeta del proyecto.
3. Da doble clic a `start_local.bat`.
4. Cuando la consola diga que está corriendo, abre `http://127.0.0.1:5000`.

## Manual, por si quieres verlo paso por paso
```bash
py -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python app.py
```

## Requisitos
- Python 3.11+
- LibreOffice instalado en Windows

## Qué trae esta versión
- Corrección del formulario que antes tomaba filas ocultas y disparaba el error "El nombre no puede venir vacío".
- Descarga automática del PDF al generarlo.
- Campo obligatorio de hora del lote.
- Opción de salario `Otro` con validación `0000.00`.
- Alta / Reingreso preseleccionado.
- Solo 2 opciones visibles para el usuario final: Alta / Reingreso y Baja.
- Plantilla de 1 movimiento actualizada con `formato_movimiento.docx`.
- Preparación para despliegue público con Docker.

## Publicarla en internet
Esta versión ya incluye `Dockerfile`, así que puedes subirla como servicio Docker.

Variables útiles para producción:
- `PROCLEAN_INSTANCE_DIR`
- `PROCLEAN_GENERATED_DIR`
- `PROCLEAN_TEMPLATES_DIR`
- `PORT`

Para no perder usuarios, historial, PDFs y plantillas, monta almacenamiento persistente y apunta esas rutas al disco o volumen.
