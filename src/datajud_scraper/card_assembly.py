"""Assemble contractual instruments without conflating separate reproduced fragments."""

from __future__ import annotations

import re


def printed_page_counter(page):
    """Read a printed document counter near the footer, excluding dates and case IDs."""
    words = [
        w
        for w in page.get("words", [])
        if w[0] >= page["width"] * 0.6 and page["height"] * 0.8 <= w[1] < page["height"] * 0.97
    ]
    found = []
    for word in words:
        match = re.fullmatch(r"(\d{1,3})/(\d{1,3})", word[4].strip())
        if match:
            current, total = map(int, match.groups())
            if 1 <= current <= total <= 150:
                found.append((word[1], current, total))
    if not found:
        return None
    _, current, total = max(found)
    return current, total


def assemble_occurrences(predictions, pages):
    """Keep whole documents by sequence; embedded fragments have explicit local groups.

    Different fragments on separate physical pages are retained as separate fragment
    occurrences. They are not asserted to form one complete agreement. Within a page,
    the visual model groups multiple pieces of the same reproduced instrument.
    """
    lookup = {p["page_number"]: p for p in pages}
    groups = []
    for number in sorted(predictions):
        prediction = predictions[number]
        if not prediction["state"].startswith("card_") or not prediction.get("regions"):
            continue
        attachment = lookup[number]["attachment_position"]
        if prediction["state"] == "card_excerpt":
            local = {}
            for region in prediction["regions"]:
                local.setdefault(region.get("document", 1), []).append(region)
            for regions in local.values():
                groups.append(
                    {
                        "kind": "card_excerpt",
                        "attachment": attachment,
                        "regions": regions,
                        "last_page": number,
                        "counter": None,
                    }
                )
            continue
        counter = printed_page_counter(lookup[number])
        prior = groups[-1] if groups else None
        adjacent = (
            prior is not None
            and number == prior["last_page"] + 1
            and attachment == prior["attachment"]
            and prior["kind"] != "card_excerpt"
        )
        continued_numbering = (
            adjacent
            and counter
            and prior["counter"]
            and counter == (prior["counter"][0] + 1, prior["counter"][1])
        )
        new = (
            not adjacent
            or not prediction.get("integral_annex")
            and counter
            and counter[0] == 1
            or not continued_numbering
            and prediction.get("start", False)
        )
        if new:
            groups.append(
                {
                    "kind": prediction["kind"],
                    "attachment": attachment,
                    "regions": [],
                    "last_page": number,
                    "counter": counter,
                }
            )
        groups[-1]["regions"].extend(prediction["regions"])
        groups[-1]["last_page"] = number
        groups[-1]["counter"] = counter
    return [{"kind": g["kind"], "regions": g["regions"]} for g in groups]


def excerpt_image_candidates(image_boxes):
    """Actual image objects, without near-identical border/shadow overlays."""

    def area(b):
        return (b[2] - b[0]) * (b[3] - b[1])

    candidates = [
        list(b)
        for b in dict.fromkeys(tuple(b) for b in image_boxes)
        if 0 <= b[0] < b[2] <= 1 and 0 <= b[1] < b[3] <= 1 and 0.002 < area(b) < 0.6
    ]
    return [
        b
        for b in candidates
        if not any(
            b != c
            and b[0] <= c[0]
            and b[1] <= c[1]
            and b[2] >= c[2]
            and b[3] >= c[3]
            and area(c) > 0.6 * area(b)
            for c in candidates
        )
    ]


def ground_excerpt_regions(prediction, image_boxes):
    """Snap reproduced excerpts to actual embedded image extents when available.

    A model can locate several pieces with one loose rectangle. PDF image objects
    provide exact bounds, separating those pieces from captions and surrounding prose.
    """
    if prediction["state"] != "card_excerpt":
        return prediction

    candidates = excerpt_image_candidates(image_boxes)
    regions = []
    for region in prediction.get("regions", []):
        r = region["rect"]
        found = []
        for b in candidates:
            overlap = max(0, min(b[2], r[2]) - max(b[0], r[0])) * max(
                0, min(b[3], r[3]) - max(b[1], r[1])
            )
            if overlap > 0.55 * (b[2] - b[0]) * (b[3] - b[1]):
                found.append(b)
        if found:
            regions.extend({**region, "rect": b, "boundary_basis": "pdf_image"} for b in found)
        else:
            regions.append(region)
    unique = {(tuple(r["rect"]), r.get("document", 1)): r for r in regions}
    return {
        **prediction,
        "regions": sorted(unique.values(), key=lambda r: (r["rect"][1], r["rect"][0])),
    }
