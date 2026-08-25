from __future__ import annotations

from datetime import datetime, timezone

import pytest
from conftest import PROCESS_DIGITS, PROCESS_NUMBER, PUBLIC_URL, case_html

from datajud_scraper.errors import InvalidInputError, ParseError
from datajud_scraper.parsing import parse_case_page
from datajud_scraper.url_validation import validate_input_url, validate_trusted_target


def test_parse_minimal_legacy_html() -> None:
    source = validate_input_url(PUBLIC_URL)
    metadata = parse_case_page(
        case_html(),
        "text/html;charset=iso-8859-1",
        source,
    )

    assert metadata.process_number == PROCESS_NUMBER
    assert metadata.process_number_digits == PROCESS_DIGITS
    assert metadata.process_year == 2025
    assert metadata.projudi_internal_id == "6020251285346"
    assert metadata.subject == "Indenização por Dano Material « DIREITO DO CONSUMIDOR"
    assert metadata.is_secret is False
    assert metadata.distribution_at == datetime(2025, 4, 3, 12, 2, 17, tzinfo=timezone.utc)


def test_unknown_secrecy_returns_partial_metadata() -> None:
    metadata = parse_case_page(
        case_html(
            secret="INDEFINIDO",
            include_subject=False,
            include_distribution=False,
            include_download=False,
        ),
        "text/html;charset=iso-8859-1",
        validate_input_url(PUBLIC_URL),
    )

    assert metadata.process_number == PROCESS_NUMBER
    assert metadata.is_secret is None
    assert metadata.subject is None
    assert metadata.distribution_at is None
    assert metadata.projudi_internal_id is None


def test_secret_case_stops_before_other_fields() -> None:
    metadata = parse_case_page(
        case_html(
            secret="SIM",
            include_subject=False,
            include_distribution=False,
            include_download=False,
        ),
        "text/html;charset=iso-8859-1",
        validate_input_url(PUBLIC_URL),
    )

    assert metadata.process_number == PROCESS_NUMBER
    assert metadata.is_secret is True
    assert metadata.subject is None
    assert metadata.distribution_at is None
    assert metadata.projudi_internal_id is None


def test_missing_subject_is_allowed_for_public_case() -> None:
    metadata = parse_case_page(
        case_html(include_subject=False),
        "text/html;charset=iso-8859-1",
        validate_input_url(PUBLIC_URL),
    )

    assert metadata.is_secret is False
    assert metadata.subject is None
    assert metadata.distribution_at is not None
    assert metadata.projudi_internal_id == "6020251285346"


def test_public_case_still_requires_download_identifier() -> None:
    with pytest.raises(ParseError, match="identificador interno"):
        parse_case_page(
            case_html(include_download=False),
            "text/html;charset=iso-8859-1",
            validate_input_url(PUBLIC_URL),
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://projudi.tjba.jus.br/projudi/AcessoPublico?codigoHash=22f20646",
        "https://example.com/projudi/AcessoPublico?codigoHash=22f20646",
        "https://projudi.tjba.jus.br/otra?codigoHash=22f20646",
        "https://projudi.tjba.jus.br/projudi/AcessoPublico?codigoHash=bad!",
        "https://projudi.tjba.jus.br/projudi/AcessoPublico?codigoHash=ok&extra=1",
    ],
)
def test_rejects_untrusted_or_malformed_urls(url: str) -> None:
    with pytest.raises(InvalidInputError):
        validate_input_url(url)


def test_rejects_redirect_outside_projudi() -> None:
    with pytest.raises(InvalidInputError):
        validate_trusted_target("https://example.com/steal")
