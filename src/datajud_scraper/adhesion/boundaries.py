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

CLAUSE_HEADING = re.compile(
    r"^(?:\d+[.)-]?\s+)?(?:clausulas|condicoes (?:gerais|especificas|do cartao|de uso))"
)


def heading_lines(page):
    return [row["normalized"] for row in heading_rows(page)]


def heading_rows(page):
    return [
        row
        for row in lines_from_words(words_normalized(page))
        if row["rect"][1] < 0.24 and len(row["normalized"]) >= 6
    ]


def wrapped_clause_line(rows, index):
    """A line inside a justified paragraph, not a caption that starts a new document."""
    if index == 0 or index + 1 >= len(rows):
        return False
    previous, row, following = rows[index - 1], rows[index], rows[index + 1]
    height = row["rect"][3] - row["rect"][1]
    if height <= 0:
        return False
    similar = all(
        abs((r["rect"][3] - r["rect"][1]) - height) <= 0.15 * height for r in (previous, following)
    )
    return (
        similar
        and row["rect"][1] - previous["rect"][3] < 1.2 * height
        and following["rect"][1] - row["rect"][3] < 0.8 * height
        and row["rect"][2] - row["rect"][0] > 0.6
        and previous["rect"][2] - previous["rect"][0] > 0.6
        and not previous["normalized"].rstrip().endswith((".", ":"))
    )


def independent_title(page, *, wrapped_check=False):
    """Match a real caption, not a credit-note reference inside an adhesion clause.

    wrapped_check ignores a matching line that sits inside a paragraph (a clause wrapped
    right before "Regulamento de ..."); it is used only where a page is forced to N.
    """
    rows = heading_rows(page)[:8]
    for index, row in enumerate(rows):
        line = row["normalized"]
        if SEPARATE_TITLE.search(line):
            if wrapped_check and wrapped_clause_line(rows, index):
                continue
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


def counter_runs(pages, roles, attachments=()):
    """Force A/C/E on complete printed runs 1/n..n/n that start with adhesion evidence.

    Returns the changes and the pages 1..n-1 that later visual rechecks must not undo.
    The last page is never locked and is not forced to E when integral benefits follow.
    """
    changes, locked = [], set()
    for start in sorted(roles):
        if start in locked:
            continue
        counter = printed_counter(pages[start - 1])
        if not counter or counter[0] != 1 or counter[1] < 2:
            continue
        run = list(range(start, start + counter[1]))
        if run[-1] > len(pages) or any(n not in roles for n in run):
            continue
        if attachments and not any(
            a["start_page"] <= start and run[-1] <= a["end_page"] for a in attachments
        ):
            continue
        printed = [printed_counter(pages[n - 1]) for n in run]
        # A two-sheet term often prints "1 de 2" only on its front: accept a back sheet
        # without counter when it is a full page of text and not another start.
        front_only = (
            counter[1] == 2
            and printed[1] is None
            and instrument_evidence(pages[run[1] - 1]) is None
            and len(body_text(pages[run[1] - 1]).split()) >= 150
        )
        if not front_only and any(c != (k + 1, counter[1]) for k, c in enumerate(printed)):
            continue
        if instrument_evidence(pages[start - 1]) is None or any(roles[n] == "F" for n in run):
            continue
        if any(independent_title(pages[n - 1], wrapped_check=True) for n in run[1:]):
            continue
        last = run[-1]
        extends = last < len(pages) and continuation_evidence(pages[last - 1], pages[last])
        target = dict.fromkeys(run, "C")
        target[start] = "A"
        if not extends:
            target[last] = "E"
        for n, role in target.items():
            if roles[n] != role:
                changes.append(
                    {
                        "page": n,
                        "before": roles[n],
                        "after": role,
                        "reason": "complete_printed_counter_run",
                    }
                )
                roles[n] = role
        # Without a counter on the back sheet only the lock keeps the recheck from
        # turning that closing page into a continuation.
        locked.update(run if front_only and not extends else run[:-1])
    return changes, locked


def cover_page(page):
    """A separator sheet (DOC. 01, a dossier barcode) carries almost no body text."""
    return (
        len(body_text(page).split()) < 60
        and instrument_evidence(page) is None
        and printed_counter(page) is None
    )


def repair_starts(pages, roles, attachments=(), locked=()):
    """Undo two visual mistakes about where a term starts, after the boundary recheck.

    A cover sheet labelled A hides the real first page from the scope confirmation, and a
    clause sheet right after a start is a continuation, not a new term.
    """

    def same_attachment(first, last):
        return not attachments or any(
            a["start_page"] <= first and last <= a["end_page"] for a in attachments
        )

    changes = []

    def change(number, role, reason):
        changes.append({"page": number, "before": roles[number], "after": role, "reason": reason})
        roles[number] = role

    def starts_term(page):
        counter = printed_counter(page)
        return instrument_evidence(page) is not None or bool(counter and counter[0] == 1)

    for number in sorted(roles):
        role, following = roles[number], roles.get(number + 1)
        if (
            role == "A"
            and following in ("C", "E")
            and same_attachment(number, number + 1)
            and cover_page(pages[number - 1])
            and starts_term(pages[number])
        ):
            change(number, "N", "cover_sheet_before_term")
            change(number + 1, "A" if following == "C" else "B", "cover_sheet_before_term")
        elif (
            role == "A"
            and number not in locked
            and roles.get(number - 1) in ("A", "C")
            and same_attachment(number - 1, number)
            and instrument_evidence(pages[number - 1]) is None
            and not independent_title(pages[number - 1])
            and any(CLAUSE_HEADING.search(line) for line in heading_lines(pages[number - 1])[:3])
        ):
            change(number, "C", "clause_sheet_continues_term")
    return changes


def resolve_roles(pages, raw_roles, *, attachments=(), locked=None):
    roles = dict(raw_roles)
    changes = []
    for number in roles:
        if independent_title(pages[number - 1], wrapped_check=True):
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
    counted, run_pages = counter_runs(pages, roles, attachments)
    changes.extend(counted)
    if locked is not None:
        locked.update(run_pages)
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


def foreign_continuation(pages, roles, number, excluded, raw_roles=None):
    """A C/E page continuing a contiguous page already judged another document (N/F).

    Kept as a doubt whenever anything hints at a missed adhesion start: evidence on either
    page, a first page relabelled by a rule, weak text, or a counter restarting at 1.
    """
    previous = number - 1
    if previous not in roles or not (roles[previous] in ("N", "F") or previous in excluded):
        return False
    before, current = pages[previous - 1], pages[number - 1]
    for page in (before, current):
        if instrument_evidence(page) or (
            ADHESION.search(heading_text(page)) and CARD.search(heading_text(page))
        ):
            return False
    if (
        raw_roles
        and raw_roles.get(previous) in ("A", "B")
        and roles[previous] != raw_roles[previous]
    ):
        return False
    if len(body_text(before)) < 80 or len(body_text(current)) < 80:
        return False
    counter = printed_counter(current)
    return not (counter and counter[0] == 1)


def assemble_roles(
    pages, roles, *, deduplicate=True, attachments=(), allowed_crossings=(), raw_roles=None
):
    instruments, unresolved, fragments, excluded = [], [], [], []
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
            elif number not in starts and foreign_continuation(
                pages, roles, number, set(excluded), raw_roles
            ):
                excluded.append(number)
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
        # Audit trail: continuations of documents already judged out of scope. They are
        # reported separately and never counted as resolved adhesions.
        "excluded_continuations": excluded,
        "page_roles": roles,
    }
