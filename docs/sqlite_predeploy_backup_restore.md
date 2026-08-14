# Backup SQLite predeploy y recuperación

Esta herramienta protege la SQLite completa de ProClean-App antes del push que puede
activar una migración. No es un backup exclusivo de Headcount y no se ejecuta desde
HTTP, startup ni cron.

## Por qué se usa SQLite Online Backup

`sqlite3.Connection.backup()` obtiene una vista consistente aun cuando la base usa
WAL y permite que la aplicación continúe operando. Copiar `proclean.db` aisladamente
puede omitir transacciones presentes en `proclean.db-wal`; copiar manualmente DB,
WAL y SHM introduce una carrera entre archivos. El procedimiento no cambia
`journal_mode`, no fuerza checkpoints y abre el origen con `mode=ro`.

## Creación y extracción predeploy

Antes de comenzar se deben confirmar Railway CLI, autenticación, proyecto
`ProClean-App`, environment `production`, servicio `proclean-app`, deployment activo,
volumen y ruta real de la DB. La autorización manual de backup es obligatoria.

El CLI resuelve la raíz física del checkout a partir de la ubicación del script y
rechaza `--destination` o `--manifest` cuando resuelven a la raíz del repositorio o
a cualquiera de sus descendientes. Esto aplica a rutas absolutas, relativas,
segmentos `..` y padres enlazados mediante symlinks. Ambos archivos deben escribirse
en una ubicación externa confidencial cuyo directorio ya exista.

1. Fijar el deployment instance con `railway ssh` cuando la CLI lo permita.
2. Resolver la ruta de DB usada por el runtime; no asumirla por el mount solamente.
3. Ejecutar en esa instancia `scripts/sqlite_predeploy_backup.py create`, con un
   `backup_id` único, el `deployment_id` fijado y destino/manifest bajo `/tmp`. El
   origen se pasa con `--source`; ambos identificadores quedan en el manifest.
4. Confirmar que el JSON reporta `integrity_check: ok`, tamaño mayor que cero, SHA-256
   y las tablas esenciales de la versión productiva predeploy.
5. Antes de descargar, reconfirmar proyecto, environment, servicio y deployment.
   No ejecutar deploy entre creación y descarga.
6. Descargar exactamente el backup y manifest con `railway service files`. Si la
   instancia cambió, reinició o el temporal desapareció, fallar y repetir desde cero;
   nunca buscarlo en otra instancia.
7. Guardarlos en una ubicación confidencial controlada por el usuario, fuera de
   Railway y fuera de este repositorio.
8. Ejecutar localmente el subcomando `verify --backup ... --manifest ...`. Deben
   coincidir tamaño y SHA-256 y repetirse apertura, tablas e `integrity_check = ok`.
9. Sólo después del PASS externo, eliminar del deployment los dos temporales de
   `/tmp`. Si falla creación, se elimina únicamente `.partial`; si falla extracción o
   validación externa, conservar el temporal válido y detener el gate.

Nunca imprimir la URL OneDrive, secretos, filas ni contenido de tablas. El `.sqlite3`
contiene PII y no debe ser público ni entrar a Git.

## Recuperación

### Rollback lógico Headcount

Para una versión incorrecta del archivo, activar uno de los snapshots históricos. Es
la primera opción y no reemplaza la base completa.

### Rollback de aplicación

Revertir el código/deploy y conservar la proyección activa compatible cuando el fallo
sea de aplicación y la integridad SQLite permanezca correcta.

### Restore SQLite completo

Es último recurso y requiere autorización manual independiente. Detener aplicación y
escrituras; conservar la DB problemática para análisis; colocar una copia del backup
externo ya validado; abrirla y ejecutar `PRAGMA integrity_check`; iniciar el servicio;
y ejecutar smoke tests de autenticación, Headcount, Nómina, IMSS/SUA, Finiquitos,
facturación y demás módulos críticos. Este proyecto no automatiza el restore.
