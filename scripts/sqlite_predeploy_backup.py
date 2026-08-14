"""Crea y valida un backup SQLite consistente para el gate predeploy.

Uso operativo exclusivamente manual. No se importa desde la aplicación ni se ejecuta
en startup, HTTP o cron. El manifest nunca contiene filas ni valores de negocio.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import quote

DEFAULT_ESSENTIAL_TABLES = (
    "users",
    "nomina_headcount_snapshot",
    "nomina_headcount_snapshot_meta",
)


class BackupError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _readonly_connection(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(path.resolve().as_posix(), safe='/:')}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=30)


def _destination_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path, timeout=30)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _free_bytes(path: Path) -> int:
    return int(shutil.disk_usage(path).free)


def _repository_root(repository_root: str | Path | None = None) -> Path:
    """Devuelve la raíz física del checkout que contiene este script."""
    candidate = (
        Path(repository_root)
        if repository_root is not None
        else Path(__file__).resolve().parents[1]
    )
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise BackupError("No fue posible resolver la raíz del repositorio.") from exc
    if not resolved.is_dir():
        raise BackupError("La raíz del repositorio no existe o no es un directorio.")
    return resolved


def _require_external_path(path: Path, *, label: str, repository_root: Path) -> None:
    """Rechaza la raíz y cualquier descendiente, después de resolver symlinks/``..``."""
    try:
        resolved = path.resolve()
        resolved.relative_to(repository_root)
    except ValueError:
        return
    except OSError as exc:
        raise BackupError(f"No fue posible resolver {label}.") from exc
    raise BackupError(f"{label} debe estar fuera del repositorio.")


def _check_source(source: Path) -> None:
    if not source.is_file():
        raise BackupError("La DB origen no existe o no es un archivo regular.")
    if source.stat().st_size <= 0:
        raise BackupError("La DB origen está vacía.")
    conn = _readonly_connection(source)
    try:
        row = conn.execute("PRAGMA quick_check").fetchone()
        if not row or str(row[0]).lower() != "ok":
            raise BackupError("La DB origen no supera PRAGMA quick_check.")
    except sqlite3.DatabaseError as exc:
        raise BackupError("La DB origen no es una SQLite legible.") from exc
    finally:
        conn.close()


def validate_backup(
    backup_path: str | Path,
    *,
    essential_tables: Sequence[str] = DEFAULT_ESSENTIAL_TABLES,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> dict[str, object]:
    path = Path(backup_path).resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise BackupError("El backup no existe o tiene tamaño cero.")
    conn = _readonly_connection(path)
    try:
        integrity_rows = [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]
        if integrity_rows != ["ok"]:
            raise BackupError("PRAGMA integrity_check del backup no devolvió ok.")
        existing = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = sorted(set(essential_tables) - existing)
        if missing:
            raise BackupError("Faltan tablas esenciales: " + ", ".join(missing))
    except sqlite3.DatabaseError as exc:
        raise BackupError("El backup no puede abrirse como SQLite válida.") from exc
    finally:
        conn.close()
    size = path.stat().st_size
    sha256 = _sha256(path)
    if expected_size is not None and size != int(expected_size):
        raise BackupError("El tamaño no coincide con el manifest remoto.")
    if expected_sha256 is not None and sha256 != str(expected_sha256).lower():
        raise BackupError("El SHA-256 no coincide con el manifest remoto.")
    return {
        "size_bytes": size,
        "sha256": sha256,
        "integrity_check": "ok",
        "essential_tables": sorted(set(essential_tables)),
    }


def create_consistent_backup(
    source_path: str | Path,
    destination_path: str | Path,
    *,
    manifest_path: str | Path,
    project: str,
    environment: str,
    service: str,
    backup_id: str,
    deployment_id: str,
    essential_tables: Sequence[str] = DEFAULT_ESSENTIAL_TABLES,
    timestamp_utc: str | None = None,
    backup_operation: Callable[[sqlite3.Connection, sqlite3.Connection], None] | None = None,
    repository_root: str | Path | None = None,
) -> dict[str, object]:
    source = Path(source_path).resolve()
    destination = Path(destination_path).resolve()
    manifest = Path(manifest_path).resolve()
    repo_root = _repository_root(repository_root)
    _require_external_path(
        destination,
        label="El destino del backup",
        repository_root=repo_root,
    )
    _require_external_path(
        manifest,
        label="El manifest",
        repository_root=repo_root,
    )
    partial = destination.with_name(destination.name + ".partial")
    if destination.exists() or partial.exists():
        raise BackupError("El destino ya existe; no se sobrescribirá.")
    if manifest.exists():
        raise BackupError("El manifest ya existe; no se sobrescribirá.")
    if not destination.parent.is_dir() or not manifest.parent.is_dir():
        raise BackupError("El directorio destino/manifest no existe.")
    _check_source(source)
    estimated = max(source.stat().st_size * 2, 1024 * 1024)
    wal = Path(str(source) + "-wal")
    if wal.is_file():
        estimated += wal.stat().st_size
    if _free_bytes(destination.parent) < estimated:
        raise BackupError("Espacio libre insuficiente para crear y validar el backup.")

    source_conn: sqlite3.Connection | None = None
    destination_conn: sqlite3.Connection | None = None
    copy_error: Exception | None = None
    try:
        source_conn = _readonly_connection(source)
        destination_conn = _destination_connection(partial)
        destination_conn.execute("PRAGMA journal_mode=DELETE")
        destination_conn.execute("PRAGMA synchronous=FULL")
        if backup_operation is None:
            source_conn.backup(destination_conn, pages=256, sleep=0.05)
        else:
            backup_operation(source_conn, destination_conn)
        destination_conn.commit()
    except (OSError, sqlite3.Error, BackupError) as exc:
        copy_error = exc
    finally:
        if destination_conn is not None:
            destination_conn.close()
        if source_conn is not None:
            source_conn.close()
    if copy_error is not None:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        raise BackupError("SQLite Online Backup no pudo completar la copia.") from copy_error
    try:
        validation = validate_backup(partial, essential_tables=essential_tables)
        os.chmod(partial, 0o600)
        placeholder_fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(placeholder_fd)
        try:
            os.replace(partial, destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    except Exception:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    record: dict[str, object] = {
        "manifest_version": 1,
        "created_at_utc": timestamp_utc or _utc_now(),
        "project": project,
        "environment": environment,
        "service": service,
        "deployment_id": deployment_id,
        "backup_id": backup_id,
        "source_database_name": source.name,
        "backup_filename": destination.name,
        **validation,
    }
    try:
        with manifest.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
        os.chmod(manifest, 0o600)
    except OSError as exc:
        raise BackupError(
            "El backup es válido, pero no se pudo crear su manifest; no se eliminó la copia."
        ) from exc
    return record


def verify_against_manifest(
    backup_path: str | Path,
    manifest_path: str | Path,
) -> dict[str, object]:
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError("El manifest no puede leerse o no es JSON válido.") from exc
    if not isinstance(manifest, dict) or manifest.get("manifest_version") != 1:
        raise BackupError("Versión de manifest inválida.")
    essential = manifest.get("essential_tables")
    if not isinstance(essential, list) or not all(isinstance(x, str) for x in essential):
        raise BackupError("Lista de tablas esenciales inválida en manifest.")
    validation = validate_backup(
        backup_path,
        essential_tables=essential,
        expected_size=int(manifest.get("size_bytes") or 0),
        expected_sha256=str(manifest.get("sha256") or ""),
    )
    return {**manifest, **validation, "external_validation": "pass"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--source", required=True)
    create.add_argument("--destination", required=True)
    create.add_argument("--manifest", required=True)
    create.add_argument("--project", required=True)
    create.add_argument("--environment", required=True)
    create.add_argument("--service", required=True)
    create.add_argument("--deployment-id", required=True)
    create.add_argument("--backup-id", required=True)
    create.add_argument("--essential-table", action="append", dest="essential_tables")
    verify = commands.add_parser("verify")
    verify.add_argument("--backup", required=True)
    verify.add_argument("--manifest", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "create":
            result = create_consistent_backup(
                args.source,
                args.destination,
                manifest_path=args.manifest,
                project=args.project,
                environment=args.environment,
                service=args.service,
                deployment_id=args.deployment_id,
                backup_id=args.backup_id,
                essential_tables=args.essential_tables or DEFAULT_ESSENTIAL_TABLES,
            )
        else:
            result = verify_against_manifest(args.backup, args.manifest)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except BackupError as exc:
        print(f"BACKUP GATE FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
