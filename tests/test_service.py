from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest
import respx
from conftest import (
    DOWNLOAD_URL,
    EXPIRED,
    INTERNAL_ID,
    PROCESS_NUMBER,
    PUBLIC_URL,
    QUERY_URL,
    SOURCE_URL,
    case_html,
    numbered_record,
    pdf_bytes,
    record_data,
)

from datajud_scraper.batch import BatchService
from datajud_scraper.errors import StorageError
from datajud_scraper.scraper import ScraperService


def rows(summary):
    return [json.loads(line) for line in Path(summary["results_path"]).read_text().splitlines()]


def sql(service, query):
    with sqlite3.connect(service.paths.database) as db:
        return db.execute(query).fetchall()


def page(**kwargs):
    return httpx.Response(200, content=case_html(**kwargs), headers={"Content-Type": "text/html"})


def pdf(content):
    return httpx.Response(200, content=content, headers={"Content-Type": "application/pdf"})


def portal(router, valid_pdf, **page_options):
    boot = router.get(PUBLIC_URL).respond(
        200, content=case_html(), headers={"Set-Cookie": "JSESSIONID=test; Path=/projudi"}
    )
    source = router.get(SOURCE_URL).mock(return_value=page(include_download=False, **page_options))
    download = router.get(DOWNLOAD_URL).mock(return_value=pdf(valid_pdf))
    return boot, source, download


@respx.mock
def test_download_and_reimport_are_idempotent(respx_mock, dataset_factory, test_config, valid_pdf):
    inputs = dataset_factory()
    boot, source, download = portal(respx_mock, valid_pdf)

    def answer(request):
        assert request.headers["Referer"] == SOURCE_URL
        assert request.headers["Cookie"] == "JSESSIONID=test"
        return pdf(valid_pdf)

    download.mock(side_effect=answer)
    service = BatchService(test_config)
    first = service.start(*inputs)
    second = service.start(*inputs)
    resumed = service.resume(first["batch_id"])
    assert first["counts"] == {"downloaded": 1}
    assert second["counts"] == resumed["counts"] == {"already_exists": 1}
    assert second["metrics"]["pdf_attempts"] == second["metrics"]["page_attempts"] == 0
    assert boot.call_count == source.call_count == download.call_count == 1
    result = rows(first)[0]  # The resumed report now records the cache hit.
    assert result["original"]["subject"] == "Assunto original"
    assert result["verified"]["subject"].startswith("Indenização")
    assert result["verified"]["projudi_internal_id"] == INTERNAL_ID
    assert result["verified"]["is_secret"] is False
    assert sql(service, "SELECT COUNT(*) FROM datasets") == [(1,)]
    assert sql(service, "SELECT COUNT(*) FROM cases") == [(1,)]
    assert sql(service, "SELECT COUNT(*) FROM dataset_records") == [(1,)]
    assert sql(service, "SELECT COUNT(*) FROM documents") == [(1,)]
    assert sql(service, "PRAGMA foreign_key_check") == []
    for directory in service.paths.pdfs.rglob("*"):
        if directory.is_dir():
            assert os.stat(directory).st_mode & 0o777 == 0o750
    assert os.stat(service.paths.database).st_mode & 0o777 == 0o640
    assert list(service.paths.tmp.iterdir()) == []
    logs = "".join(p.read_text() for p in service.paths.logs.glob("*"))
    assert "JSESSIONID" not in logs and "<html" not in logs


@pytest.mark.parametrize("damage", ["missing", "truncated", "changed"])
@respx.mock
def test_repair_preserves_document_identity(
    respx_mock,
    dataset_factory,
    test_config,
    valid_pdf,
    damage,
):
    inputs = dataset_factory()
    boot, source, download = portal(respx_mock, valid_pdf)
    service = BatchService(test_config)
    first = service.start(*inputs)
    path = Path(rows(first)[0]["result"]["pdf_path"])
    document = sql(service, "SELECT document_id FROM documents")
    if damage == "missing":
        path.unlink()
    elif damage == "truncated":
        path.write_bytes(valid_pdf[:-20])
    else:
        path.write_bytes(valid_pdf.replace(b"%PDF-1.3", b"%PDF-1.4"))
    repaired = service.resume(first["batch_id"])
    assert repaired["counts"] == {"downloaded": 1}
    assert sql(service, "SELECT document_id FROM documents") == document
    assert len(list(service.paths.pdfs.rglob("*.pdf"))) == 1
    assert boot.call_count == source.call_count == download.call_count == 2
    assert sql(service, "PRAGMA integrity_check") == [("ok",)]


@pytest.mark.parametrize("changed", [False, True])
@respx.mock
def test_refresh_versions(respx_mock, dataset_factory, test_config, valid_pdf, changed):
    inputs = dataset_factory()
    _, _, download = portal(respx_mock, valid_pdf)
    service = BatchService(test_config)
    first = service.start(*inputs)
    download.mock(return_value=pdf(pdf_bytes(pages=2) if changed else valid_pdf))
    result = service.resume(first["batch_id"], refresh=True)
    assert result["counts"] == {"downloaded" if changed else "unchanged": 1}
    assert sql(service, "SELECT COUNT(*) FROM documents") == [(2 if changed else 1,)]


@pytest.mark.parametrize(("secret", "state"), [("SIM", "secret_skipped"), ("?", "secrecy_unknown")])
@respx.mock(assert_all_called=False)
def test_fresh_secrecy_blocks_download(respx_mock, dataset_factory, test_config, secret, state):
    inputs = dataset_factory()
    boot, source, download = portal(
        respx_mock,
        b"",
        secret=secret,
        include_distribution=False,
        include_subject=False,
    )
    service = BatchService(test_config)
    first = service.start(*inputs)
    assert first["counts"] == {state: 1}
    assert rows(first)[0]["verified"]["projudi_internal_id"] == INTERNAL_ID
    assert service.resume(first["batch_id"])["counts"] == {state: 1}
    assert boot.call_count == source.call_count == 1 and download.call_count == 0


@respx.mock
def test_historical_secret_and_warnings_do_not_override_live_page(
    respx_mock,
    dataset_factory,
    test_config,
    valid_pdf,
):
    inputs = dataset_factory([record_data(is_secret=True, subject=None)])
    portal(respx_mock, valid_pdf, include_subject=False)
    summary = BatchService(test_config).start(*inputs)
    assert summary["counts"] == {"downloaded": 1}
    assert rows(summary)[0]["result"]["subject"] is None


@respx.mock
def test_expired_http200_recovers_once(respx_mock, dataset_factory, test_config, valid_pdf):
    inputs = dataset_factory()
    boot, source, download = portal(respx_mock, valid_pdf)
    download.mock(side_effect=[httpx.Response(200, content=EXPIRED), pdf(valid_pdf)])
    summary = BatchService(test_config).start(*inputs)
    assert summary["counts"] == {"downloaded": 1}
    assert boot.call_count == source.call_count == download.call_count == 2
    assert summary["metrics"]["session_recoveries"] == 1


@respx.mock
def test_repeated_expiry_cannot_loop(respx_mock, dataset_factory, test_config, valid_pdf):
    boot, source, download = portal(respx_mock, valid_pdf)
    download.mock(return_value=httpx.Response(200, content=EXPIRED))
    summary = BatchService(test_config).start(*dataset_factory())
    assert summary["counts"] == {"failed": 1}
    assert boot.call_count == source.call_count == download.call_count == 2


@respx.mock(assert_all_called=False)
def test_bootstrap_failure_pauses(respx_mock, dataset_factory, test_config, valid_pdf):
    boot, source, download = portal(respx_mock, valid_pdf)
    boot.mock(return_value=httpx.Response(200, content=EXPIRED))
    summary = BatchService(test_config).start(*dataset_factory())
    assert summary["status"] == "paused" and summary["stop_reason"] == "bootstrap_failed"
    assert boot.call_count == 2 and source.call_count == download.call_count == 0


@pytest.mark.parametrize("wrong_source", [404, "wrong_id", "wrong_cnj"])
@respx.mock(assert_all_called=False)
def test_resolves_once_and_retains_new_id_after_recovery(
    respx_mock,
    dataset_factory,
    test_config,
    valid_pdf,
    wrong_source,
):
    inputs = dataset_factory()
    boot, source, _ = portal(respx_mock, valid_pdf)
    if wrong_source == 404:
        source.respond(404)
    elif wrong_source == "wrong_id":
        source.mock(return_value=page(internal_id="999"))
    else:
        source.mock(return_value=page(cnj="0000317-51.2026.8.05.0105"))
    resolved = respx_mock.post(QUERY_URL).mock(return_value=page(internal_id="999"))
    resolved_source = respx_mock.get(SOURCE_URL.replace(INTERNAL_ID, "999")).mock(
        return_value=page(internal_id="999")
    )

    def resolve(request):
        assert request.content == f"numeroProcesso={PROCESS_NUMBER}".encode()
        return page(internal_id="999")

    resolved.mock(side_effect=resolve)
    download = respx_mock.get(DOWNLOAD_URL.replace(INTERNAL_ID, "999")).mock(
        side_effect=[httpx.Response(200, content=EXPIRED), pdf(valid_pdf)]
    )
    summary = BatchService(test_config).start(*inputs)
    assert summary["counts"] == {"downloaded": 1}
    assert resolved.call_count == source.call_count == resolved_source.call_count == 1
    assert boot.call_count == download.call_count == 2
    entry = rows(summary)[0]
    assert entry["original"]["projudi_internal_id"] == INTERNAL_ID
    assert entry["verified"]["projudi_internal_id"] == "999"


@respx.mock(assert_all_called=False)
def test_resolver_cannot_publish_another_case(respx_mock, dataset_factory, test_config, valid_pdf):
    _, source, download = portal(respx_mock, valid_pdf)
    source.respond(404)
    query = respx_mock.post(QUERY_URL).mock(return_value=page(cnj="0000317-51.2026.8.05.0105"))
    summary = BatchService(test_config).start(*dataset_factory())
    assert summary["counts"] == {"failed": 1}
    assert rows(summary)[0]["result"]["error_code"] == "identity_mismatch"
    assert query.call_count == 1 and download.call_count == 0


@pytest.mark.parametrize("challenge", ["captcha", "403"])
@respx.mock(assert_all_called=False)
def test_challenge_pauses_without_retry(
    respx_mock, dataset_factory, test_config, valid_pdf, challenge
):
    boot, source, download = portal(respx_mock, valid_pdf)
    source.mock(
        return_value=httpx.Response(
            403 if challenge == "403" else 200,
            text="<div class='g-recaptcha'>CAPTCHA</div>",
        )
    )
    service = BatchService(test_config)
    summary = service.start(*dataset_factory())
    assert summary["status"] == "paused" and summary["counts"] == {"pending": 1}
    assert service.resume(summary["batch_id"])["status"] == "paused"
    assert boot.call_count == source.call_count == 1 and download.call_count == 0
    assert sql(service, "SELECT COUNT(*) FROM runs WHERE status='running'") == [(0,)]


@pytest.mark.parametrize("retry_after", ["3600", "date"])
@respx.mock(assert_all_called=False)
def test_long_retry_after_is_saved_in_full(
    respx_mock,
    dataset_factory,
    test_config,
    valid_pdf,
    retry_after,
):
    _, source, download = portal(respx_mock, valid_pdf)
    value = (
        format_datetime(datetime.now(timezone.utc) + timedelta(hours=1))
        if retry_after == "date"
        else retry_after
    )
    source.respond(429, headers={"Retry-After": value})
    service = BatchService(test_config)
    before = time.time()
    summary = service.start(*dataset_factory())
    assert summary["status"] == "paused" and summary["stop_reason"] == "retry_after"
    assert datetime.fromisoformat(summary["next_retry_at"]).timestamp() - before >= 3598
    assert service.resume(summary["batch_id"])["status"] == "paused"
    assert source.call_count == 1 and download.call_count == 0


@respx.mock
def test_short_retry_after_waits(respx_mock, dataset_factory, test_config, valid_pdf):
    _, source, _ = portal(respx_mock, valid_pdf)
    source.mock(side_effect=[httpx.Response(429, headers={"Retry-After": "7"}), page()])
    delays = []
    service = BatchService(
        test_config, service_factory=lambda c: ScraperService(c, sleep=delays.append)
    )
    summary = service.start(*dataset_factory())
    assert summary["counts"] == {"downloaded": 1} and delays == [7.0]


@pytest.mark.parametrize(
    "invalid", ["html", "truncated", "other_cnj", "oversized", "foreign_redirect"]
)
@respx.mock
def test_invalid_downloads_never_publish(
    respx_mock,
    dataset_factory,
    test_config,
    valid_pdf,
    invalid,
):
    _, _, download = portal(respx_mock, valid_pdf)
    if invalid == "html":
        download.respond(200, text="<html>error</html>")
    elif invalid == "truncated":
        download.mock(return_value=pdf(valid_pdf[:-20]))
    elif invalid == "other_cnj":
        download.mock(return_value=pdf(pdf_bytes("0000317-51.2026.8.05.0105")))
    elif invalid == "oversized":
        test_config = replace(test_config, max_pdf_bytes=10)
    else:
        download.respond(302, headers={"Location": "https://example.com/foreign.pdf"})
    service = BatchService(test_config)
    summary = service.start(*dataset_factory())
    assert summary["counts"] == {"failed": 1}
    assert list(service.paths.pdfs.rglob("*.pdf")) == list(service.paths.tmp.iterdir()) == []
    assert sql(service, "SELECT COUNT(*) FROM documents") == [(0,)]


@respx.mock
def test_interruption_resumes_exact_manifest(respx_mock, dataset_factory, test_config, valid_pdf):
    inputs = dataset_factory()
    _, _, download = portal(respx_mock, valid_pdf)

    def interrupt(request):
        raise KeyboardInterrupt

    download.mock(side_effect=interrupt)
    service = BatchService(test_config)
    summary = service.start(*inputs)
    manifest = (Path(summary["report_path"]).parent / "manifest.json").read_bytes()
    assert summary["status"] == "paused" and summary["stop_reason"] == "interrupted"
    assert sql(service, "SELECT COUNT(*) FROM runs WHERE status='running'") == [(0,)]
    inputs[0].unlink()  # Resume uses the imported original, not a changed input file.
    download.mock(return_value=pdf(valid_pdf))
    assert service.resume(summary["batch_id"])["counts"] == {"downloaded": 1}
    assert (Path(summary["report_path"]).parent / "manifest.json").read_bytes() == manifest


@respx.mock
def test_failed_items_require_flag(respx_mock, dataset_factory, test_config, valid_pdf):
    inputs = dataset_factory()
    _, _, download = portal(respx_mock, valid_pdf)
    download.mock(return_value=pdf(b"bad"))
    service = BatchService(test_config)
    first = service.start(*inputs)
    assert service.resume(first["batch_id"])["counts"] == {"failed": 1}
    assert download.call_count == 1
    download.mock(return_value=pdf(valid_pdf))
    assert service.resume(first["batch_id"], retry_failed=True)["counts"] == {"downloaded": 1}
    assert download.call_count == 2


@respx.mock
def test_five_infrastructure_failures_pause_without_replacements(
    respx_mock,
    dataset_factory,
    test_config,
):
    records = [numbered_record(i) for i in range(1, 20)]
    requested = []
    respx_mock.get(PUBLIC_URL).mock(return_value=page())
    for record in records[:5]:
        respx_mock.get(record["source_url"]).mock(
            return_value=page(
                cnj=record["process_number"],
                internal_id=record["projudi_internal_id"],
            )
        )
        respx_mock.get(DOWNLOAD_URL.replace(INTERNAL_ID, record["projudi_internal_id"])).respond(
            500
        )
    service = BatchService(
        test_config, service_factory=lambda c: ScraperService(c, sleep=requested.append)
    )
    summary = service.start(*dataset_factory(records), sample="first")
    assert summary["selected"] == 15
    assert summary["counts"] == {"failed": 5, "pending": 10}
    assert summary["stop_reason"] == "infrastructure_failures"
    assert service.resume(summary["batch_id"])["counts"] == summary["counts"]


@respx.mock
def test_atomic_publication_failure_removes_pdf(
    respx_mock,
    dataset_factory,
    test_config,
    valid_pdf,
    monkeypatch,
):
    from datajud_scraper.runtime import Database

    portal(respx_mock, valid_pdf)

    def fail(*args, **kwargs):
        raise StorageError("disk failure")

    monkeypatch.setattr(Database, "insert_document", fail)
    service = BatchService(test_config)
    summary = service.start(*dataset_factory())
    assert summary["status"] == "paused"
    assert list(service.paths.pdfs.rglob("*.pdf")) == list(service.paths.tmp.iterdir()) == []


@respx.mock
def test_hard_crash_journal_removes_orphan_and_repairs_running_state(
    respx_mock,
    dataset_factory,
    test_config,
    valid_pdf,
):
    portal(respx_mock, valid_pdf)
    service = BatchService(test_config)
    summary = service.start(*dataset_factory())
    run_id = rows(summary)[0]["result"]["run_id"]
    published = Path(rows(summary)[0]["result"]["pdf_path"])
    # Simulate a kill between the rename and the document INSERT / item checkpoint.
    with sqlite3.connect(service.paths.database) as db:
        db.execute("DELETE FROM documents")
        db.execute("UPDATE runs SET status='running',finished_at=NULL WHERE run_id=?", (run_id,))
        db.execute("UPDATE batch_items SET status='running'")
    temporary = service.paths.temp_file(run_id)
    temporary.write_bytes(b"partial")
    assert published.exists()
    summary = service.resume(summary["batch_id"])
    assert summary["counts"] == {"downloaded": 1}
    assert not temporary.exists()
    assert len(list(service.paths.pdfs.rglob("*.pdf"))) == 1
    assert sql(service, "SELECT COUNT(*) FROM runs WHERE status='running'") == [(0,)]
    assert sql(service, "SELECT status FROM runs ORDER BY started_at")[0] == ("interrupted",)


@respx.mock
def test_isolated_failure_does_not_replace_or_stop_selected_records(
    respx_mock,
    dataset_factory,
    test_config,
):
    records = [numbered_record(i) for i in (1, 2, 3)]
    respx_mock.get(PUBLIC_URL).mock(return_value=page())
    for index, record in enumerate(records[:2]):
        respx_mock.get(record["source_url"]).mock(
            return_value=page(
                cnj=record["process_number"],
                internal_id=record["projudi_internal_id"],
            )
        )
        respx_mock.get(DOWNLOAD_URL.replace(INTERNAL_ID, record["projudi_internal_id"])).mock(
            return_value=pdf(b"bad" if index == 0 else pdf_bytes(record["process_number"])),
        )
    summary = BatchService(test_config).start(*dataset_factory(records), limit=2, sample="first")
    assert summary["counts"] == {"failed": 1, "downloaded": 1}
    assert [r["process_number"] for r in rows(summary)] == [
        r["process_number"] for r in records[:2]
    ]


@pytest.mark.parametrize("interrupt", [False, True])
@respx.mock
def test_streaming_failure_cleans_partial_file(
    respx_mock,
    dataset_factory,
    test_config,
    valid_pdf,
    interrupt,
):
    _, _, download = portal(respx_mock, valid_pdf)

    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield valid_pdf
            if interrupt:
                raise KeyboardInterrupt
            raise httpx.ReadError("connection lost")

    download.mock(
        side_effect=lambda request: httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            stream=BrokenStream(),
        )
    )
    service = BatchService(
        test_config, service_factory=lambda c: ScraperService(c, sleep=lambda _: None)
    )
    summary = service.start(*dataset_factory())
    assert summary["counts"] == {"pending" if interrupt else "failed": 1}
    assert list(service.paths.tmp.iterdir()) == list(service.paths.pdfs.rglob("*.pdf")) == []
