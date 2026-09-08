from __future__ import annotations

from datetime import datetime, timezone

import pytest
from conftest import PROCESS_DIGITS, PROCESS_NUMBER, PUBLIC_URL, case_html

from datajud_scraper.errors import InvalidInputError, ParseError
from datajud_scraper.parsing import decode_html, parse_case_page
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


@pytest.mark.parametrize(
    ("declaration", "content_type"),
    [
        ('<meta charset="utf-8">', "text/html"),
        ('<meta charset="utf-8">', None),
        ('<meta http-equiv="Content-Type" content="text/html; charset=utf-8">', None),
        ("", "text/html"),
        ("", "text/html; charset=unknown-encoding"),
        ("", "text/html; charset=utf-8"),
    ],
)
def test_utf8_page_remains_public_with_or_without_charset(declaration, content_type) -> None:
    html = case_html().decode("iso-8859-1")
    content = html.replace('<meta charset="iso-8859-1">', declaration).encode("utf-8")
    metadata = parse_case_page(content, content_type, validate_input_url(PUBLIC_URL))

    assert metadata.is_secret is False
    assert metadata.subject == "Indenização por Dano Material « DIREITO DO CONSUMIDOR"
    assert metadata.distribution_at == datetime(2025, 4, 3, 12, 2, 17, tzinfo=timezone.utc)
    assert metadata.projudi_internal_id == "6020251285346"


def test_encoding_declarations_and_bom_take_precedence() -> None:
    html = '<meta charset="windows-1252"><p>NÃO — Justiça</p>'
    assert decode_html(html.encode("cp1252"), None) == html
    latin = '<meta charset="utf-8"><p>NÃO</p>'
    assert decode_html(latin.encode("iso-8859-1"), "text/html; charset=iso-8859-1") == latin
    assert decode_html(latin.encode("utf-8-sig"), "text/html; charset=iso-8859-1") == latin


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
