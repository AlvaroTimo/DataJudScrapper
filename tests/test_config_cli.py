from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
import respx
from conftest import DOWNLOAD_URL, PUBLIC_URL, case_html

from datajud_scraper.cli import main
from datajud_scraper.config import ScraperConfig


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for name in os.environ:
        if name.startswith("DATAJUD_"):
            monkeypatch.delenv(name)


def test_default_storage_is_data_in_working_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert ScraperConfig.from_env().storage_root == tmp_path / "data"


def test_environment_and_explicit_precedence(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATAJUD_STORAGE_ROOT", str(tmp_path / "environment"))
    monkeypatch.setenv("DATAJUD_USER_AGENT", "DataJudTest/1")
    monkeypatch.setenv("DATAJUD_PAGE_TIMEOUT_SECONDS", "45.5")
    monkeypatch.setenv("DATAJUD_PDF_ATTEMPTS", "4")
    monkeypatch.setenv("DATAJUD_LOG_RETENTION_DAYS", "10")
    monkeypatch.setenv("DATAJUD_MIN_REQUEST_INTERVAL_SECONDS", "8")
    config = ScraperConfig.from_env()
    assert config.storage_root == tmp_path / "environment"
    assert config.user_agent == "DataJudTest/1"
    assert config.page_timeout_seconds == 45.5
    assert config.pdf_attempts == 4
    assert config.log_retention_days == 10
    config = ScraperConfig.from_env(tmp_path / "explicit", min_request_interval_seconds=0)
    assert config.storage_root == tmp_path / "explicit"
    assert config.min_request_interval_seconds == 0


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PAGE_ATTEMPTS", "0"),
        ("PDF_ATTEMPTS", "1.5"),
        ("PAGE_TIMEOUT_SECONDS", "nan"),
        ("PDF_TIMEOUT_SECONDS", "inf"),
        ("CONNECT_TIMEOUT_SECONDS", "0"),
        ("MIN_REQUEST_INTERVAL_SECONDS", "-1"),
        ("MAX_PDF_BYTES", "0"),
        ("MIN_FREE_BYTES", "-1"),
        ("LOG_MAX_BYTES", "0"),
        ("LOCK_TIMEOUT_SECONDS", "-1"),
        ("LOG_RETENTION_DAYS", "-1"),
        ("STALE_TEMP_HOURS", "-1"),
        ("CHALLENGE_COOLDOWN_SECONDS", "-1"),
        ("USER_AGENT", "test\r\nInjected: value"),
        ("STORAGE_ROOT", ""),
    ],
)
def test_invalid_configuration_fails_before_storage_or_network(
    name, value, tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"DATAJUD_{name}", value)
    with respx.mock(assert_all_called=False) as router:
        assert main(["scrape", PUBLIC_URL]) == 2
        assert len(router.calls) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["error_code"] == "invalid_configuration"
    assert not (tmp_path / "data").exists()


def test_direct_config_rejects_fractional_attempts() -> None:
    with pytest.raises(ValueError, match="page_attempts"):
        replace(ScraperConfig(), page_attempts=1.5).normalized()
    with pytest.raises(ValueError, match="page_attempts"):
        ScraperConfig.from_env(page_attempts=1.5)


@respx.mock
def test_cli_download_uses_local_data_and_overrides_invalid_environment(
    tmp_path, monkeypatch, valid_pdf, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATAJUD_MAX_PDF_BYTES", "invalid")
    page_route = respx.get(PUBLIC_URL).respond(
        200, content=case_html(), headers={"Content-Type": "text/html"}
    )
    pdf_route = respx.get(DOWNLOAD_URL).respond(
        200, content=valid_pdf, headers={"Content-Type": "application/pdf"}
    )
    arguments = [
        "scrape", PUBLIC_URL,
        "--max-pdf-bytes", str(len(valid_pdf)),
        "--min-request-interval-seconds", "0",
        "--max-request-jitter-seconds", "0",
        "--min-free-bytes", "0",
    ]
    assert main(arguments) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "downloaded"
    assert Path(result["pdf_path"]).is_relative_to(tmp_path / "data")
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "already_exists"
    assert page_route.call_count == pdf_route.call_count == 1
