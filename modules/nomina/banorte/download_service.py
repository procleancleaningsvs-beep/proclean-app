"""Lectura y empaquetado seguro de exportaciones Banorte históricas."""
from __future__ import annotations

import hashlib
import hmac
import io
import re
import zipfile
from dataclasses import dataclass

from modules.nomina.banorte.export_service import get_export_blob


class ExportDownloadError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class HistoricalPag:
    export_id: int
    filename: str
    blob: bytes
    size_bytes: int
    sha256: str


_SAFE_PAG_FILENAME = re.compile(r"^[^/\\\x00]+\.pag$", re.IGNORECASE)


def load_historical_pag(db_path: str, export_id: int) -> HistoricalPag:
    """Recupera el BLOB persistido y valida nombre/hash sin reconstruir el archivo."""
    try:
        filename, blob, stored_sha256 = get_export_blob(db_path, int(export_id))
    except KeyError as exc:
        raise ExportDownloadError("export_not_found") from exc

    if not _SAFE_PAG_FILENAME.fullmatch(filename) or filename != filename.strip():
        raise ExportDownloadError("export_filename_unsafe")
    digest = hashlib.sha256(blob).hexdigest()
    stored = stored_sha256.strip().lower()
    if (
        len(stored) != 64
        or any(ch not in "0123456789abcdef" for ch in stored)
        or not hmac.compare_digest(digest, stored)
    ):
        raise ExportDownloadError("export_integrity_mismatch")
    return HistoricalPag(
        export_id=int(export_id),
        filename=filename,
        blob=blob,
        size_bytes=len(blob),
        sha256=digest,
    )


def build_single_entry_zip(export: HistoricalPag) -> bytes:
    """Empaqueta exactamente una entrada con el nombre bancario histórico."""
    stream = io.BytesIO()
    info = zipfile.ZipInfo(export.filename, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    with zipfile.ZipFile(stream, mode="w") as archive:
        archive.writestr(info, export.blob)
    return stream.getvalue()
