"""Privacy policy for rebuilding anonymized contracts (scripts/recrear_report.py).

The original anonymization covered whole lines. Rebuilding restores a covered line only when
it is contract boilerplate: the same normalized line appears in at least MIN_EXPEDIENTES
distinct expedientes, it reads like contract prose, it carries no identifier pattern and it
was approved by the line review (scripts/recrear_report.py revisar). Everything uncertain
stays blank. Identifiers left visible by the original anonymization are blanked as well.
"""

from __future__ import annotations

import re
import unicodedata

MIN_EXPEDIENTES = 3

STOPWORDS = frozenset(
    """a ao aos as à às com como da das de do dos e em entre esta este eu me meu meus minha
    minhas na nas no nos o os ou para pela pelas pelo pelos por qual quando que se sem ser
    seu seus sobre sua suas também todo todos um uma à é será são foi deverá poderá não""".split()
)

# Lines the court system stamps on every page, plus pagination that is kept visible.
STAMP = re.compile(
    r"assinado eletronicamente|codigo de validacao|validado no sitio|projudi|"
    r"\bid\.?\s*\d{6,}|\bpag\.?\s*\d+\s*$",
    re.I,
)
PAGINATION = re.compile(r"^\s*p[aá]gina(\(s\))?\s*\d+(\s*(de|/)\s*\d+)?\s*$", re.I)

IDENTIFIERS = [
    ("cpf", re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}\s?-?\s?\d{2}\b")),
    ("cnpj", re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}\s?/?\s?\d{4}\s?-?\s?\d{2}\b")),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("cep", re.compile(r"\b\d{5}-\d{3}\b")),
    ("data", re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")),
    ("coordenada", re.compile(r"-?\b\d{1,3}[.,]\d{4,}\b")),
    ("ip", re.compile(r"\b\d{1,3}(\.\d{1,3}){3}\b")),
]
LONG_DIGITS = re.compile(r"\d[\d.\-/]{4,}\d")
# legal references ("Lei Complementar nº 105/2001", "Resolução 4.571") are not identifiers
LEGAL = re.compile(
    r"(lei|complementar|resolu\w*|circular|decreto|medida provis\w*|instru\w* normativa|portaria|art\w*)"
    r"\s*(n[º°o.]*\s*)?\d[\d.]*(/\d{2,4})?",
    re.I,
)


def plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def key(text: str) -> str:
    """Line identity across documents: accents, case, punctuation and spacing ignored."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", plain(text))).strip()


def _legal_spans(text):
    return [m.span() for m in LEGAL.finditer(text)]


def _long_digit_spans(text):
    legal = _legal_spans(text)
    return [m.span() for m in LONG_DIGITS.finditer(text)
            if sum(c.isdigit() for c in m.group()) >= 6
            and not any(a <= m.start() and m.end() <= b for a, b in legal)]


def digit_runs(text: str) -> list[str]:
    return [text[a:b] for a, b in _long_digit_spans(text)]


def identifier_hits(text: str) -> list[str]:
    hits = [name for name, pattern in IDENTIFIERS if pattern.search(text)]
    if digit_runs(text):
        hits.append("digitos")
    return hits


def is_stamp(text: str) -> bool:
    return bool(STAMP.search(plain(text)))


def is_pagination(text: str) -> bool:
    return bool(PAGINATION.match(text))


def prose(text: str) -> bool:
    """Contract prose, not a bare name/label/value: several words and stopwords."""
    words = re.findall(r"[^\W\d_]+", text.lower())
    stops = {w for w in words if w in STOPWORDS}
    return len(words) >= 5 and (len(stops) >= 3 or (len(stops) >= 2 and len(words) >= 8))


def restorable(text: str, n_expedientes: int) -> tuple[bool, str]:
    """Deterministic gate before the line review; returns (candidate, reason)."""
    if is_pagination(text):
        return True, "paginacion"
    if is_stamp(text):
        return False, "sello"
    if n_expedientes < MIN_EXPEDIENTES:
        return False, "linea_unica"
    if identifier_hits(text):
        return False, "identificador"
    if not prose(text):
        return False, "no_es_prosa"
    return True, "plantilla"


def review_candidate(text: str) -> bool:
    """Covered lines worth sending to the line review (everything else stays blank)."""
    return not is_stamp(text) and not is_pagination(text) and bool(key(text))


STRONG = ("cpf", "cnpj", "email", "ip", "coordenada")


def hidden_spans(text: str, n_expedientes: int) -> list[tuple[int, int]]:
    """Character spans of identifiers that must be hidden even on a visible line.

    Strong identifiers always; weaker ones (long digit runs, dates, CEP) unless the line is
    boilerplate repeated across expedientes (e.g. the bank's own phone numbers)."""
    spans = []
    legal = _legal_spans(text)
    for name, pattern in IDENTIFIERS:
        if name in STRONG or n_expedientes < MIN_EXPEDIENTES:
            spans += [m.span() for m in pattern.finditer(text)
                      if name in STRONG or not any(a <= m.start() and m.end() <= b for a, b in legal)]
    if n_expedientes < MIN_EXPEDIENTES:
        spans += _long_digit_spans(text)
    return spans


def decide_words(words, coverage, n_expedientes, verdict):
    """Per-word visibility for one line.

    words: [str]; coverage: [fraction of each word under the original masks];
    verdict: line review result for this line ("restaurar" or anything else).
    Returns (visible flags, reason)."""
    text = " ".join(words)
    if is_stamp(text):
        return [False] * len(words), "sello"
    if is_pagination(text):
        return [True] * len(words), "paginacion"
    covered = sum(coverage) / max(1, len(coverage)) >= 0.3
    if covered and verdict == "restaurar":
        visible, reason = [True] * len(words), "restaurada"
    elif covered:
        visible, reason = [c < 0.3 for c in coverage], "tapada"
    else:
        visible, reason = [True] * len(words), "visible"
    # identifiers inside the line, mapped from character spans to words
    spans = hidden_spans(text, n_expedientes)
    if spans:
        pos = 0
        for i, w in enumerate(words):
            start, end = pos, pos + len(w)
            pos = end + 1
            if any(s < end and e > start for s, e in spans):
                visible[i] = False
        reason += "+identificador"
    return visible, reason
