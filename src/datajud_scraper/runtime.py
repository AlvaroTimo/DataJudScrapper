from __future__ import annotations

import fcntl
import json
import os
import random
import sqlite3
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ScraperConfig
from .errors import BusyError, StorageError
from .models import CaseMetadata, isoformat_utc


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class StoredCaseDocument:
    case_id: str
    process_number: str
    process_number_digits: str
    distribution_at: datetime | None
    subject: str | None
    is_secret: bool | None
    document_id: str
    retrieved_at: datetime
    relative_path: str
    sha256: str
    size_bytes: int
    page_count: int


class StoragePaths:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=False)
        self.pdfs = self.root / "pdfs"
        self.logs = self.root / "logs"
        self.state = self.root / "state"
        self.tmp = self.root / "tmp"
        self.reports = self.root / "reports"
        self.database = self.state / "scraper.sqlite3"
        self.lock = self.state / "scraper.lock"

    def initialize(self) -> None:
        for directory in (self.root, self.pdfs, self.logs, self.state, self.tmp, self.reports):
            try:
                directory.mkdir(mode=0o750, parents=True, exist_ok=True)
                directory.chmod(0o750)
            except OSError as exc:
                raise StorageError(f"no se pudo preparar {directory}") from exc

    def assert_managed(self, path: Path) -> Path:
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise StorageError("una ruta calculada quedo fuera de storage_root") from exc
        return resolved

    def absolute(self, relative_path: str) -> Path:
        return self.assert_managed(self.root / relative_path)

    def temp_file(self, run_id: str) -> Path:
        if not uuid.UUID(run_id):
            raise StorageError("run_id invalido para el archivo temporal")
        return self.assert_managed(self.tmp / f"{run_id}.pdf.part")

    def final_file(
        self,
        metadata: CaseMetadata,
        retrieved_at: datetime,
        sha256: str,
    ) -> tuple[Path, str]:
        timestamp = retrieved_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        filename = f"{metadata.process_number}__{timestamp}__{sha256[:12]}.pdf"
        path = self.pdfs / str(metadata.process_year) / metadata.process_number_digits / filename
        absolute = self.assert_managed(path)
        relative = str(absolute.relative_to(self.root))
        return absolute, relative

    def publish(self, temp_path: Path, final_path: Path) -> None:
        temp_path = self.assert_managed(temp_path)
        final_path = self.assert_managed(final_path)
        try:
            self._ensure_directory_chain(final_path.parent)
            os.replace(temp_path, final_path)
            final_path.chmod(0o640)
            directory_fd = os.open(final_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise StorageError("no se pudo publicar atomicamente el PDF") from exc

    def _ensure_directory_chain(self, directory: Path) -> None:
        managed = self.assert_managed(directory)
        relative = managed.relative_to(self.root)
        current = self.root
        for part in relative.parts:
            current /= part
            current.mkdir(mode=0o750, exist_ok=True)
            current.chmod(0o750)

    def remove_managed_file(self, path: Path) -> None:
        managed = self.assert_managed(path)
        try:
            managed.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(
                f"no se pudo eliminar el archivo administrado {managed.name}"
            ) from exc

    def cleanup_stale_temp(self, max_age_hours: int) -> int:
        threshold = time.time() - (max_age_hours * 3600)
        removed = 0
        for path in self.tmp.glob("*.part"):
            try:
                if path.is_file() and path.stat().st_mtime < threshold:
                    path.unlink()
                    removed += 1
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise StorageError("no se pudieron limpiar temporales antiguos") from exc
        return removed


class StorageLock:
    def __init__(
        self,
        path: Path,
        timeout_seconds: float,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self.sleep = sleep
        self._fd: int | None = None

    def __enter__(self) -> StorageLock:
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o640)
            os.chmod(self.path, 0o640)
        except OSError as exc:
            raise StorageError("no se pudo abrir el bloqueo global") from exc
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fd = fd
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise BusyError(
                        "otro proceso del scraper esta usando este almacenamiento"
                    ) from None
                self.sleep(0.1)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


class EventLogger:
    def __init__(self, paths: StoragePaths, config: ScraperConfig) -> None:
        self.paths = paths
        self.config = config
        self.cleanup_old_logs()

    def _active_path(self, now: datetime) -> Path:
        base = self.paths.logs / f"scraper-{now:%Y-%m-%d}.jsonl"
        candidate = base
        sequence = 0
        while candidate.exists() and candidate.stat().st_size >= self.config.log_max_bytes:
            sequence += 1
            candidate = self.paths.logs / f"{base.name}.{sequence}"
        return self.paths.assert_managed(candidate)

    def emit(self, event: str, *, level: str = "info", **fields: object) -> None:
        now = utc_now()
        record = {
            "timestamp": isoformat_utc(now),
            "level": level,
            "event": event,
            **{key: value for key, value in fields.items() if value is not None},
        }
        line = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) + "\n"
        path = self._active_path(now)
        try:
            fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o640)
            try:
                os.chmod(path, 0o640)
                fcntl.flock(fd, fcntl.LOCK_EX)
                os.write(fd, line.encode("utf-8"))
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        except OSError as exc:
            raise StorageError("no se pudo escribir el log operativo") from exc

    def cleanup_old_logs(self) -> None:
        threshold = utc_now() - timedelta(days=self.config.log_retention_days)
        for path in self.paths.logs.glob("scraper-*.jsonl*"):
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                if path.is_file() and modified < threshold:
                    path.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise StorageError("no se pudo aplicar la retencion de logs") from exc


class Database:
    SCHEMA_VERSION = "3"
    BUSY_TIMEOUT_SECONDS = 30

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o640)
            os.close(descriptor)
            path.chmod(0o640)
            self.connection = sqlite3.connect(path, timeout=self.BUSY_TIMEOUT_SECONDS)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute(f"PRAGMA busy_timeout = {self.BUSY_TIMEOUT_SECONDS * 1000}")
            self._initialize_schema()
            self._secure_sqlite_files()
        except (OSError, sqlite3.Error) as exc:
            with suppress(Exception):
                self.connection.close()
            raise StorageError("no se pudo abrir o inicializar SQLite") from exc
        except Exception:
            with suppress(Exception):
                self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()
        self._secure_sqlite_files()

    def _secure_sqlite_files(self) -> None:
        for candidate in (
            self.path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        ):
            try:
                candidate.chmod(0o640)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise StorageError("no se pudieron asegurar los permisos de SQLite") from exc

    def _initialize_schema(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        current = self.connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        current_version = current["value"] if current else None
        cases_exists = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'cases'"
        ).fetchone()
        if current_version not in (None, self.SCHEMA_VERSION) or (
            current_version is None and cases_exists
        ):
            raise StorageError(
                "esquema antiguo: use un almacenamiento vacio; no se borra automaticamente"
            )

        self._create_schema_v3()
        self.connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
            (self.SCHEMA_VERSION,),
        )
        self.connection.commit()

    def _create_schema_v3(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS datasets (
                dataset_id TEXT PRIMARY KEY,
                input_path TEXT NOT NULL,
                metadata_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                metadata_sha256 TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                record_count INTEGER NOT NULL,
                imported_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dataset_records (
                record_id INTEGER PRIMARY KEY,
                dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id),
                line_number INTEGER NOT NULL,
                case_id TEXT NOT NULL REFERENCES cases(case_id),
                raw_json TEXT NOT NULL,
                UNIQUE(dataset_id, line_number),
                UNIQUE(dataset_id, case_id)
            );
            CREATE TABLE IF NOT EXISTS batches (
                batch_id TEXT PRIMARY KEY,
                dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id),
                status TEXT NOT NULL,
                sample TEXT NOT NULL,
                seed INTEGER NOT NULL,
                config_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                next_retry_at REAL,
                stop_reason TEXT,
                infrastructure_failures INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS batch_items (
                batch_id TEXT NOT NULL REFERENCES batches(batch_id),
                position INTEGER NOT NULL,
                record_id INTEGER NOT NULL REFERENCES dataset_records(record_id),
                status TEXT NOT NULL DEFAULT 'pending',
                last_run_id TEXT,
                result_json TEXT,
                PRIMARY KEY(batch_id, position),
                UNIQUE(batch_id, record_id)
            );
            CREATE INDEX IF NOT EXISTS batch_items_status ON batch_items(batch_id, status);
            CREATE TABLE IF NOT EXISTS cases (
                case_id TEXT PRIMARY KEY,
                process_number TEXT NOT NULL UNIQUE,
                process_number_digits TEXT NOT NULL UNIQUE,
                source_url TEXT,
                codigo_hash TEXT,
                projudi_internal_id TEXT,
                distribution_at TEXT,
                subject TEXT,
                is_secret INTEGER CHECK (is_secret IN (0, 1) OR is_secret IS NULL),
                first_seen_at TEXT NOT NULL,
                last_checked_at TEXT
            );

            CREATE TABLE IF NOT EXISTS case_sources (
                source_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
                source_url TEXT NOT NULL UNIQUE,
                codigo_hash TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                UNIQUE (case_id, source_url)
            );

            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL REFERENCES cases(case_id),
                retrieved_at TEXT NOT NULL,
                relative_path TEXT NOT NULL UNIQUE,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
                mime_type TEXT NOT NULL,
                page_count INTEGER NOT NULL CHECK (page_count > 0),
                original_filename TEXT,
                validation_status TEXT NOT NULL DEFAULT 'valid'
                    CHECK (validation_status IN ('valid', 'invalid')),
                validation_error TEXT,
                UNIQUE (case_id, sha256)
            );

            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                input_url_sha256 TEXT NOT NULL,
                case_id TEXT REFERENCES cases(case_id),
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                batch_id TEXT REFERENCES batches(batch_id),
                position INTEGER,
                published_path TEXT,
                bootstrap_attempts INTEGER NOT NULL DEFAULT 0,
                resolve_attempts INTEGER NOT NULL DEFAULT 0,
                session_recoveries INTEGER NOT NULL DEFAULT 0,
                page_attempts INTEGER NOT NULL DEFAULT 0,
                pdf_attempts INTEGER NOT NULL DEFAULT 0,
                bytes_received INTEGER,
                error_code TEXT,
                error_message TEXT
            );

            CREATE TABLE IF NOT EXISTS request_state (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                last_request_started_at REAL
            );
            INSERT OR IGNORE INTO request_state(singleton, last_request_started_at)
            VALUES (1, NULL);

            CREATE TABLE IF NOT EXISTS access_state (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                blocked_until REAL,
                reason TEXT
            );
            INSERT OR IGNORE INTO access_state(singleton, blocked_until, reason)
            VALUES (1, NULL, NULL);
            CREATE INDEX IF NOT EXISTS runs_batch_position ON runs(batch_id, position);
            """
        )

    def start_run(
        self,
        run_id: str,
        input_url_sha256: str,
        started_at: datetime,
        *,
        batch_id: str | None = None,
        position: int | None = None,
        case_id: str | None = None,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO runs(
                    run_id,input_url_sha256,started_at,status,batch_id,position,case_id)
                VALUES (?,?,?,'running',?,?,?)""",
                (run_id, input_url_sha256, isoformat_utc(started_at), batch_id, position, case_id),
            )
            if batch_id:
                self.connection.execute(
                    "UPDATE batch_items SET status='running',last_run_id=? "
                    "WHERE batch_id=? AND position=?",
                    (run_id, batch_id, position),
                )

    def increment_attempt(self, run_id: str, kind: str) -> None:
        if kind not in ("bootstrap", "page", "resolve", "pdf", "session"):
            raise ValueError("fase de peticion desconocida")
        column = "session_recoveries" if kind == "session" else f"{kind}_attempts"
        self.connection.execute(
            f"UPDATE runs SET {column} = {column} + 1 WHERE run_id = ?",
            (run_id,),
        )
        self.connection.commit()

    def finish_run(
        self,
        run_id: str,
        status: str,
        *,
        finished_at: datetime,
        bytes_received: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        safe_message = error_message[:500] if error_message else None
        self.connection.execute(
            """
            UPDATE runs
            SET finished_at = ?, status = ?, bytes_received = ?, error_code = ?, error_message = ?
            WHERE run_id = ?
            """,
            (
                isoformat_utc(finished_at),
                status,
                bytes_received,
                error_code,
                safe_message,
                run_id,
            ),
        )
        self.connection.commit()

    def upsert_case(self, metadata: CaseMetadata, checked_at: datetime) -> str:
        existing = self.connection.execute(
            "SELECT case_id FROM cases WHERE process_number_digits = ?",
            (metadata.process_number_digits,),
        ).fetchone()
        case_id = existing["case_id"] if existing else str(uuid.uuid4())
        source_owner = self.connection.execute(
            "SELECT case_id FROM case_sources WHERE source_url = ?",
            (metadata.source_url,),
        ).fetchone()
        if source_owner is not None and source_owner["case_id"] != case_id:
            raise StorageError("la URL de origen ya estaba vinculada a otro numero CNJ")

        secrecy_value = None if metadata.is_secret is None else int(metadata.is_secret)
        checked_at_text = isoformat_utc(checked_at)
        with self.connection:
            if existing:
                self.connection.execute(
                    """
                    UPDATE cases
                    SET process_number = ?, source_url = ?, codigo_hash = ?,
                        projudi_internal_id = COALESCE(?, projudi_internal_id),
                        distribution_at = ?,
                        subject = ?,
                        is_secret = ?, last_checked_at = ?
                    WHERE case_id = ?
                    """,
                    (
                        metadata.process_number,
                        metadata.source_url,
                        metadata.codigo_hash,
                        metadata.projudi_internal_id,
                        isoformat_utc(metadata.distribution_at),
                        metadata.subject,
                        secrecy_value,
                        checked_at_text,
                        case_id,
                    ),
                )
            else:
                self.connection.execute(
                    """
                    INSERT INTO cases(
                        case_id, process_number, process_number_digits, source_url, codigo_hash,
                        projudi_internal_id, distribution_at, subject, is_secret,
                        first_seen_at, last_checked_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        case_id,
                        metadata.process_number,
                        metadata.process_number_digits,
                        metadata.source_url,
                        metadata.codigo_hash,
                        metadata.projudi_internal_id,
                        isoformat_utc(metadata.distribution_at),
                        metadata.subject,
                        secrecy_value,
                        checked_at_text,
                        checked_at_text,
                    ),
                )
            self.connection.execute(
                """
                INSERT INTO case_sources(
                    source_id, case_id, source_url, codigo_hash, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_url) DO UPDATE SET
                    codigo_hash = excluded.codigo_hash,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    str(uuid.uuid4()),
                    case_id,
                    metadata.source_url,
                    metadata.codigo_hash,
                    checked_at_text,
                    checked_at_text,
                ),
            )
        return case_id

    def find_latest_valid_by_case_id(self, case_id: str) -> StoredCaseDocument | None:
        row = self.connection.execute(
            """
            SELECT c.case_id, c.process_number, c.process_number_digits, c.distribution_at,
                   c.subject, c.is_secret, d.document_id, d.retrieved_at, d.relative_path,
                   d.sha256, d.size_bytes, d.page_count
            FROM cases c
            JOIN documents d ON d.case_id = c.case_id AND d.validation_status = 'valid'
            WHERE c.case_id = ?
            ORDER BY d.retrieved_at DESC
            LIMIT 1
            """,
            (case_id,),
        ).fetchone()
        return self._stored_document(row) if row else None

    def find_valid_by_hash(self, case_id: str, sha256: str) -> StoredCaseDocument | None:
        row = self.connection.execute(
            """
            SELECT c.case_id, c.process_number, c.process_number_digits, c.distribution_at,
                   c.subject, c.is_secret, d.document_id, d.retrieved_at, d.relative_path,
                   d.sha256, d.size_bytes, d.page_count
            FROM cases c
            JOIN documents d ON d.case_id = c.case_id AND d.validation_status = 'valid'
            WHERE c.case_id = ? AND d.sha256 = ?
            LIMIT 1
            """,
            (case_id, sha256),
        ).fetchone()
        return self._stored_document(row) if row else None

    def insert_document(
        self,
        *,
        case_id: str,
        retrieved_at: datetime,
        relative_path: str,
        sha256: str,
        size_bytes: int,
        mime_type: str,
        page_count: int,
        original_filename: str | None,
    ) -> str:
        document_id = str(uuid.uuid4())
        cursor = self.connection.execute(
            """
            INSERT INTO documents(
                document_id, case_id, retrieved_at, relative_path, sha256,
                size_bytes, mime_type, page_count, original_filename
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(case_id, sha256) DO UPDATE SET
                retrieved_at = excluded.retrieved_at,
                relative_path = excluded.relative_path,
                size_bytes = excluded.size_bytes,
                mime_type = excluded.mime_type,
                page_count = excluded.page_count,
                original_filename = excluded.original_filename,
                validation_status = 'valid',
                validation_error = NULL
            WHERE documents.validation_status = 'invalid'
            """,
            (
                document_id,
                case_id,
                isoformat_utc(retrieved_at),
                relative_path,
                sha256,
                size_bytes,
                mime_type,
                page_count,
                original_filename,
            ),
        )
        if cursor.rowcount != 1:
            raise sqlite3.IntegrityError("el documento ya tiene una copia valida")
        self.connection.commit()
        row = self.connection.execute(
            "SELECT document_id FROM documents WHERE case_id = ? AND sha256 = ?",
            (case_id, sha256),
        ).fetchone()
        return row["document_id"]

    def mark_document_invalid(self, document_id: str, error_code: str) -> None:
        self.connection.execute(
            """
            UPDATE documents
            SET validation_status = 'invalid', validation_error = ?
            WHERE document_id = ?
            """,
            (error_code, document_id),
        )
        self.connection.commit()

    def get_last_request_at(self) -> float | None:
        row = self.connection.execute(
            "SELECT last_request_started_at FROM request_state WHERE singleton = 1"
        ).fetchone()
        return row["last_request_started_at"] if row else None

    def set_last_request_at(self, timestamp: float) -> None:
        self.connection.execute(
            "UPDATE request_state SET last_request_started_at = ? WHERE singleton = 1",
            (timestamp,),
        )
        self.connection.commit()

    def mark_request_started(self, clock: Callable[[], float]) -> None:
        # Take the timestamp after any competing catalog writer has finished.
        # Otherwise database contention could consume the courtesy interval
        # before the HTTP request even starts.
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.set_last_request_at(clock())
        except Exception:
            self.connection.rollback()
            raise

    def get_access_blocked_until(self) -> float | None:
        row = self.connection.execute(
            "SELECT blocked_until FROM access_state WHERE singleton = 1"
        ).fetchone()
        return row["blocked_until"] if row else None

    @staticmethod
    def _stored_document(row: sqlite3.Row) -> StoredCaseDocument:
        return StoredCaseDocument(
            case_id=row["case_id"],
            process_number=row["process_number"],
            process_number_digits=row["process_number_digits"],
            distribution_at=Database._optional_datetime(row["distribution_at"]),
            subject=row["subject"],
            is_secret=Database._optional_bool(row["is_secret"]),
            document_id=row["document_id"],
            retrieved_at=datetime.fromisoformat(row["retrieved_at"].replace("Z", "+00:00")),
            relative_path=row["relative_path"],
            sha256=row["sha256"],
            size_bytes=row["size_bytes"],
            page_count=row["page_count"],
        )

    @staticmethod
    def _optional_datetime(value: str | None) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    @staticmethod
    def _optional_bool(value: int | None) -> bool | None:
        return None if value is None else bool(value)


class PersistentRateLimiter:
    def __init__(
        self,
        database: Database,
        config: ScraperConfig,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.database = database
        self.config = config
        self.clock = clock
        self.sleep = sleep
        self.jitter = jitter

    def wait(self) -> float:
        last = self.database.get_last_request_at()
        now = self.clock()
        courtesy_jitter = self.jitter(0.0, self.config.max_request_jitter_seconds)
        wait_seconds = 0.0
        if last is not None:
            wait_seconds = max(
                0.0,
                self.config.min_request_interval_seconds + courtesy_jitter - (now - last),
            )
        if wait_seconds:
            self.sleep(wait_seconds)
        self.database.mark_request_started(self.clock)
        return wait_seconds
