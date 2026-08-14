from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from scripts import sqlite_predeploy_backup as backup


ESSENTIAL = ("users", "nomina_headcount_snapshot", "nomina_headcount_snapshot_meta")


def _source(path, *, wal=False):
    conn = sqlite3.connect(path, timeout=5)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT);
        CREATE TABLE nomina_headcount_snapshot (id INTEGER PRIMARY KEY, nombre TEXT);
        CREATE TABLE nomina_headcount_snapshot_meta (id INTEGER PRIMARY KEY, total_rows INTEGER);
        INSERT INTO users VALUES (1,'synthetic-admin');
        INSERT INTO nomina_headcount_snapshot VALUES (1,'PERSONA SINTETICA');
        INSERT INTO nomina_headcount_snapshot_meta VALUES (1,1);
        """
    )
    conn.commit()
    return conn


def _create(source, destination, manifest, **kwargs):
    return backup.create_consistent_backup(
        source,
        destination,
        manifest_path=manifest,
        project="ProClean-App",
        environment="production",
        service="proclean-app",
        deployment_id="deployment-synthetic",
        backup_id="backup-synthetic",
        essential_tables=ESSENTIAL,
        timestamp_utc="2026-08-11T00:00:00Z",
        **kwargs,
    )


def test_normal_db_backup_validates_and_manifest_has_no_rows(tmp_path):
    source = tmp_path / "source.db"
    _source(source).close()
    destination = tmp_path / "backup.sqlite3"
    manifest = tmp_path / "backup.json"
    record = _create(source, destination, manifest)
    assert record["integrity_check"] == "ok"
    assert record["size_bytes"] > 0
    assert len(record["sha256"]) == 64
    local = backup.verify_against_manifest(destination, manifest)
    assert local["external_validation"] == "pass"
    assert "PERSONA" not in manifest.read_text(encoding="utf-8")


def test_external_destination_and_manifest_are_allowed(tmp_path):
    repository = tmp_path / "checkout"
    external = tmp_path / "confidential-backups"
    repository.mkdir()
    external.mkdir()
    source = repository / "source.db"
    _source(source).close()

    record = _create(
        source,
        external / "backup.sqlite3",
        external / "backup.json",
        repository_root=repository,
    )

    assert record["integrity_check"] == "ok"
    assert (external / "backup.sqlite3").is_file()
    assert (external / "backup.json").is_file()


def test_destination_inside_repository_is_rejected(tmp_path):
    repository = tmp_path / "checkout"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    source = external / "source.db"
    _source(source).close()

    with pytest.raises(backup.BackupError, match="destino.*fuera del repositorio"):
        _create(
            source,
            repository / "backup.sqlite3",
            external / "backup.json",
            repository_root=repository,
        )


def test_default_repository_root_is_derived_from_script_location(tmp_path):
    repository = Path(backup.__file__).resolve().parents[1]
    source = tmp_path / "source.db"
    _source(source).close()

    with pytest.raises(backup.BackupError, match="destino.*fuera del repositorio"):
        _create(
            source,
            repository / "backup-must-not-be-created.sqlite3",
            tmp_path / "backup.json",
        )


def test_manifest_inside_repository_is_rejected(tmp_path):
    repository = tmp_path / "checkout"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    source = external / "source.db"
    _source(source).close()

    with pytest.raises(backup.BackupError, match="manifest.*fuera del repositorio"):
        _create(
            source,
            external / "backup.sqlite3",
            repository / "backup.json",
            repository_root=repository,
        )


def test_relative_destination_resolving_inside_repository_is_rejected(
    tmp_path, monkeypatch
):
    repository = tmp_path / "checkout"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    source = external / "source.db"
    _source(source).close()
    monkeypatch.chdir(repository)

    with pytest.raises(backup.BackupError, match="destino.*fuera del repositorio"):
        _create(
            source,
            "backup.sqlite3",
            external / "backup.json",
            repository_root=repository,
        )


def test_parent_segments_cannot_bypass_repository_guard(tmp_path):
    repository = tmp_path / "checkout"
    nested = repository / "nested"
    external = tmp_path / "external"
    nested.mkdir(parents=True)
    external.mkdir()
    source = external / "source.db"
    _source(source).close()

    with pytest.raises(backup.BackupError, match="destino.*fuera del repositorio"):
        _create(
            source,
            nested / ".." / "backup.sqlite3",
            external / "backup.json",
            repository_root=repository,
        )


def test_wal_backup_with_reader_and_controlled_writer_is_consistent(tmp_path):
    source = tmp_path / "source.db"
    keeper = _source(source, wal=True)
    reader = sqlite3.connect(source)
    assert reader.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    stop = threading.Event()

    def writer():
        conn = sqlite3.connect(source, timeout=5)
        index = 2
        while not stop.is_set() and index < 30:
            conn.execute("INSERT INTO users VALUES (?,?)", (index, f"synthetic-{index}"))
            conn.commit()
            index += 1
            time.sleep(0.002)
        conn.close()

    thread = threading.Thread(target=writer)
    thread.start()
    destination = tmp_path / "backup.sqlite3"
    manifest = tmp_path / "backup.json"
    _create(source, destination, manifest)
    stop.set()
    thread.join(timeout=5)
    reader.close()
    keeper.close()
    result = backup.validate_backup(destination, essential_tables=ESSENTIAL)
    assert result["integrity_check"] == "ok"
    conn = sqlite3.connect(destination)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= 1
    conn.close()


def test_existing_destination_or_manifest_is_never_overwritten(tmp_path):
    source = tmp_path / "source.db"
    _source(source).close()
    destination = tmp_path / "backup.sqlite3"
    manifest = tmp_path / "backup.json"
    destination.write_bytes(b"keep")
    with pytest.raises(backup.BackupError, match="no se sobrescribirá"):
        _create(source, destination, manifest)
    assert destination.read_bytes() == b"keep"
    destination.unlink()
    manifest.write_text("keep", encoding="utf-8")
    with pytest.raises(backup.BackupError, match="manifest ya existe"):
        _create(source, destination, manifest)
    assert manifest.read_text(encoding="utf-8") == "keep"


def test_missing_corrupt_and_empty_sources_fail(tmp_path):
    for source in (tmp_path / "missing.db", tmp_path / "empty.db", tmp_path / "corrupt.db"):
        if source.name == "empty.db":
            source.touch()
        elif source.name == "corrupt.db":
            source.write_bytes(b"not sqlite")
        with pytest.raises(backup.BackupError):
            _create(source, tmp_path / f"{source.stem}.backup", tmp_path / f"{source.stem}.json")


def test_missing_table_and_hash_or_size_mismatch_fail_validation(tmp_path):
    source = tmp_path / "source.db"
    _source(source).close()
    destination = tmp_path / "backup.sqlite3"
    manifest = tmp_path / "backup.json"
    _create(source, destination, manifest)
    with pytest.raises(backup.BackupError, match="Faltan tablas"):
        backup.validate_backup(destination, essential_tables=(*ESSENTIAL, "missing_table"))
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["sha256"] = "0" * 64
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(backup.BackupError, match="SHA-256"):
        backup.verify_against_manifest(destination, manifest)


def test_insufficient_space_fails_before_creating_destination(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    _source(source).close()
    monkeypatch.setattr(backup, "_free_bytes", lambda _path: 0)
    destination = tmp_path / "backup.sqlite3"
    with pytest.raises(backup.BackupError, match="Espacio libre insuficiente"):
        _create(source, destination, tmp_path / "backup.json")
    assert not destination.exists()


def test_interruption_or_permission_error_cleans_partial_only(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    _source(source).close()
    destination = tmp_path / "backup.sqlite3"
    manifest = tmp_path / "backup.json"

    def interrupted(_source_conn, destination_conn):
        destination_conn.execute("CREATE TABLE incomplete (id INTEGER)")
        destination_conn.commit()
        raise sqlite3.OperationalError("interrupted")

    with pytest.raises(backup.BackupError, match="no pudo completar"):
        _create(source, destination, manifest, backup_operation=interrupted)
    assert not destination.exists()
    assert not destination.with_name(destination.name + ".partial").exists()

    monkeypatch.setattr(
        backup,
        "_destination_connection",
        lambda _path: (_ for _ in ()).throw(sqlite3.OperationalError("permission denied")),
    )
    with pytest.raises(backup.BackupError, match="no pudo completar"):
        _create(source, destination, manifest)


def test_backup_does_not_change_source_rows_or_journal_mode(tmp_path):
    source = tmp_path / "source.db"
    conn = _source(source, wal=True)
    before = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    mode_before = conn.execute("PRAGMA journal_mode").fetchone()[0]
    _create(source, tmp_path / "backup.sqlite3", tmp_path / "backup.json")
    after = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    mode_after = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert (before, mode_before) == (after, mode_after)
