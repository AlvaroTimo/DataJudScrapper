from __future__ import annotations

import hashlib
import logging
import os
import shutil
from pathlib import Path

from pypdf import PdfReader

from .config import ScraperConfig
from .errors import PdfValidationError, StorageError
from .models import PdfValidation
from .runtime import StoragePaths


def ensure_disk_space(
    paths: StoragePaths,
    config: ScraperConfig,
    announced_size: int | None,
) -> None:
    try:
        free = shutil.disk_usage(paths.tmp).free
    except OSError as exc:
        raise StorageError("no se pudo consultar el espacio libre") from exc
    expected_with_margin = int((announced_size or 0) * 1.2)
    required = max(config.min_free_bytes, expected_with_margin)
    if free < required:
        raise StorageError(
            f"espacio insuficiente: libres={free} bytes, requeridos={required} bytes"
        )


def create_temp_file(path: Path):
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        os.chmod(path, 0o640)
        return os.fdopen(descriptor, "wb")
    except OSError as exc:
        raise StorageError("no se pudo crear el archivo temporal") from exc


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file_handle:
            for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise StorageError("no se pudo calcular la huella del PDF existente") from exc
    return digest.hexdigest()


def validate_pdf(
    path: Path,
    *,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> PdfValidation:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise PdfValidationError("el archivo PDF no existe o no se puede leer") from exc
    if size < 8:
        raise PdfValidationError("el archivo recibido es demasiado pequeno para ser PDF")
    if expected_size is not None and size != expected_size:
        raise PdfValidationError(
            f"PDF truncado: esperados={expected_size} bytes, recibidos={size} bytes"
        )

    try:
        with path.open("rb") as file_handle:
            if file_handle.read(5) != b"%PDF-":
                raise PdfValidationError("el archivo no comienza con la firma PDF")
            file_handle.seek(max(0, size - 65536))
            if b"%%EOF" not in file_handle.read():
                raise PdfValidationError("el PDF no contiene un marcador final")
    except OSError as exc:
        raise PdfValidationError("no se pudo inspeccionar el PDF") from exc

    actual_sha256 = hash_file(path)
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        raise PdfValidationError("la huella del PDF cambio durante la validacion")

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        with path.open("rb") as file_handle:
            reader = PdfReader(file_handle, strict=False)
            if reader.is_encrypted and reader.decrypt("") == 0:
                raise PdfValidationError("el PDF esta cifrado y no se puede validar")
            page_count = len(reader.pages)
    except PdfValidationError:
        raise
    except Exception as exc:
        raise PdfValidationError("la estructura interna del PDF es invalida") from exc
    if page_count < 1:
        raise PdfValidationError("el PDF no contiene paginas")
    return PdfValidation(sha256=actual_sha256, size_bytes=size, page_count=page_count)
