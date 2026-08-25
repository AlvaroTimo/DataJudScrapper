from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

from .errors import AccessChallengeError, ParseError, SessionExpiredError
from .models import CaseMetadata, ValidatedUrl

PROCESS_RE = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
INTERNAL_ID_RE = re.compile(r"chamaDownloadProcesso\(\s*(\d+)\s*\)")
DATE_RE = re.compile(
    r"(?P<day>\d{1,2})\s+de\s+(?P<month>[A-Za-zÀ-ÿ]+)\s+de\s+"
    r"(?P<year>\d{4})\s+(?:às|as)\s+(?P<hour>\d{1,2}):"
    r"(?P<minute>\d{2}):(?P<second>\d{2})",
    re.IGNORECASE,
)
MONTHS = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}


def normalize_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def fold_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", normalize_text(value))
    return "".join(char for char in normalized if not unicodedata.combining(char)).casefold()


def decode_html(content: bytes, content_type: str | None) -> str:
    charset_match = re.search(r"charset\s*=\s*['\"]?([^;\s'\"]+)", content_type or "", re.I)
    candidates = [charset_match.group(1) if charset_match else None, "iso-8859-1", "utf-8"]
    for encoding in candidates:
        if not encoding:
            continue
        try:
            return content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("iso-8859-1", errors="replace")


def detect_special_page(html: str) -> None:
    folded = fold_text(html)
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    folded_title = fold_text(title_match.group(1)) if title_match else ""
    if "a sessao expirou" in folded_title or "/projudi/sessionexpired" in folded:
        raise SessionExpiredError("PROJUDI devolvio una pagina de sesion expirada")
    markup_markers = (
        "g-recaptcha",
        "hcaptcha",
        "cf-chl-",
    )
    challenge_title = "captcha" in folded_title or "access denied" in folded_title
    short_challenge_page = len(html) < 500_000 and "nao sou um robo" in folded
    if (
        any(marker in html.casefold() for marker in markup_markers)
        or challenge_title
        or short_challenge_page
    ):
        raise AccessChallengeError("PROJUDI presento un desafio anti-automatizacion")


def _cell_after_label(soup: BeautifulSoup, label: str) -> str | None:
    wanted = fold_text(label).rstrip(":")
    for cell in soup.find_all(["td", "th"]):
        if not isinstance(cell, Tag):
            continue
        cell_text = fold_text(cell.get_text(" ", strip=True)).rstrip(":")
        if cell_text != wanted:
            continue
        sibling = cell.find_next_sibling(["td", "th"])
        if sibling is None:
            sibling = cell.find_next(["td", "th"])
        if isinstance(sibling, Tag):
            value = normalize_text(sibling.get_text(" ", strip=True))
            if value:
                return value
    return None


def _validate_cnj_number(process_number: str) -> tuple[str, int]:
    digits = re.sub(r"\D", "", process_number)
    if len(digits) != 20:
        raise ParseError("el numero CNJ no tiene 20 digitos")
    base = digits[:7] + digits[9:] + "00"
    expected = 98 - (int(base) % 97)
    if expected != int(digits[7:9]):
        raise ParseError("el digito verificador del numero CNJ es invalido")
    return digits, int(digits[9:13])


def _parse_distribution_date(value: str) -> datetime:
    match = DATE_RE.search(value)
    if not match:
        raise ParseError("no se pudo interpretar la fecha de distribucion")
    month_key = fold_text(match.group("month"))
    month = MONTHS.get(month_key)
    if month is None:
        raise ParseError("el mes de la fecha de distribucion es desconocido")
    local = datetime(
        int(match.group("year")),
        month,
        int(match.group("day")),
        int(match.group("hour")),
        int(match.group("minute")),
        int(match.group("second")),
        tzinfo=ZoneInfo("America/Bahia"),
    )
    return local.astimezone(timezone.utc)


def parse_case_page(
    content: bytes,
    content_type: str | None,
    source: ValidatedUrl,
) -> CaseMetadata:
    html = decode_html(content, content_type)
    detect_special_page(html)
    soup = BeautifulSoup(html, "lxml")
    page_text = normalize_text(soup.get_text(" ", strip=True))

    process_match = PROCESS_RE.search(page_text)
    if not process_match:
        raise ParseError("no se encontro el numero CNJ")
    process_number = process_match.group(0)
    digits, process_year = _validate_cnj_number(process_number)

    secrecy_value = _cell_after_label(soup, "Segredo de Justiça")
    if secrecy_value is None:
        secrecy: bool | None = None
    else:
        folded_secrecy = fold_text(secrecy_value)
        if folded_secrecy == "nao":
            secrecy = False
        elif folded_secrecy == "sim":
            secrecy = True
        else:
            secrecy = None

    partial_metadata = {
        "process_number": process_number,
        "process_number_digits": digits,
        "process_year": process_year,
        "source_url": source.canonical_url,
        "codigo_hash": source.codigo_hash,
    }
    if secrecy is not False:
        return CaseMetadata(
            **partial_metadata,
            projudi_internal_id=None,
            distribution_at=None,
            subject=None,
            is_secret=secrecy,
        )

    distribution_value = _cell_after_label(soup, "Data de Distribuição")
    if not distribution_value:
        raise ParseError("no se encontro la fecha de distribucion")

    subject = _cell_after_label(soup, "Assunto")
    internal_match = INTERNAL_ID_RE.search(html)
    if not internal_match:
        raise ParseError("no se encontro el identificador interno de descarga")

    return CaseMetadata(
        **partial_metadata,
        projudi_internal_id=internal_match.group(1),
        distribution_at=_parse_distribution_date(distribution_value),
        subject=subject,
        is_secret=False,
    )
