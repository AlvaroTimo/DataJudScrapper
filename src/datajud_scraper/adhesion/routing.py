"""Index-first candidate planning and exhaustive residual lexical fallback."""

from __future__ import annotations

import re

from .boundaries import instrument_evidence
from .detection import ADHESION, CARD
from .inventory import body_text
from .text import normalize


def normalize_title(title):
    title = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", title)
    return re.sub(r"[^a-z0-9]+", " ", normalize(title)).strip()


def title_signals(title):
    value = normalize_title(title)
    score, reasons = 0, []
    strong = bool(re.search(r"\badesao\b|termo.{0,25}ades|proposta.{0,25}emiss", value))
    for name, weight, found in (
        ("explicit_adhesion", 8, strong),
        ("contract", 5, re.search(r"\bcontratos?\b|\bcontrato(?=cart|\d)", value)),
        ("application", 4, re.search(r"\bproposta|\bsolicitac|\bemiss", value)),
        ("term", 2, re.search(r"\btermos?\b", value)),
        ("card", 2, re.search(r"\bcart|\brmc\b|\brcc\b", value)),
        ("alias", 5, re.search(r"\b(?:tad|ctt|ctr|tacc)\b", value)),
        ("card_copy", 3, re.search(r"segunda via.{0,20}cart", value)),
        ("opaque_alias", 3, re.search(r"\bbluebird\b", value)),
    ):
        if found:
            score += weight
            reasons.append(name)
    if score and re.search(
        r"\bbmg\b|\bpan\b|daycoval|cencosud|bradescard|banco do brasil|"
        r"bradesco|santander|mercantil|banco master|credcesta|credicesta|agibank",
        value,
    ):
        score += 1
        reasons.append("issuer_context")
    if re.search(
        r"contrato social|estatuto|procurac|honorar|preposic|audien|alteracao contratual", value
    ):
        score -= 12
        reasons.append("corporate_or_judicial")
    if not strong:
        if re.search(
            r"cessao|consentimento|seguro|privacidade|termos.{0,15}uso|"
            r"condicoes.{0,15}uso|jornada|laudo|cobranca|regulamento|"
            r"conta corrente|abertura de conta|nuconta|cartilha",
            value,
        ):
            score -= 8
            reasons.append("independent_or_noncard_document")
        if "biometria" in value and "contract" not in reasons:
            score -= 8
            reasons.append("biometric_only")
    return {"score": score, "reasons": reasons}


def content_attachments(pages, attachments):
    """Use a whole-document fallback when index ranges are missing or inconsistent."""
    expected = 1
    for a in sorted(attachments, key=lambda a: a.get("start_page", 0)):
        start, end = a.get("start_page"), a.get("end_page")
        if type(start) is not int or type(end) is not int or start != expected or end < start:
            break
        expected = end + 1
    else:
        if expected == len(pages) + 1 and attachments:
            if len(attachments) == 1 and attachments[0].get("title") == "Sin indice":
                return [attachments[0]], False
            return [a for a in attachments if not a.get("index_prefix")], True
    return [
        {"start_page": 1, "end_page": len(pages), "title": "", "boundary_ambiguity": True}
    ], False


def plan_candidates(pages, attachments):
    candidates, indexed = content_attachments(pages, attachments)
    groups, evidence, probes = [], [], []
    if hasattr(pages, "start_prefetch"):
        firsts = [
            n for a in candidates
            for n in range(a["start_page"], min(a["end_page"], a["start_page"] + 1) + 1)
        ]
        rest = [n for a in candidates for n in range(a["start_page"], a["end_page"] + 1)]
        pages.start_prefetch([*firsts, *rest])
    if hasattr(pages, "prefetch"):
        pages.prefetch([
            n for a in candidates
            for n in range(a["start_page"], min(a["end_page"], a["start_page"] + 1) + 1)
        ])
    for a in candidates:
        signals = title_signals(a.get("title", ""))
        firsts = list(range(a["start_page"], min(a["end_page"], a["start_page"] + 1) + 1))
        probes.extend(firsts)
        probe_evidence = [instrument_evidence(pages[n - 1]) for n in firsts]
        hit = any(probe_evidence)
        selected = indexed and (signals["score"] >= 4 or hit)
        reasons = signals["reasons"] + (["actual_form_probe"] if hit else [])
        record = {
            "start_page": a["start_page"],
            "end_page": a["end_page"],
            "score": signals["score"],
            "reasons": reasons,
            "selected": selected,
            "boundary_ambiguity": a.get("boundary_ambiguity", False),
        }
        evidence.append(record)
        if selected:
            groups.append(a)
    scores = {r["start_page"]: r["score"] for r in evidence}
    groups.sort(key=lambda a: (-scores[a["start_page"]], a["start_page"]))
    return {
        "groups": groups,
        "attachments": candidates,
        "fallback_attachments": attachments if indexed else candidates,
        "index_valid": indexed,
        "evidence": evidence,
        "probe_pages": sorted(set(probes)),
    }


def candidate_numbers(pages, attachments, *, classified=()):
    """Preserve the old high-recall search over residual content, including opaque titles."""
    candidates, _ = content_attachments(pages, attachments)
    classified = set(classified)
    selected = set()
    if hasattr(pages, "prefetch"):
        pages.prefetch([
            n for a in candidates
            for n in range(a["start_page"], a["end_page"] + 1) if n not in classified
        ])
    for a in candidates:
        numbers = range(a["start_page"], a["end_page"] + 1)
        if all(n in classified for n in numbers):
            continue
        strong = False
        uncertain = []
        for number in numbers:
            page = pages[number - 1]
            text = body_text(page)
            strong |= bool(ADHESION.search(text) and CARD.search(text))
            if (
                page.get("ocr_error")
                or page.get("text_quality")
                or (page.get("image_fraction", 0) > 0.5 and len(text) < 100)
            ):
                uncertain.append(number)
        title_hint = re.search(r"ades|contrat|proposta|termo", a.get("title", "").lower())
        if strong or title_hint:
            selected.update(numbers)
        else:
            for number in uncertain:
                selected.update(
                    range(max(a["start_page"], number - 1), min(a["end_page"], number + 1) + 1)
                )
    return sorted(selected - classified)
