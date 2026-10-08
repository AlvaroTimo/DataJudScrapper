"""Content evidence and conservative resolution of contract boundaries."""

from __future__ import annotations

import re

from .common import digest
from .detection import ADHESION, CARD, family
from .inventory import body_text, heading_text, lines_from_words, words_normalized

SEPARATE_TITLE = re.compile(
    r"^(?:\d+[.)-]?\s+)?(?:cedula de credito|solicitacao.{0,45}saque|"
    r"termo de consentimento|consentimento esclarecido|proposta de adesao.{0,25}seguro|"
    r"proposta de seguro|regulamento (?:de|do)|condicoes gerais (?:de|do)|"
    r"relatorio.{0,35}(?:assinatura|biometria)|politica de privacidade|"
    r"contrato (?:de|do) (?:emprestimo|financiamento))"
)


def heading_lines(page):
    return [
        row["normalized"]
        for row in lines_from_words(words_normalized(page))
        if row["rect"][1] < 0.24 and len(row["normalized"]) >= 6
    ]


def independent_title(page):
    """Match a real caption, not a credit-note reference inside an adhesion clause."""
    for line in heading_lines(page)[:8]:
        if SEPARATE_TITLE.search(line):
            return True
        if ADHESION.search(line) and CARD.search(line):
            return False
    return False


def instrument_evidence(first):
    head, body = heading_text(first), body_text(first)
    if independent_title(first):
        return None
    explicit_card = bool(
        re.search(
            r"\bades[a4]o\s+(?:(?:ao|a|de|do|para)\s+){0,2}(?:cart[a4]o|cartoes|rmc|rcc)\b|"
            r"\bproposta\s+(?:de\s+)?emiss[a4]o\s+(?:(?:de|do)\s+)?cart[a4]o\b|"
            r"cart[a4]o.{0,35}termo\s+(?:de\s+)?ades[a4]o",
            head,
        )
    )
    if explicit_card and CARD.search(head):
        return "card_adhesion_heading"
    if re.search(
        r"abertura de conta|pacote de servicos|portabilidade de salario|"
        r"comunicacao de conta salario",
        head,
    ):
        return None
    if ADHESION.search(head) and CARD.search(head):
        return "card_adhesion_heading"
    if ADHESION.search(head) and CARD.search(body) and family(first) != "generic":
        return "issuer_adhesion_heading_and_card_body"
    applicant = bool(
        re.search(r"\bcpf\b|dados pessoais|nome do (?:cliente|titular)|identificacao do", body)
    )
    if (
        re.search(r"solicito.{0,35}emiss[a4]o|(?:adere|adiro).{0,25}regulamento", body)
        and CARD.search(body)
        and applicant
    ):
        return "card_request_and_applicant_fields"
    if (
        re.search(r"autorizacao para reserva.{0,35}margem", head)
        and re.search(r"ades[a4]o ao cart[a4]o", head)
        and applicant
        and re.search(r"limite|bandeira", body)
    ):
        return "personalized_card_margin_adhesion"
    return None


def printed_counter(page):
    bottom = " ".join(w[4] for w in words_normalized(page) if 0.82 < w[1] < 0.956)
    values = re.findall(r"\b(\d{1,2})\s*(?:/|de)\s*(\d{1,2})\b", bottom)
    # Some genuine integral appendices say 5/4 and 6/4. Preserve the observation.
    return next(
        ((int(a), int(b)) for a, b in reversed(values) if 0 < int(a) <= 60 and 0 < int(b) <= 60),
        None,
    )


def continuation_evidence(previous, current):
    if independent_title(current):
        return None
    before, after = printed_counter(previous), printed_counter(current)
    if before and after and after == (before[0] + 1, before[1]):
        return "consecutive_printed_pages"
    head, body = heading_text(current), body_text(current)
    if re.search(r"anexo.{0,60}beneficios|beneficios.{0,50}cartao", head) and CARD.search(body):
        return "integral_card_benefits"
    return None


def repair_numbered_continuations(pages, roles):
    """Repair supported continuations; a counter alone cannot convert another document."""
    covered = set()
    for number in sorted(roles):
        if number in covered or roles[number] not in ("A", "B"):
            continue
        counter = printed_counter(pages[number - 1])
        if not counter or counter[0] != 1 or counter[1] == 1:
            continue
        group = [number]
        for n in range(number + 1, len(pages) + 1):
            if n not in roles or roles[n] in ("A", "B", "F", "U"):
                break
            current = pages[n - 1]
            evidence = continuation_evidence(pages[n - 2], current)
            if not evidence:
                break
            semantic = (
                roles[n] in ("C", "E")
                or (ADHESION.search(heading_text(current)) and CARD.search(body_text(current)))
                or evidence == "integral_card_benefits"
            )
            if not semantic:
                break
            group.append(n)
        end = group[-1]
        closing = bool(re.search(r"ouvidoria|central de atendimento", body_text(pages[end - 1])))
        last_counter = printed_counter(pages[end - 1])
        complete = (
            roles[end] == "E"
            or closing
            or (last_counter == (counter[1], counter[1]) and counter[1] == len(group))
        )
        if complete and len(group) > 1:
            for n in group:
                roles[n] = "A" if n == number else ("E" if n == end else "C")
            covered.update(group)


def resolve_roles(pages, raw_roles, *, attachments=()):
    roles = dict(raw_roles)
    changes = []
    for number in roles:
        if independent_title(pages[number - 1]):
            if roles[number] != "N":
                changes.append(
                    {
                        "page": number,
                        "before": roles[number],
                        "after": "N",
                        "reason": "independent_document_caption",
                    }
                )
            roles[number] = "N"
    before = dict(roles)
    if attachments:
        for a in attachments:
            local = {n: r for n, r in roles.items() if a["start_page"] <= n <= a["end_page"]}
            repair_numbered_continuations(pages, local)
            roles.update(local)
    else:
        repair_numbered_continuations(pages, roles)
    changes.extend(
        {"page": n, "before": before[n], "after": r, "reason": "supported_numbered_continuation"}
        for n, r in roles.items()
        if r != before[n]
    )
    return roles, changes


def assemble_roles(pages, roles, *, deduplicate=True, attachments=(), allowed_crossings=()):
    instruments, unresolved, fragments = [], [], []
    active = []
    starts = {a["start_page"] for a in attachments}
    allowed = set(allowed_crossings)

    def close(complete):
        nonlocal active
        if active:
            if complete:
                fingerprint = digest([body_text(pages[n - 1]) for n in active])
                old = next(
                    (i for i in instruments if deduplicate and i["fingerprint"] == fingerprint),
                    None,
                )
                if old:
                    old["occurrences"].append(active)
                else:
                    instruments.append(
                        {
                            "pages": active,
                            "occurrences": [active],
                            "family": family(pages[active[0] - 1]),
                            "fingerprint": fingerprint,
                            "boundary_rule": "visual_structure_and_sequence",
                        }
                    )
            else:
                unresolved.append({"pages": active, "reason": "incomplete_instrument"})
        active = []

    previous = None
    for number, role in sorted(roles.items()):
        if previous is not None and number != previous + 1:
            close(False)
        if previous is not None and number in starts and number not in allowed:
            close(False)
        if role in ("A", "B"):
            close(False)
            active = [number]
            if role == "B":
                close(True)
        elif role in ("C", "E"):
            if active:
                active.append(number)
                if role == "E":
                    close(True)
            else:
                unresolved.append({"pages": [number], "reason": "orphan_continuation"})
        else:
            close(False)
            if role == "U":
                unresolved.append({"pages": [number], "reason": "visual_uncertainty"})
            elif role == "F":
                fragments.append(number)
        previous = number
    close(False)
    return {
        "instruments": instruments,
        "unresolved": unresolved,
        "fragments": fragments,
        "page_roles": roles,
    }
