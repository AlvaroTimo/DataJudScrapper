from __future__ import annotations

import hashlib
import os
import random
import re
import sqlite3
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlencode, urljoin

import httpx

from .config import ScraperConfig
from .errors import (
    AccessChallengeError,
    FetchError,
    PdfValidationError,
    ScraperError,
    SessionExpiredError,
    StorageError,
)
from .models import CaseMetadata, PdfValidation, ScrapeResult, ValidatedUrl
from .parsing import decode_html, detect_special_page, parse_case_page
from .pdf_validation import create_temp_file, ensure_disk_space, validate_pdf
from .runtime import (
    Database,
    EventLogger,
    PersistentRateLimiter,
    StorageLock,
    StoragePaths,
    StoredCase,
    StoredCaseDocument,
    utc_now,
)
from .url_validation import TRUSTED_HOST, validate_input_url, validate_trusted_target

DOWNLOAD_PATH = "/projudi/acoes/DownloadProcesso"
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}
BACKOFF_SECONDS = (5.0, 15.0)
MAX_REDIRECTS = 3


@dataclass(slots=True)
class AttemptBudget:
    page: int = 0
    pdf: int = 0


@dataclass(frozen=True, slots=True)
class PageResponse:
    content: bytes
    content_type: str | None


@dataclass(frozen=True, slots=True)
class DownloadedPdf:
    temp_path: Path
    validation: PdfValidation
    mime_type: str
    original_filename: str | None


class _RetryableRequest(Exception):
    def __init__(self, status_code: int | None, retry_after: float | None = None) -> None:
        super().__init__(f"retryable status={status_code}")
        self.status_code = status_code
        self.retry_after = retry_after


class ScraperService:
    def __init__(
        self,
        config: ScraperConfig | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.config = (config or ScraperConfig.from_env()).normalized()
        self.sleep = sleep
        self.jitter = jitter
        self.now = now
        self.paths = StoragePaths(self.config.storage_root)
        self.paths.initialize()
        self.logger = EventLogger(self.paths, self.config)

    def scrape_url(self, url: str, refresh: bool = False) -> ScrapeResult:
        run_id = str(uuid.uuid4())
        started_at = self.now()
        input_fingerprint = hashlib.sha256(url.encode("utf-8", errors="replace")).hexdigest()

        with StorageLock(
            self.paths.lock,
            self.config.lock_timeout_seconds,
            sleep=self.sleep,
        ):
            database = Database(self.paths.database)
            run_started = False
            try:
                database.start_run(run_id, input_fingerprint, started_at)
                run_started = True
                self.logger.emit("run_started", run_id=run_id, refresh=refresh)
                source = validate_input_url(url)
                result = self._scrape_locked(database, source, run_id, refresh)
                database.finish_run(
                    run_id,
                    result.status,
                    finished_at=self.now(),
                    bytes_received=result.size_bytes,
                )
                self.logger.emit(
                    "run_finished",
                    run_id=run_id,
                    process_number=result.process_number,
                    status=result.status,
                    bytes=result.size_bytes,
                    sha256=result.sha256,
                )
                return result
            except ScraperError as exc:
                exc.run_id = run_id
                if isinstance(exc, AccessChallengeError):
                    now_timestamp = time.time()
                    database.activate_access_cooldown(
                        now_timestamp,
                        now_timestamp + self.config.challenge_cooldown_seconds,
                    )
                if run_started:
                    self._finish_failed_run(database, run_id, exc)
                self._safe_log(
                    "run_failed",
                    level="error",
                    run_id=run_id,
                    error_code=exc.code,
                )
                raise
            except (OSError, sqlite3.Error) as exc:
                wrapped = StorageError("fallo inesperado del almacenamiento", run_id=run_id)
                if run_started:
                    self._finish_failed_run(database, run_id, wrapped)
                self._safe_log(
                    "run_failed",
                    level="error",
                    run_id=run_id,
                    error_code=wrapped.code,
                    error_type=type(exc).__name__,
                )
                raise wrapped from exc
            except Exception as exc:
                wrapped = ScraperError("fallo interno inesperado", run_id=run_id)
                if run_started:
                    self._finish_failed_run(database, run_id, wrapped)
                self._safe_log(
                    "run_failed",
                    level="error",
                    run_id=run_id,
                    error_code=wrapped.code,
                    error_type=type(exc).__name__,
                )
                raise wrapped from exc
            finally:
                database.close()

    def _scrape_locked(
        self,
        database: Database,
        source: ValidatedUrl,
        run_id: str,
        refresh: bool,
    ) -> ScrapeResult:
        removed = self.paths.cleanup_stale_temp(self.config.stale_temp_hours)
        if removed:
            self.logger.emit("stale_temp_cleaned", run_id=run_id, count=removed)

        cached_case = database.find_case_by_url(source.canonical_url)
        if cached_case is not None and not refresh:
            if cached_case.is_secret is True:
                database.attach_case(run_id, cached_case.case_id)
                return self._result_from_cached_secret(cached_case, run_id)
            if cached_case.is_secret is None:
                database.attach_case(run_id, cached_case.case_id)
                return self._result_from_cached_unknown(cached_case, run_id)

        existing = database.find_latest_valid_by_url(source.canonical_url)
        if existing is not None:
            if self._existing_is_valid(database, existing, run_id):
                if not refresh:
                    database.attach_case(run_id, existing.case_id)
                    return self._result_from_stored(existing, "already_exists", run_id)
            else:
                existing = None

        blocked_until = database.get_access_blocked_until()
        if blocked_until is not None and blocked_until > time.time():
            blocked_at = datetime.fromtimestamp(blocked_until, timezone.utc).isoformat()
            raise AccessChallengeError(
                f"cooldown activo por un desafio anterior hasta {blocked_at}"
            )

        limiter = PersistentRateLimiter(
            database,
            self.config,
            sleep=self.sleep,
            jitter=self.jitter,
        )
        budget = AttemptBudget()
        client = self._new_client()
        try:
            page = self._fetch_page(client, source.canonical_url, run_id, database, limiter, budget)
            metadata = parse_case_page(page.content, page.content_type, source)
            checked_at = self.now()
            case_id = database.upsert_case(metadata, checked_at)
            database.attach_case(run_id, case_id)
            self.logger.emit(
                "case_parsed",
                run_id=run_id,
                process_number=metadata.process_number,
                is_secret=metadata.is_secret,
            )

            if metadata.is_secret is True:
                return ScrapeResult(
                    status="secret_skipped",
                    process_number=metadata.process_number,
                    distribution_at=None,
                    retrieved_at=None,
                    subject=None,
                    is_secret=True,
                    pdf_path=None,
                    sha256=None,
                    size_bytes=None,
                    page_count=None,
                    run_id=run_id,
                )
            if metadata.is_secret is None:
                return ScrapeResult(
                    status="secrecy_unknown",
                    process_number=metadata.process_number,
                    distribution_at=None,
                    retrieved_at=None,
                    subject=None,
                    is_secret=None,
                    pdf_path=None,
                    sha256=None,
                    size_bytes=None,
                    page_count=None,
                    run_id=run_id,
                )

            canonical_existing = database.find_latest_valid_by_case_id(case_id)
            if (
                canonical_existing is not None
                and not refresh
                and self._existing_is_valid(database, canonical_existing, run_id)
            ):
                return self._result_from_stored(
                    canonical_existing,
                    "already_exists",
                    run_id,
                )

            if metadata.projudi_internal_id is None:
                raise FetchError("falta el identificador interno necesario para descargar")

            session_recovered = False
            while budget.pdf < self.config.pdf_attempts:
                budget.pdf += 1
                database.increment_attempt(run_id, "pdf")
                temp_path = self.paths.temp_file(run_id)
                self.paths.remove_managed_file(temp_path)
                try:
                    downloaded = self._download_once(
                        client,
                        metadata,
                        temp_path,
                        run_id,
                        limiter,
                        budget.pdf,
                    )
                    break
                except SessionExpiredError:
                    self.paths.remove_managed_file(temp_path)
                    if session_recovered or budget.pdf >= self.config.pdf_attempts:
                        raise FetchError(
                            "la sesion expiro repetidamente durante la descarga"
                        ) from None
                    session_recovered = True
                    self.logger.emit("session_recovery", run_id=run_id, phase="pdf")
                    client.close()
                    client = self._new_client()
                    page = self._fetch_page(
                        client,
                        source.canonical_url,
                        run_id,
                        database,
                        limiter,
                        budget,
                    )
                    refreshed_metadata = parse_case_page(page.content, page.content_type, source)
                    if (
                        refreshed_metadata.process_number_digits != metadata.process_number_digits
                        or refreshed_metadata.is_secret is not False
                    ):
                        raise FetchError(
                            "el expediente cambio durante la recuperacion de sesion"
                        ) from None
                    metadata = refreshed_metadata
                    if metadata.projudi_internal_id is None:
                        raise FetchError(
                            "falta el identificador interno tras recuperar la sesion"
                        ) from None
                except _RetryableRequest as exc:
                    self.paths.remove_managed_file(temp_path)
                    if budget.pdf >= self.config.pdf_attempts:
                        raise FetchError("se agotaron los intentos de descarga del PDF") from exc
                    self._sleep_before_retry(exc, budget.pdf, run_id, "pdf")
                except httpx.TransportError as exc:
                    self.paths.remove_managed_file(temp_path)
                    if budget.pdf >= self.config.pdf_attempts:
                        raise FetchError("fallo de red al descargar el PDF") from exc
                    self._sleep_before_retry(None, budget.pdf, run_id, "pdf")
                except Exception:
                    self.paths.remove_managed_file(temp_path)
                    raise
            else:
                raise FetchError("no se pudo descargar el PDF")

            duplicate = database.find_valid_by_hash(case_id, downloaded.validation.sha256)
            if duplicate is not None and self._existing_is_valid(database, duplicate, run_id):
                self.paths.remove_managed_file(downloaded.temp_path)
                return self._result_from_stored(duplicate, "unchanged", run_id)

            retrieved_at = self.now()
            final_path, relative_path = self.paths.final_file(
                metadata,
                retrieved_at,
                downloaded.validation.sha256,
            )
            self.paths.publish(downloaded.temp_path, final_path)
            try:
                database.insert_document(
                    case_id=case_id,
                    retrieved_at=retrieved_at,
                    relative_path=relative_path,
                    sha256=downloaded.validation.sha256,
                    size_bytes=downloaded.validation.size_bytes,
                    mime_type=downloaded.mime_type,
                    page_count=downloaded.validation.page_count,
                    original_filename=downloaded.original_filename,
                )
            except sqlite3.Error as exc:
                self.paths.remove_managed_file(final_path)
                raise StorageError("no se pudo catalogar el PDF publicado") from exc

            return ScrapeResult(
                status="downloaded",
                process_number=metadata.process_number,
                distribution_at=metadata.distribution_at,
                retrieved_at=retrieved_at,
                subject=metadata.subject,
                is_secret=False,
                pdf_path=final_path,
                sha256=downloaded.validation.sha256,
                size_bytes=downloaded.validation.size_bytes,
                page_count=downloaded.validation.page_count,
                run_id=run_id,
            )
        finally:
            client.close()

    def _fetch_page(
        self,
        client: httpx.Client,
        url: str,
        run_id: str,
        database: Database,
        limiter: PersistentRateLimiter,
        budget: AttemptBudget,
    ) -> PageResponse:
        last_error: Exception | None = None
        while budget.page < self.config.page_attempts:
            budget.page += 1
            database.increment_attempt(run_id, "page")
            try:
                response = self._read_page_once(client, url, run_id, limiter, budget.page)
                html = decode_html(response.content, response.content_type)
                detect_special_page(html)
                return response
            except AccessChallengeError:
                raise
            except SessionExpiredError as exc:
                last_error = exc
            except _RetryableRequest as exc:
                last_error = exc
                if budget.page < self.config.page_attempts:
                    self._sleep_before_retry(exc, budget.page, run_id, "page")
                    continue
            except httpx.TransportError as exc:
                last_error = exc
                if budget.page < self.config.page_attempts:
                    self._sleep_before_retry(None, budget.page, run_id, "page")
                    continue
            if budget.page < self.config.page_attempts:
                self._sleep_before_retry(None, budget.page, run_id, "page")
        raise FetchError("se agotaron los intentos de obtener la ficha") from last_error

    def _read_page_once(
        self,
        client: httpx.Client,
        url: str,
        run_id: str,
        limiter: PersistentRateLimiter,
        attempt: int,
    ) -> PageResponse:
        current_url = url
        for redirect_count in range(MAX_REDIRECTS + 1):
            waited = limiter.wait()
            started = time.monotonic()
            with client.stream(
                "GET",
                current_url,
                timeout=self._timeout(self.config.page_timeout_seconds),
            ) as response:
                duration_ms = int((time.monotonic() - started) * 1000)
                self.logger.emit(
                    "http_response",
                    run_id=run_id,
                    phase="page",
                    attempt=attempt,
                    status=response.status_code,
                    duration_ms=duration_ms,
                    courtesy_wait_ms=int(waited * 1000),
                )
                if response.status_code in REDIRECT_STATUSES:
                    if redirect_count >= MAX_REDIRECTS:
                        raise FetchError("demasiadas redirecciones al obtener la ficha")
                    location = response.headers.get("Location")
                    if not location:
                        raise FetchError("redireccion sin cabecera Location")
                    current_url = validate_trusted_target(urljoin(current_url, location))
                    continue
                if response.status_code == 403:
                    raise AccessChallengeError("PROJUDI rechazo el acceso con HTTP 403")
                if response.status_code in RETRYABLE_STATUSES:
                    raise _RetryableRequest(
                        response.status_code,
                        self._parse_retry_after(response.headers.get("Retry-After")),
                    )
                if response.status_code != 200:
                    raise FetchError(
                        "respuesta HTTP inesperada al obtener la ficha: "
                        f"{response.status_code}"
                    )
                announced = self._content_length(response.headers.get("Content-Length"))
                if announced is not None and announced > self.config.max_html_bytes:
                    raise FetchError("la ficha supera el limite de tamano configurado")
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > self.config.max_html_bytes:
                        raise FetchError("la ficha supero el limite durante la lectura")
                return PageResponse(bytes(content), response.headers.get("Content-Type"))
        raise FetchError("no se pudo resolver la ficha")

    def _download_once(
        self,
        client: httpx.Client,
        metadata: CaseMetadata,
        temp_path: Path,
        run_id: str,
        limiter: PersistentRateLimiter,
        attempt: int,
    ) -> DownloadedPdf:
        query = urlencode({"numeroProcesso": metadata.projudi_internal_id})
        current_url = f"https://{TRUSTED_HOST}{DOWNLOAD_PATH}?{query}"
        headers = {"Referer": metadata.source_url, "Accept": "application/pdf"}

        for redirect_count in range(MAX_REDIRECTS + 1):
            waited = limiter.wait()
            started = time.monotonic()
            with client.stream(
                "GET",
                current_url,
                headers=headers,
                timeout=self._timeout(self.config.pdf_timeout_seconds),
            ) as response:
                duration_ms = int((time.monotonic() - started) * 1000)
                self.logger.emit(
                    "http_response",
                    run_id=run_id,
                    process_number=metadata.process_number,
                    phase="pdf",
                    attempt=attempt,
                    status=response.status_code,
                    duration_ms=duration_ms,
                    courtesy_wait_ms=int(waited * 1000),
                )
                if response.status_code in REDIRECT_STATUSES:
                    if redirect_count >= MAX_REDIRECTS:
                        raise FetchError("demasiadas redirecciones al descargar el PDF")
                    location = response.headers.get("Location")
                    if not location:
                        raise FetchError("redireccion de PDF sin Location")
                    current_url = validate_trusted_target(urljoin(current_url, location))
                    continue
                if response.status_code == 403:
                    raise AccessChallengeError("PROJUDI rechazo la descarga con HTTP 403")
                if response.status_code in RETRYABLE_STATUSES:
                    raise _RetryableRequest(
                        response.status_code,
                        self._parse_retry_after(response.headers.get("Retry-After")),
                    )
                if response.status_code != 200:
                    raise FetchError(
                        f"respuesta HTTP inesperada al descargar: {response.status_code}"
                    )

                content_type_header = response.headers.get("Content-Type", "")
                mime_type = content_type_header.split(";", 1)[0].strip().lower()
                if mime_type != "application/pdf":
                    diagnostic = bytearray()
                    for chunk in response.iter_bytes():
                        diagnostic.extend(chunk)
                        if len(diagnostic) >= 1024 * 1024:
                            break
                    html = decode_html(bytes(diagnostic), content_type_header)
                    detect_special_page(html)
                    raise PdfValidationError("la descarga no devolvio application/pdf")

                announced = self._content_length(response.headers.get("Content-Length"))
                if announced is not None and announced > self.config.max_pdf_bytes:
                    raise PdfValidationError("el PDF supera el limite de tamano configurado")
                ensure_disk_space(self.paths, self.config, announced)

                digest = hashlib.sha256()
                received = 0
                with create_temp_file(temp_path) as file_handle:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        received += len(chunk)
                        if received > self.config.max_pdf_bytes:
                            raise PdfValidationError("el PDF supero el limite durante la descarga")
                        file_handle.write(chunk)
                        digest.update(chunk)
                    file_handle.flush()
                    os.fsync(file_handle.fileno())
                if announced is not None and received != announced:
                    raise PdfValidationError(
                        f"PDF truncado: esperados={announced} bytes, recibidos={received} bytes"
                    )
                validation = validate_pdf(
                    temp_path,
                    expected_size=received,
                    expected_sha256=digest.hexdigest(),
                )
                self.logger.emit(
                    "pdf_validated",
                    run_id=run_id,
                    process_number=metadata.process_number,
                    bytes=validation.size_bytes,
                    pages=validation.page_count,
                    sha256=validation.sha256,
                )
                return DownloadedPdf(
                    temp_path=temp_path,
                    validation=validation,
                    mime_type=mime_type,
                    original_filename=self._original_filename(
                        response.headers.get("Content-Disposition")
                    ),
                )
        raise FetchError("no se pudo resolver la URL de descarga")

    def _existing_is_valid(
        self,
        database: Database,
        existing: StoredCaseDocument,
        run_id: str,
    ) -> bool:
        path = self.paths.absolute(existing.relative_path)
        try:
            validation = validate_pdf(
                path,
                expected_size=existing.size_bytes,
                expected_sha256=existing.sha256,
            )
            if validation.page_count != existing.page_count:
                raise PdfValidationError("el numero de paginas del PDF existente cambio")
            return True
        except PdfValidationError as exc:
            database.mark_document_invalid(existing.document_id, exc.code)
            self.paths.remove_managed_file(path)
            self.logger.emit(
                "existing_pdf_removed",
                level="warning",
                run_id=run_id,
                process_number=existing.process_number,
                error_code=exc.code,
            )
            return False

    def _new_client(self) -> httpx.Client:
        return httpx.Client(
            headers={
                "User-Agent": self.config.user_agent,
                "Accept-Language": "pt-BR,pt;q=0.9",
            },
            verify=True,
            follow_redirects=False,
        )

    def _timeout(self, read_seconds: float) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.config.connect_timeout_seconds,
            read=read_seconds,
            write=30.0,
            pool=self.config.connect_timeout_seconds,
        )

    def _sleep_before_retry(
        self,
        error: _RetryableRequest | None,
        attempts_used: int,
        run_id: str,
        phase: str,
    ) -> None:
        if error is not None and error.retry_after is not None:
            delay = min(120.0, max(0.0, error.retry_after))
        else:
            index = min(max(attempts_used - 1, 0), len(BACKOFF_SECONDS) - 1)
            delay = BACKOFF_SECONDS[index]
        self.logger.emit(
            "http_retry",
            level="warning",
            run_id=run_id,
            phase=phase,
            delay_seconds=delay,
            status=error.status_code if error else None,
        )
        self.sleep(delay)

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                target = parsedate_to_datetime(value)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=timezone.utc)
                return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None

    @staticmethod
    def _content_length(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(value)
        except ValueError as exc:
            raise PdfValidationError("Content-Length no es numerico") from exc
        if parsed < 0:
            raise PdfValidationError("Content-Length no puede ser negativo")
        return parsed

    @staticmethod
    def _original_filename(value: str | None) -> str | None:
        if not value:
            return None
        extended = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", value, re.I)
        regular = re.search(r"filename\s*=\s*(?:\"([^\"]+)\"|([^;]+))", value, re.I)
        candidate = unquote(extended.group(1)) if extended else None
        if candidate is None and regular:
            candidate = regular.group(1) or regular.group(2)
        if candidate is None:
            return None
        candidate = Path(candidate.strip()).name
        candidate = "".join(
            char for char in candidate if char.isprintable() and char not in "\r\n\0"
        )
        return candidate[:255] or None

    def _result_from_stored(
        self,
        stored: StoredCaseDocument,
        status: Literal["already_exists", "unchanged"],
        run_id: str,
    ) -> ScrapeResult:
        return ScrapeResult(
            status=status,
            process_number=stored.process_number,
            distribution_at=stored.distribution_at,
            retrieved_at=stored.retrieved_at,
            subject=stored.subject,
            is_secret=stored.is_secret,
            pdf_path=self.paths.absolute(stored.relative_path),
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            page_count=stored.page_count,
            run_id=run_id,
        )

    @staticmethod
    def _result_from_cached_secret(stored: StoredCase, run_id: str) -> ScrapeResult:
        return ScrapeResult(
            status="secret_skipped",
            process_number=stored.process_number,
            distribution_at=None,
            retrieved_at=None,
            subject=None,
            is_secret=True,
            pdf_path=None,
            sha256=None,
            size_bytes=None,
            page_count=None,
            run_id=run_id,
        )

    @staticmethod
    def _result_from_cached_unknown(stored: StoredCase, run_id: str) -> ScrapeResult:
        return ScrapeResult(
            status="secrecy_unknown",
            process_number=stored.process_number,
            distribution_at=None,
            retrieved_at=None,
            subject=None,
            is_secret=None,
            pdf_path=None,
            sha256=None,
            size_bytes=None,
            page_count=None,
            run_id=run_id,
        )

    def _finish_failed_run(self, database: Database, run_id: str, error: ScraperError) -> None:
        with suppress(Exception):
            database.finish_run(
                run_id,
                "error",
                finished_at=self.now(),
                error_code=error.code,
                error_message=error.message,
            )

    def _safe_log(self, event: str, **fields: object) -> None:
        with suppress(Exception):
            self.logger.emit(event, **fields)


def scrape_url(
    url: str,
    refresh: bool = False,
    *,
    config: ScraperConfig | None = None,
) -> ScrapeResult:
    return ScraperService(config).scrape_url(url, refresh=refresh)
