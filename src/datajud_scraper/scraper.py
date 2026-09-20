from __future__ import annotations

import hashlib
import math
import os
import random
import re
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlencode, urljoin

import httpx

from .config import ScraperConfig
from .dataset import CASE_PATH, DOWNLOAD_PATH, DatasetRecord
from .errors import (
    AccessChallengeError,
    FetchError,
    IdentityError,
    NotFoundError,
    ParseError,
    PauseError,
    PdfValidationError,
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
    StoragePaths,
    StoredCaseDocument,
    utc_now,
)
from .url_validation import TRUSTED_HOST, validate_trusted_target

REDIRECT_STATUSES = {301, 302, 303, 307, 308}
RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}
BACKOFF_SECONDS = (5.0, 15.0)
MAX_REDIRECTS = 3


@dataclass(frozen=True)
class PageResponse:
    content: bytes
    content_type: str | None


@dataclass(frozen=True)
class DownloadedPdf:
    temp_path: Path
    validation: PdfValidation
    mime_type: str
    original_filename: str | None


class _RetryableRequest(Exception):
    def __init__(self, status_code: int, retry_after: float | None = None):
        self.status_code = status_code
        self.retry_after = retry_after


class ScraperService:
    def __init__(
        self,
        config: ScraperConfig,
        *,
        sleep: Callable = time.sleep,
        jitter: Callable = random.uniform,
        now: Callable = utc_now,
    ):
        self.config = config.normalized()
        self.sleep, self.jitter, self.now = sleep, jitter, now
        self.paths = StoragePaths(self.config.storage_root)
        self.paths.initialize()
        self.logger = EventLogger(self.paths, self.config)

    def scrape_record(
        self,
        record: DatasetRecord,
        database: Database,
        case_id: str,
        run_id: str,
        *,
        refresh: bool = False,
    ) -> ScrapeResult:
        existing = database.find_latest_valid_by_case_id(case_id)
        preserved = self._preserved_contracts(database, existing, run_id) if existing else None
        if preserved and not refresh:
            return preserved
        if (
            existing
            and existing.is_secret is False
            and not refresh
            and self._existing_is_valid(database, existing, run_id)
        ):
            return self._result_from_stored(existing, "already_exists", run_id)
        blocked = database.get_access_blocked_until()
        if blocked and blocked > time.time():
            raise PauseError("access_cooldown", "cooldown de acceso activo", blocked)
        limiter = PersistentRateLimiter(database, self.config, sleep=self.sleep, jitter=self.jitter)
        budget: Counter = Counter()
        client = self._new_client()
        recovered = False
        fallback_used = False
        need_context = True
        source = ValidatedUrl(record.data["source_url"], None)
        effective_id = record.data["projudi_internal_id"]
        temp = self.paths.temp_file(run_id)
        try:
            while True:
                try:
                    if need_context:
                        try:
                            page = self._fetch_page(
                                client,
                                self.config.bootstrap_url,
                                "bootstrap",
                                run_id,
                                database,
                                limiter,
                                budget,
                            )
                            if b"DadosProcesso?numeroProcesso=" not in page.content:
                                raise SessionExpiredError(
                                    "la inicializacion no devolvio una consulta publica"
                                )
                        except (FetchError, ParseError) as exc:
                            raise PauseError(
                                "bootstrap_failed", "fallo persistente de inicializacion"
                            ) from exc
                        try:
                            page = self._fetch_page(
                                client,
                                source.canonical_url,
                                "page",
                                run_id,
                                database,
                                limiter,
                                budget,
                            )
                            metadata = parse_case_page(
                                page.content,
                                page.content_type,
                                source,
                                expected_cnj=record.cnj,
                                expected_id=effective_id,
                            )
                        except (NotFoundError, IdentityError):
                            if fallback_used:
                                raise
                            fallback_used = True
                            query_url = f"https://{TRUSTED_HOST}/projudi/buscas/ProcessosParte"
                            page = self._fetch_page(
                                client,
                                query_url,
                                "resolve",
                                run_id,
                                database,
                                limiter,
                                budget,
                                data={"numeroProcesso": record.cnj},
                            )
                            metadata = parse_case_page(
                                page.content,
                                page.content_type,
                                ValidatedUrl(query_url, None),
                                expected_cnj=record.cnj,
                            )
                            if metadata.projudi_internal_id:
                                metadata = replace(
                                    metadata,
                                    source_url=(
                                        f"https://{TRUSTED_HOST}{CASE_PATH}?numeroProcesso={metadata.projudi_internal_id}"
                                    ),
                                )
                                source = ValidatedUrl(metadata.source_url, None)
                                effective_id = metadata.projudi_internal_id
                        database.upsert_case(metadata, self.now())
                        if metadata.is_secret is not False:
                            return ScrapeResult(
                                "secret_skipped" if metadata.is_secret else "secrecy_unknown",
                                metadata.process_number,
                                metadata.distribution_at,
                                None,
                                metadata.subject,
                                metadata.is_secret,
                                None,
                                None,
                                None,
                                None,
                                run_id,
                            )
                        need_context = False
                    if budget["pdf"] >= self.config.pdf_attempts:
                        raise FetchError("se agotaron los intentos de descargar el PDF")
                    budget["pdf"] += 1
                    database.increment_attempt(run_id, "pdf")
                    self.paths.remove_managed_file(temp)
                    downloaded = self._download_once(
                        client, metadata, temp, run_id, limiter, budget["pdf"]
                    )
                    break
                except SessionExpiredError as exc:
                    if recovered or budget["pdf"] >= self.config.pdf_attempts:
                        if need_context:
                            raise PauseError(
                                "bootstrap_failed", "no se pudo establecer una sesion util"
                            ) from exc
                        raise FetchError("la sesion expiro repetidamente") from exc
                    recovered = True
                    database.increment_attempt(run_id, "session")
                    self.logger.emit("session_recovery", run_id=run_id)
                    client.close()
                    client = self._new_client()
                    need_context = True
                except _RetryableRequest as exc:
                    # Honor server-requested pauses even on the last allowed attempt.
                    self._sleep_before_retry(exc, budget["pdf"], run_id, "pdf", database)
                    if budget["pdf"] >= self.config.pdf_attempts:
                        raise FetchError("se agotaron los intentos de descargar el PDF") from exc
                except httpx.TransportError as exc:
                    if budget["pdf"] >= self.config.pdf_attempts or recovered:
                        raise FetchError("fallo de red durante la descarga") from exc
                    self._sleep_before_retry(None, budget["pdf"], run_id, "pdf", database)
                    recovered = True
                    database.increment_attempt(run_id, "session")
                    client.close()
                    client = self._new_client()
                    need_context = True
            duplicate = database.find_valid_by_hash(case_id, downloaded.validation.sha256)
            preserved = (
                self._preserved_contracts(database, duplicate, run_id) if duplicate else None
            )
            if preserved:
                return preserved
            if duplicate and self._existing_is_valid(database, duplicate, run_id):
                return self._result_from_stored(duplicate, "unchanged", run_id)
            retrieved = self.now()
            final, relative = self.paths.final_file(
                metadata, retrieved, downloaded.validation.sha256
            )
            database.connection.execute(
                "UPDATE runs SET published_path=? WHERE run_id=?", (relative, run_id)
            )
            database.connection.commit()
            self.paths.publish(temp, final)
            try:
                database.insert_document(
                    case_id=case_id,
                    retrieved_at=retrieved,
                    relative_path=relative,
                    sha256=downloaded.validation.sha256,
                    size_bytes=downloaded.validation.size_bytes,
                    mime_type=downloaded.mime_type,
                    page_count=downloaded.validation.page_count,
                    original_filename=downloaded.original_filename,
                )
            except (sqlite3.Error, StorageError) as exc:
                self.paths.remove_managed_file(final)
                raise StorageError("no se pudo catalogar el PDF") from exc
            return ScrapeResult(
                "downloaded",
                metadata.process_number,
                metadata.distribution_at,
                retrieved,
                metadata.subject,
                False,
                final,
                downloaded.validation.sha256,
                downloaded.validation.size_bytes,
                downloaded.validation.page_count,
                run_id,
            )
        finally:
            client.close()
            self.paths.remove_managed_file(temp)

    def _fetch_page(self, client, url, phase, run_id, database, limiter, budget, *, data=None):
        limit = 1 if phase == "resolve" else self.config.page_attempts
        while budget[phase] < limit:
            budget[phase] += 1
            database.increment_attempt(run_id, phase)
            try:
                response = self._read_page_once(
                    client, url, run_id, limiter, budget[phase], phase, data
                )
                detect_special_page(decode_html(response.content, response.content_type))
                return response
            except _RetryableRequest as exc:
                self._sleep_before_retry(exc, budget[phase], run_id, phase, database)
            except httpx.TransportError:
                if budget[phase] < limit:
                    self._sleep_before_retry(None, budget[phase], run_id, phase, database)
        raise FetchError(f"se agotaron los intentos de la fase {phase}")

    def _read_page_once(self, client, url, run_id, limiter, attempt, phase, data):
        current_url = url
        method = "POST" if data is not None else "GET"
        for redirect_count in range(MAX_REDIRECTS + 1):
            waited = limiter.wait()
            with client.stream(
                method,
                current_url,
                data=data,
                headers={"Referer": url},
                timeout=self._timeout(self.config.page_timeout_seconds),
            ) as response:
                self._log_response(response, run_id, phase, attempt, waited)
                if response.status_code in REDIRECT_STATUSES:
                    if redirect_count >= MAX_REDIRECTS or not response.headers.get("Location"):
                        raise FetchError("redireccion invalida al obtener la ficha")
                    current_url = validate_trusted_target(
                        urljoin(current_url, response.headers["Location"])
                    )
                    if response.status_code == 303 or (
                        method == "POST" and response.status_code in (301, 302)
                    ):
                        method, data = "GET", None
                    continue
                if response.status_code in (401, 403):
                    raise AccessChallengeError("PROJUDI rechazo el acceso")
                if response.status_code in (404, 410):
                    raise NotFoundError("no se encontro la ficha")
                if response.status_code in RETRYABLE_STATUSES:
                    self._diagnostic(response)
                    raise _RetryableRequest(
                        response.status_code,
                        self._parse_retry_after(response.headers.get("Retry-After")),
                    )
                if response.status_code != 200:
                    raise FetchError(f"respuesta HTTP inesperada: {response.status_code}")
                announced = self._content_length(response.headers.get("Content-Length"))
                if announced and announced > self.config.max_html_bytes:
                    raise FetchError("la ficha supera el limite de tamano")
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > self.config.max_html_bytes:
                        raise FetchError("la ficha supera el limite de tamano")
                return PageResponse(bytes(content), response.headers.get("Content-Type"))
        raise FetchError("no se pudo obtener la ficha")

    @staticmethod
    def _diagnostic(response):
        content = bytearray()
        for chunk in response.iter_bytes(chunk_size=65536):
            content.extend(chunk)
            if len(content) >= 1024 * 1024:
                break
        return bytes(content)

    def _log_response(self, response, run_id, phase, attempt, waited):
        # The port is diagnostic evidence, never a session token or a cookie value.
        stream = response.extensions.get("network_stream")
        socket = stream.get_extra_info("socket") if stream else None
        self.logger.emit(
            "http_response",
            run_id=run_id,
            phase=phase,
            attempt=attempt,
            status=response.status_code,
            courtesy_wait_ms=int(waited * 1000),
            local_port=socket.getsockname()[1] if socket else None,
        )

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
        headers = {
            "Referer": metadata.source_url,
            "Accept": "application/pdf",
            "Accept-Encoding": "identity",
        }

        for redirect_count in range(MAX_REDIRECTS + 1):
            waited = limiter.wait()
            with client.stream(
                "GET",
                current_url,
                headers=headers,
                timeout=self._timeout(self.config.pdf_timeout_seconds),
            ) as response:
                self._log_response(response, run_id, "pdf", attempt, waited)
                if response.status_code in REDIRECT_STATUSES:
                    if redirect_count >= MAX_REDIRECTS:
                        raise FetchError("demasiadas redirecciones al descargar el PDF")
                    location = response.headers.get("Location")
                    if not location:
                        raise FetchError("redireccion de PDF sin Location")
                    current_url = validate_trusted_target(urljoin(current_url, location))
                    continue
                if response.status_code in (401, 403):
                    raise AccessChallengeError("PROJUDI rechazo la descarga con HTTP 403")
                if response.status_code in RETRYABLE_STATUSES:
                    self._diagnostic(response)
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
                    html = decode_html(self._diagnostic(response), content_type_header)
                    detect_special_page(html)
                    raise PdfValidationError("la descarga no devolvio application/pdf")

                announced = self._content_length(response.headers.get("Content-Length"))
                if announced is not None and announced > self.config.max_pdf_bytes:
                    raise PdfValidationError("el PDF supera el limite de tamano configurado")
                filename = self._original_filename(response.headers.get("Content-Disposition"))
                if filename and filename != f"{metadata.process_number}.pdf":
                    raise PdfValidationError("Content-Disposition no corresponde al CNJ solicitado")
                ensure_disk_space(self.paths, self.config, announced)

                digest = hashlib.sha256()
                received = 0
                with create_temp_file(temp_path) as file_handle:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        if received == 0 and not chunk.startswith(b"%PDF-"):
                            detect_special_page(decode_html(chunk[:65536], content_type_header))
                            raise PdfValidationError("la respuesta no comienza con la firma PDF")
                        ensure_disk_space(self.paths, self.config, None)
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
                    expected_process_number=metadata.process_number,
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
                expected_process_number=existing.process_number,
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

    def _preserved_contracts(
        self, database: Database, stored: StoredCaseDocument, run_id: str
    ) -> ScrapeResult | None:
        # The base scraper remains usable without the optional contracts extension.
        version = database.connection.execute(
            "SELECT value FROM schema_meta WHERE key='contracts_schema_version'"
        ).fetchone()
        if version is None:
            return None
        row = database.connection.execute(
            "SELECT source_disposition FROM documents WHERE document_id=?", (stored.document_id,)
        ).fetchone()
        if row["source_disposition"] == "retained":
            return None
        from .contract_release import managed_path, verified_release

        try:
            has_card_retention = database.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='card_retention_events'"
            ).fetchone()
            card_retention = has_card_retention and database.connection.execute(
                "SELECT 1 FROM card_retention_events WHERE document_id=?", (stored.document_id,)
            ).fetchone()
            if card_retention:
                from .card_retention import verified_card_release

                manifest = verified_card_release(
                    database.connection, self.paths.root, stored.document_id
                )
            else:
                manifest = verified_release(
                    database.connection, self.paths.root, stored.document_id
                )
        except (ValueError, OSError, sqlite3.Error) as exc:
            raise StorageError(
                "la conservacion de contratos requiere reparar o completar su publicacion"
            ) from exc
        return ScrapeResult(
            status="contracts_preserved",
            process_number=stored.process_number,
            distribution_at=stored.distribution_at,
            retrieved_at=stored.retrieved_at,
            subject=stored.subject,
            is_secret=stored.is_secret,
            pdf_path=None,
            sha256=None,
            size_bytes=None,
            page_count=None,
            run_id=run_id,
            contract_count=len(manifest["contracts"]),
            contract_paths=tuple(
                managed_path(self.paths.root, c["path"]) for c in manifest["contracts"]
            ),
        )

    def _new_client(self) -> httpx.Client:
        return httpx.Client(
            headers={
                "User-Agent": self.config.user_agent,
                "Accept-Language": "pt-BR,pt;q=0.9",
            },
            http2=False,
            limits=httpx.Limits(
                max_connections=1, max_keepalive_connections=1, keepalive_expiry=None
            ),
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
        database: Database,
    ) -> None:
        if error is not None and error.retry_after is not None:
            delay = max(0.0, error.retry_after)
            retry_at = time.time() + delay
            with database.connection:
                database.connection.execute(
                    "UPDATE access_state SET blocked_until=?,reason='retry_after' "
                    "WHERE singleton=1",
                    (retry_at,),
                )
            if delay > 60:
                raise PauseError("retry_after", "el servidor solicita una pausa", retry_at)
        else:
            index = min(max(attempts_used - 1, 0), len(BACKOFF_SECONDS) - 1)
            delay = BACKOFF_SECONDS[index] + self.jitter(0, self.config.max_request_jitter_seconds)
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
            number = float(value)
            return max(0.0, number) if math.isfinite(number) else None
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
