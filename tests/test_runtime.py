from __future__ import annotations

import os
import sqlite3
from dataclasses import replace
from io import BytesIO

import pytest
from pypdf import PdfWriter

from datajud_scraper.config import ScraperConfig
from datajud_scraper.errors import BusyError, PdfValidationError
from datajud_scraper.pdf_validation import validate_pdf
from datajud_scraper.runtime import Database, EventLogger, StorageLock, StoragePaths


def test_storage_layout_permissions_and_log_redaction_surface(tmp_path) -> None:
    config = replace(ScraperConfig(storage_root=tmp_path), min_free_bytes=0).normalized()
    paths = StoragePaths(config.storage_root)
    paths.initialize()
    logger = EventLogger(paths, config)
    logger.emit("test_event", run_id="safe-id", status=200)

    for directory in (paths.root, paths.pdfs, paths.logs, paths.state, paths.tmp):
        assert directory.is_dir()
        assert os.stat(directory).st_mode & 0o777 == 0o750
    log_file = next(paths.logs.glob("scraper-*.jsonl"))
    assert os.stat(log_file).st_mode & 0o777 == 0o640
    text = log_file.read_text(encoding="utf-8")
    assert "test_event" in text
    assert "safe-id" in text


def test_second_global_lock_is_rejected(tmp_path) -> None:
    path = tmp_path / "lock"
    with StorageLock(path, 0), pytest.raises(BusyError), StorageLock(path, 0):
        pass


def test_truncated_pdf_is_rejected(tmp_path, valid_pdf: bytes) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(valid_pdf[:-20])
    with pytest.raises(PdfValidationError):
        validate_pdf(path)


def test_encrypted_pdf_without_empty_password_is_rejected(tmp_path) -> None:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt("secret")
    writer.write(output)
    path = tmp_path / "encrypted.pdf"
    path.write_bytes(output.getvalue())

    with pytest.raises(PdfValidationError, match="cifrado"):
        validate_pdf(path)


def test_v1_database_is_migrated_without_losing_case(tmp_path) -> None:
    path = tmp_path / "scraper.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO schema_meta VALUES ('schema_version', '1');
        CREATE TABLE cases (
            case_id TEXT PRIMARY KEY,
            process_number TEXT NOT NULL UNIQUE,
            process_number_digits TEXT NOT NULL UNIQUE,
            source_url TEXT NOT NULL UNIQUE,
            codigo_hash TEXT NOT NULL UNIQUE,
            projudi_internal_id TEXT NOT NULL,
            distribution_at TEXT NOT NULL,
            subject TEXT NOT NULL,
            is_secret INTEGER NOT NULL CHECK (is_secret IN (0, 1)),
            first_seen_at TEXT NOT NULL,
            last_checked_at TEXT NOT NULL
        );
        CREATE TABLE documents (
            document_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(case_id),
            retrieved_at TEXT NOT NULL,
            relative_path TEXT NOT NULL UNIQUE,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            mime_type TEXT NOT NULL,
            page_count INTEGER NOT NULL,
            original_filename TEXT,
            validation_status TEXT NOT NULL DEFAULT 'valid',
            validation_error TEXT,
            UNIQUE (case_id, sha256)
        );
        INSERT INTO cases VALUES (
            'case-id', '0003714-33.2025.8.05.0274', '00037143320258050274',
            'https://projudi.tjba.jus.br/projudi/AcessoPublico?codigoHash=22f20646',
            '22f20646', '6020251285346', '2025-04-03T12:02:17Z',
            'Assunto anterior', 0, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
        );
        INSERT INTO documents VALUES (
            'document-id', 'case-id', '2026-01-01T00:00:00Z', 'pdfs/example.pdf',
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            100, 'application/pdf', 1, 'example.pdf', 'valid', NULL
        );
        """
    )
    connection.close()

    database = Database(path)
    try:
        assert database.connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == "2"
        assert database.connection.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 1
        assert database.connection.execute("SELECT COUNT(*) FROM case_sources").fetchone()[0] == 1
        assert database.connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
        assert database.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        columns = {
            row[1]: row for row in database.connection.execute("PRAGMA table_info(cases)")
        }
        assert columns["projudi_internal_id"][3] == 0
        assert columns["distribution_at"][3] == 0
        assert columns["subject"][3] == 0
        assert columns["is_secret"][3] == 0
    finally:
        database.close()
