"""Neutral PDF attachment and OCR extraction; no catalog or review-state writes."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from pathlib import Path

DEFAULT_TESSDATA = Path.home() / ".local/share/datajud-scraper/tessdata/fast"


def normalize(text: str) -> str:
    for _ in range(2):
        if "Ã" not in text and "Â" not in text:
            break
        try:
            text = text.encode("latin1").decode("utf8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
    return " ".join(
        "".join(
            char
            for char in unicodedata.normalize("NFKD", text.casefold())
            if not unicodedata.combining(char)
        ).split()
    )


CONTRACT_PATTERNS = {
    "adhesion": r"\b(?:termo|proposta|solicitacao)\s+(?:de\s+)?adesao\b",
    "credit_note": r"\bcedula\s+de\s+credito\s+bancario\b|\bccb\b",
    "loan_agreement": r"\bcontrato\b.{0,100}\b(?:emprestimo|financiamento|credito)\b",
    "credit_instrument": r"\binstrumento\s+particular\b.{0,140}\b(?:credito|emprestimo)\b",
    "card_application": r"\b(?:solicitacao|proposta)\b.{0,80}\bcartao\b",
    "credit_terms": r"\b(?:condicoes\s+gerais|regulamento)\b.{0,160}"
    r"\b(?:credito|cartao|emprestimo|financiamento)\b",
    "credit_form": r"\b(?:emitente|mutuario|tomador)\b.{0,200}\b(?:cpf|credito)\b",
}


def contract_evidence(text: str, *, title: bool = False) -> list[str]:
    normalized = normalize(text)
    found = [name for name, pattern in CONTRACT_PATTERNS.items() if re.search(pattern, normalized)]
    if title and re.search(r"contrat|ades|cedula|\bccb\b|regulamento|condicoes", normalized):
        found.append("title_hint")
    return found


def describe_attachments(document) -> list[dict]:
    """Combine outline boundaries with index-link text; include uncovered prefixes."""
    import pymupdf

    outline = document.get_toc()
    starts: dict[int, dict] = {}
    for _, title, page_number in outline:
        if not 1 <= page_number <= document.page_count:
            continue
        item = starts.setdefault(page_number, {"ids": [], "outline_titles": []})
        item["outline_titles"].append(title)
        match = re.search(r"\bId\.\s*(\d+)", title)
        if match and match[1] not in item["ids"]:
            item["ids"].append(match[1])
    first_content = min(starts, default=min(document.page_count + 1, 21))
    titles: dict[int, list[str]] = defaultdict(list)
    for page_number in range(min(first_content - 1, 20)):
        page = document[page_number]
        words = page.get_text("words", sort=True)
        for link in page.get_links():
            target = link.get("page", -1) + 1
            if link["kind"] != pymupdf.LINK_GOTO or not 1 <= target <= document.page_count:
                continue
            rect = link["from"]
            if page.rect.width * 0.25 <= rect.x0 < page.rect.width * 0.75:
                text = " ".join(
                    word[4]
                    for word in words
                    if rect.x0 - 1 <= (word[0] + word[2]) / 2 <= rect.x1 + 1
                    and rect.y0 <= (word[1] + word[3]) / 2 <= rect.y1
                ).strip()
                if text and text not in titles[target]:
                    titles[target].append(text)
            starts.setdefault(target, {"ids": [], "outline_titles": []})
    has_index = bool(starts)
    first_content = min(starts, default=1)
    starts.setdefault(1, {"ids": [], "outline_titles": []})
    boundaries = sorted(starts)
    attachments = []
    for position, start in enumerate(boundaries, 1):
        end = boundaries[position] - 1 if position < len(boundaries) else document.page_count
        item = starts[start]
        title = " ".join(titles[start]) or " | ".join(item["outline_titles"])
        attachments.append(
            {
                "position": position,
                "start_page": start,
                "end_page": end,
                "source_attachment_id": ",".join(item["ids"]) or None,
                "title": title or ("Indice y portada" if start < first_content else "Sin indice"),
                "index_prefix": start == 1 and first_content > 1 and has_index,
                "boundary_ambiguity": len(item["ids"]) > 1,
                "title_evidence": contract_evidence(title, title=True),
                "page_evidence": {},
            }
        )
    return attachments


def text_quality(text, words, height):
    """Recognize corrupt nonempty text without treating numeric forms as corruption."""
    head = " ".join(w[4] for w in words if w[1] < height * 0.24)
    reasons = []
    if text.count("\ufffd") > max(10, len(text) * 0.05):
        reasons.append("replacement_characters")
    if sum(unicodedata.category(c) in ("Co", "Cs") for c in text) > max(5, len(text) * 0.02):
        reasons.append("unmapped_characters")
    if (
        len(head) > 100
        and sum(c.isalpha() for c in head) / len(head) < 0.15
        and len(re.findall(r"[^\w\s.,:/()%-]", head)) / len(head) > 0.25
    ):
        reasons.append("corrupt_heading")
    return reasons


def recognize_inventory(page, native, tessdata, *, dpi=200):
    """Add OCR to an already parsed native page, without repeating native extraction."""
    result = dict(native)
    try:
        textpage = page.get_textpage_ocr(
            language="por+eng", dpi=dpi, full=True, tessdata=str(tessdata)
        )
        text = textpage.extractText(sort=True)
        words = page.get_text("words", textpage=textpage, sort=True)
        height = (page.rect * page.derotation_matrix).height
        result.update(
            text=text,
            words=[list(w) for w in words],
            text_chars=len(text),
            text_method="ocr",
            ocr_error=None,
            ocr_dpi=dpi,
            native_text=native.get("native_text") or native["text"],
            text_quality=text_quality(text, words, height),
            evidence=contract_evidence(text),
            heading_evidence=contract_evidence(text[:1600]),
        )
    except RuntimeError as exc:
        result.update(text_method="ocr_failed", ocr_error=str(exc)[:250])
    return result


def page_inventory(
    page, tessdata: Path, *, recognize=True, force_ocr=False, dpi=200, native=None
) -> dict:
    import pymupdf

    if native is not None:
        return recognize_inventory(page, native, tessdata, dpi=dpi)

    # One MuPDF parse, rather than parsing twice and rebuilding sorted text in Python.
    # Detection uses positioned words; block-sorted text is only neutral inventory data.
    native_page = page.get_textpage(flags=pymupdf.TEXTFLAGS_WORDS)
    native_words = page.get_text("words", textpage=native_page, sort=True)
    native_text = native_page.extractText(sort=True)
    source_bounds = page.rect * page.derotation_matrix
    body = [word for word in native_words if word[1] < source_bounds.height * 0.94]
    body_chars = sum(len(word[4]) for word in body)
    images = page.get_image_info()
    image_rects = [pymupdf.Rect(image["bbox"]) & source_bounds for image in images]
    image_fraction = min(1.0, sum(rect.get_area() for rect in image_rects) / page.rect.get_area())
    unrecognized_image = False
    for rect in image_rects:
        if rect.get_area() < page.rect.get_area() * 0.05:
            continue
        overlapping_chars = sum(
            len(word[4]) for word in body if rect.contains(pymupdf.Rect(word[:4]))
        )
        if overlapping_chars < 80:
            unrecognized_image = True
    quality = text_quality(native_text, native_words, source_bounds.height)
    needs_ocr = (
        unrecognized_image
        or (image_fraction > 0.1 and body_chars < 300)
        or bool(quality)
        or (body_chars < 80 and len(page.get_drawings()) > 80)
    )
    result = {
        "page_number": page.number + 1,
        "width": page.rect.width,
        "height": page.rect.height,
        "rotation": page.rotation,
        "native_chars": len(native_text),
        "text_chars": len(native_text),
        "image_fraction": round(image_fraction, 4),
        "image_rects": [list(rect) for rect in image_rects],
        "text_method": "native",
        "needs_ocr": needs_ocr,
        "ocr_dpi": None,
        "text_quality": quality,
        "native_quality": quality,
        "ocr_error": None,
        "text": native_text,
        "native_text": native_text if needs_ocr or force_ocr else None,
        "words": [list(word) for word in native_words],
        "evidence": contract_evidence(native_text),
        "heading_evidence": contract_evidence(native_text[:1600]),
    }
    return (
        recognize_inventory(page, result, tessdata, dpi=dpi)
        if (recognize and (needs_ocr or force_ocr))
        else result
    )
