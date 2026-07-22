# Contrato de plantillas Excel — Gestión IDSE / SUA

Documento técnico breve. Las plantillas son base canónica para futuros exportadores dinámicos; **no** se sirven como reporte final vacío.

## Archivos

| Plantilla | Ruta | SHA-256 |
|---|---|---|
| Comparativo semanal | `modules/gestion_idse_sua/templates_excel/Plantilla_Comparativo_Semanal_IDSE_SUA.xlsx` | `0ae398efc7c2dcadc171e4285c937a42fc3d13d143e3206ae61fe67d9466f98c` |
| Reporte mensual | `modules/gestion_idse_sua/templates_excel/Plantilla_Reporte_Mensual_IDSE_SUA.xlsx` | `3f72bb0ca58e9eef52ec899dd46327adcfa2d75200d0751279b6422e48e9d4c0` |

Validación programática: `modules.gestion_idse_sua.template_validator`.

## Comparativo semanal — hojas

`Resumen` · `Detalle Comparativo` · `Asistencia Semanal` · `Movimientos Seleccionados` · `Guía`

### Detalle Comparativo (fila 6)

| Columna | Fuente futura | Obligatorio |
|---|---|---|
| ID registro | Generado | sí |
| Cliente / Planta | Relación planta→cliente + revisión | sí |
| Periodo inicio / fin / Semana | Periodo confirmado de importación | sí |
| Núm. empleado | Nómina | sí (si existe) |
| Nombre en nómina | Nómina | sí |
| Nombre en Headcount | Match Headcount | opcional hasta match |
| Método / Estado del match / Confianza % | Motor de match | sí |
| Puesto | Nómina | opcional |
| NSS / RFC / CURP / SBC | Enriquecimiento Headcount | obligatorios para exportar movimiento |
| Resultado comparativo / Semáforo | Comparativo manual | sí |
| Fecha sugerida | Primera/última A (confirmación humana) | sugerida |
| Tipo movimiento final | Decisión manual (ALTA/MOD/BAJA) | sí para bandeja |
| Enviar a bandeja | Decisión manual | sí |
| Motivo / Revisado por / Fecha revisión | Manual | opcional |

### Asistencia Semanal (fila 6)

Datos trabajador + `Día 1`…`Día 7` (fechas derivadas del periodo en `Resumen`) + totales A/F/I-INC/V/D + Primera/Última A.

### Movimientos Seleccionados

Campos operativos IDSE/SUA. `Compatible IDSE` para todos los tipos válidos. `Compatible SUA` **solo ALTA**. Columnas de decisión/exclusión manual: `Incluir`, `Estado de datos`, `Motivo de exclusión`.

## Reporte mensual — hojas

`Resumen` · `Personal Mensual` · `Asistencia Mensual` · `Trayectoria Semanal` · `Movimientos Seleccionados` · `Pendientes` · `Guía`

### Personal Mensual

Persona única del mes (solo si tiene ≥1 A en el mes calendario), match/enriquecimiento HC, totales, estado mensual, posibles/confirmados movimientos.

### Asistencia Mensual

Códigos diarios del mes (`Día 1`…`Día 31`) + totales. Fechas desde mes/año de `Resumen`.

### Trayectoria Semanal

Tramos del mes (bajas/reingresos múltiples posibles). Confirmación humana obligatoria.

### Pendientes

Bandeja de revisión (match, planta-cliente, movimientos incompletos, etc.).

## Reglas de asistencia (contrato)

| Código | Significado | Activo | Falta consecutiva |
|---|---|---|---|
| A | Asistencia | sí | rompe secuencia |
| F | Falta | no | cuenta |
| I / INC | Incapacidad | mantiene activo | rompe secuencia |
| V | Vacaciones | mantiene activo | rompe secuencia |
| D | Descanso | — | no cuenta; no interrumpe |

- Cuatro `F` consecutivas en días laborables pueden **sugerir** baja (`D` no cuenta ni interrumpe).
- Fecha sugerida de baja: día siguiente a la última `A`.
- Fecha sugerida de reingreso: primera nueva `A`.
- Confirmación humana obligatoria.
- Trayectoria se analiza en el **mes**, no semana aislada.
- Una persona puede tener varias bajas y reingresos en el mes.

## Headcount

1. **Enriquecimiento obligatorio (intento):** NSS, RFC, CURP, nombre separado, SBC, info patronal. Orden: núm. empleado → nombre exacto → alias → aproximado → manual (NSS/RFC/CURP/nombre). Homónimos nunca auto.
2. **Comparación opcional:** el reporte mensual **no** se compara automáticamente contra Headcount; puede ofrecerse como acción separada.

## Compatibilidad IDSE / SUA

- IDSE: ALTA (incl. reingreso), MODIFICACIÓN SALARIAL, BAJA — layout futuro canónico (no TSV).
- SUA: solo ALTA en esta etapa operativa.
- Exportar válidos aunque existan incompletos; listar excluidos con motivo.

## Qué no hace esta fase

No parser de nómina/asistencia, no generador de filas, no botón de exportación de plantilla vacía, no cambio de hojas/encabezados/fórmulas de los XLSX.
