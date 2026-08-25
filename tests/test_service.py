from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import replace
from io import BytesIO

import httpx
import pytest
import respx
from conftest import DOWNLOAD_URL, PROCESS_NUMBER, PUBLIC_URL, case_html
from pypdf import PdfWriter

from datajud_scraper.config import ScraperConfig
from datajud_scraper.errors import (
    AccessChallengeError,
    InvalidInputError,
    PdfValidationError,
    StorageError,
)
from datajud_scraper.scraper import ScraperService


def make_test_config(tmp_path) -> ScraperConfig:
    return replace(
        ScraperConfig(storage_root=tmp_path),
        min_request_interval_seconds=0,
        max_request_jitter_seconds=0,
        min_free_bytes=0,
        lock_timeout_seconds=0,
    ).normalized()


def page_response(
    *,
    secret: str = "NÃO",
    include_subject: bool = True,
    include_distribution: bool = True,
    include_download: bool = True,
) -> httpx.Response:
    return httpx.Response(
        200,
        content=case_html(
            secret=secret,
            include_subject=include_subject,
            include_distribution=include_distribution,
            include_download=include_download,
        ),
        headers={
            "Content-Type": "text/html;charset=iso-8859-1",
            "Set-Cookie": "JSESSIONID=test-session; Path=/projudi; HttpOnly",
        },
    )


@respx.mock
def test_download_preserves_session_and_second_run_uses_no_network(
    tmp_path, valid_pdf: bytes
) -> None:
    page_route = respx.get(PUBLIC_URL).mock(return_value=page_response())

    def download_callback(request: httpx.Request) -> httpx.Response:
        assert "JSESSIONID=test-session" in request.headers.get("Cookie", "")
        assert request.headers["Referer"] == PUBLIC_URL
        return httpx.Response(
            200,
            content=valid_pdf,
            headers={
                "Content-Type": "application/pdf;charset=iso-8859-1",
                "Content-Length": str(len(valid_pdf)),
                "Content-Disposition": f'attachment; filename="{PROCESS_NUMBER}.pdf"',
            },
        )

    pdf_route = respx.get(DOWNLOAD_URL).mock(side_effect=download_callback)
    service = ScraperService(make_test_config(tmp_path))

    first = service.scrape_url(PUBLIC_URL)
    second = service.scrape_url(PUBLIC_URL)

    assert first.status == "downloaded"
    assert first.pdf_path is not None and first.pdf_path.is_file()
    assert first.page_count == 1
    assert second.status == "already_exists"
    assert second.pdf_path == first.pdf_path
    assert page_route.call_count == 1
    assert pdf_route.call_count == 1
    for directory in (tmp_path / "pdfs").rglob("*"):
        if directory.is_dir():
            assert os.stat(directory).st_mode & 0o777 == 0o750

    database = sqlite3.connect(tmp_path / "state" / "scraper.sqlite3")
    assert database.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 1
    assert database.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    assert database.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
    assert os.stat(tmp_path / "state" / "scraper.sqlite3").st_mode & 0o777 == 0o640


@respx.mock
def test_refresh_with_same_pdf_returns_unchanged(tmp_path, valid_pdf: bytes) -> None:
    page_route = respx.get(PUBLIC_URL).mock(return_value=page_response())
    pdf_route = respx.get(DOWNLOAD_URL).mock(
        return_value=httpx.Response(
            200,
            content=valid_pdf,
            headers={"Content-Type": "application/pdf", "Content-Length": str(len(valid_pdf))},
        )
    )
    service = ScraperService(make_test_config(tmp_path))
    first = service.scrape_url(PUBLIC_URL)
    refreshed = service.scrape_url(PUBLIC_URL, refresh=True)

    assert first.status == "downloaded"
    assert refreshed.status == "unchanged"
    assert refreshed.pdf_path == first.pdf_path
    assert page_route.call_count == 2
    assert pdf_route.call_count == 2
    assert list((tmp_path / "tmp").glob("*.part")) == []


@respx.mock
def test_refresh_with_changed_pdf_creates_new_version(tmp_path, valid_pdf: bytes) -> None:
    changed_output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_blank_page(width=72, height=72)
    writer.write(changed_output)
    changed_pdf = changed_output.getvalue()

    respx.get(PUBLIC_URL).mock(return_value=page_response())
    respx.get(DOWNLOAD_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                content=valid_pdf,
                headers={"Content-Type": "application/pdf"},
            ),
            httpx.Response(
                200,
                content=changed_pdf,
                headers={"Content-Type": "application/pdf"},
            ),
        ]
    )
    service = ScraperService(make_test_config(tmp_path))

    first = service.scrape_url(PUBLIC_URL)
    refreshed = service.scrape_url(PUBLIC_URL, refresh=True)

    assert first.status == "downloaded"
    assert refreshed.status == "downloaded"
    assert refreshed.page_count == 2
    assert refreshed.sha256 != first.sha256
    assert len(list((tmp_path / "pdfs").rglob("*.pdf"))) == 2
    database = sqlite3.connect(tmp_path / "state" / "scraper.sqlite3")
    assert database.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 2


@respx.mock
def test_secret_case_never_calls_pdf_endpoint(tmp_path) -> None:
    page_route = respx.get(PUBLIC_URL).mock(
        return_value=page_response(
            secret="SIM",
            include_subject=False,
            include_distribution=False,
            include_download=False,
        )
    )
    pdf_route = respx.get(DOWNLOAD_URL)
    service = ScraperService(make_test_config(tmp_path))

    result = service.scrape_url(PUBLIC_URL)
    cached = service.scrape_url(PUBLIC_URL)

    assert result.status == "secret_skipped"
    assert cached.status == "secret_skipped"
    assert page_route.call_count == 1
    assert pdf_route.call_count == 0


@respx.mock
def test_unknown_secrecy_is_cached_and_never_downloaded(tmp_path) -> None:
    page_route = respx.get(PUBLIC_URL).mock(
        return_value=page_response(
            secret="FORMATO_NUEVO",
            include_subject=False,
            include_distribution=False,
            include_download=False,
        )
    )
    pdf_route = respx.get(DOWNLOAD_URL)
    service = ScraperService(make_test_config(tmp_path))

    result = service.scrape_url(PUBLIC_URL)
    cached = service.scrape_url(PUBLIC_URL)

    assert result.status == "secrecy_unknown"
    assert result.is_secret is None
    assert result.subject is None
    assert cached.status == "secrecy_unknown"
    assert page_route.call_count == 1
    assert pdf_route.call_count == 0
    database = sqlite3.connect(tmp_path / "state" / "scraper.sqlite3")
    assert database.execute("SELECT is_secret FROM cases").fetchone()[0] is None


@respx.mock
def test_missing_subject_does_not_block_download(tmp_path, valid_pdf: bytes) -> None:
    respx.get(PUBLIC_URL).mock(return_value=page_response(include_subject=False))
    pdf_route = respx.get(DOWNLOAD_URL).mock(
        return_value=httpx.Response(
            200,
            content=valid_pdf,
            headers={"Content-Type": "application/pdf"},
        )
    )

    result = ScraperService(make_test_config(tmp_path)).scrape_url(PUBLIC_URL)

    assert result.status == "downloaded"
    assert result.subject is None
    assert pdf_route.call_count == 1


@respx.mock
def test_new_url_for_same_cnj_updates_source_without_duplicate_download(
    tmp_path, valid_pdf: bytes
) -> None:
    alternate_url = (
        "https://projudi.tjba.jus.br/projudi/AcessoPublico?codigoHash=abcdef12"
    )
    original_page = respx.get(PUBLIC_URL).mock(return_value=page_response())
    alternate_page = respx.get(alternate_url).mock(return_value=page_response())
    pdf_route = respx.get(DOWNLOAD_URL).mock(
        return_value=httpx.Response(
            200,
            content=valid_pdf,
            headers={"Content-Type": "application/pdf"},
        )
    )
    service = ScraperService(make_test_config(tmp_path))

    first = service.scrape_url(PUBLIC_URL)
    same_case_new_source = service.scrape_url(alternate_url)
    original_again = service.scrape_url(PUBLIC_URL)

    assert first.status == "downloaded"
    assert same_case_new_source.status == "already_exists"
    assert original_again.status == "already_exists"
    assert original_page.call_count == 1
    assert alternate_page.call_count == 1
    assert pdf_route.call_count == 1

    database = sqlite3.connect(tmp_path / "state" / "scraper.sqlite3")
    assert database.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 1
    assert database.execute("SELECT COUNT(*) FROM case_sources").fetchone()[0] == 2
    assert database.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    assert database.execute("SELECT source_url FROM cases").fetchone()[0] == alternate_url


@respx.mock
def test_session_expired_html_is_recovered_once(tmp_path, valid_pdf: bytes) -> None:
    page_route = respx.get(PUBLIC_URL).mock(
        side_effect=[page_response(), page_response()]
    )
    expired = b"<html><title>Sistema CNJ - A sessao expirou</title></html>"
    pdf_route = respx.get(DOWNLOAD_URL).mock(
        side_effect=[
            httpx.Response(200, content=expired, headers={"Content-Type": "text/html"}),
            httpx.Response(
                200,
                content=valid_pdf,
                headers={"Content-Type": "application/pdf", "Content-Length": str(len(valid_pdf))},
            ),
        ]
    )
    service = ScraperService(make_test_config(tmp_path))

    result = service.scrape_url(PUBLIC_URL)

    assert result.status == "downloaded"
    assert page_route.call_count == 2
    assert pdf_route.call_count == 2


@respx.mock
def test_captcha_stops_without_download(tmp_path) -> None:
    page_route = respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200,
            text="<html><div class='g-recaptcha'>CAPTCHA</div></html>",
            headers={"Content-Type": "text/html;charset=utf-8"},
        )
    )
    pdf_route = respx.get(DOWNLOAD_URL)

    with pytest.raises(AccessChallengeError):
        service = ScraperService(make_test_config(tmp_path))
        service.scrape_url(PUBLIC_URL)

    with pytest.raises(AccessChallengeError, match="cooldown"):
        service.scrape_url(PUBLIC_URL)

    assert page_route.call_count == 1
    assert pdf_route.call_count == 0


@respx.mock
def test_retry_after_is_respected_before_retry(tmp_path, valid_pdf: bytes) -> None:
    page_route = respx.get(PUBLIC_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            page_response(),
        ]
    )
    respx.get(DOWNLOAD_URL).mock(
        return_value=httpx.Response(
            200,
            content=valid_pdf,
            headers={"Content-Type": "application/pdf"},
        )
    )
    delays: list[float] = []
    service = ScraperService(make_test_config(tmp_path), sleep=delays.append)

    result = service.scrape_url(PUBLIC_URL)

    assert result.status == "downloaded"
    assert page_route.call_count == 2
    assert 0.0 in delays


@respx.mock
def test_external_redirect_is_rejected_without_following_it(tmp_path) -> None:
    page_route = respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(302, headers={"Location": "https://example.com/steal"})
    )
    external_route = respx.get("https://example.com/steal")

    with pytest.raises(InvalidInputError):
        ScraperService(make_test_config(tmp_path)).scrape_url(PUBLIC_URL)

    assert page_route.call_count == 1
    assert external_route.call_count == 0


@respx.mock
def test_insufficient_disk_space_stops_before_body_is_published(tmp_path, valid_pdf: bytes) -> None:
    respx.get(PUBLIC_URL).mock(return_value=page_response())
    respx.get(DOWNLOAD_URL).mock(
        return_value=httpx.Response(
            200,
            content=valid_pdf,
            headers={"Content-Type": "application/pdf", "Content-Length": str(len(valid_pdf))},
        )
    )
    config = replace(make_test_config(tmp_path), min_free_bytes=10**30)

    with pytest.raises(StorageError, match="espacio insuficiente"):
        ScraperService(config).scrape_url(PUBLIC_URL)

    assert list((tmp_path / "pdfs").rglob("*.pdf")) == []


@respx.mock
def test_invalid_pdf_is_removed_and_error_is_logged(tmp_path) -> None:
    respx.get(PUBLIC_URL).mock(return_value=page_response())
    respx.get(DOWNLOAD_URL).mock(
        return_value=httpx.Response(
            200,
            content=b"%PDF-this-is-truncated",
            headers={"Content-Type": "application/pdf", "Content-Length": "22"},
        )
    )

    with pytest.raises(PdfValidationError):
        ScraperService(make_test_config(tmp_path)).scrape_url(PUBLIC_URL)

    assert list((tmp_path / "pdfs").rglob("*.pdf")) == []
    assert list((tmp_path / "tmp").glob("*.part")) == []
    logs = "\n".join(path.read_text() for path in (tmp_path / "logs").glob("*.jsonl"))
    records = [json.loads(line) for line in logs.splitlines()]
    assert any(record["event"] == "run_failed" for record in records)
    assert "JSESSIONID" not in logs
