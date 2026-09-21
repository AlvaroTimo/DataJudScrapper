"""Source-hash-bound text/layout inventories, independent of old classifications."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from .common import read_json, source_path, workspace, write_json
from .text import (
    DEFAULT_TESSDATA,
    describe_attachments,
    normalize,
    page_inventory,
)


def load_inventory(root, source):
    import pymupdf

    root = Path(root)
    original = source_path(root, source)
    folder = workspace(root) / "inventory" / source["document_id"]
    cached = folder / "inventory.json"
    if cached.exists():
        meta = read_json(cached)
        if meta["source_sha256"] != source["sha256"]:
            raise ValueError("inventory source changed")
        page_file = folder / "pages.jsonl.gz"
    else:
        archive = Path(read_json(workspace(root) / "archive.json")["path"])
        legacy = archive / "contract-work" / source["document_id"]
        meta = read_json(legacy / "inventory.json") if (legacy / "inventory.json").exists() else {}
        compatible = (
            meta.get("source_sha256") == source["sha256"]
            and meta.get("inventory_version") == "2"
            and meta.get("text_engine_version") == "1"
            and meta.get("page_count") == source["page_count"]
            and meta.get("tessdata") == str(DEFAULT_TESSDATA)
        )
        if compatible:
            page_file = legacy / "pages.jsonl.gz"
        else:
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            page_file = folder / "pages.jsonl.gz"
            with pymupdf.open(original) as pdf:
                meta = {
                    "source_sha256": source["sha256"],
                    "page_count": len(pdf),
                    "attachments": describe_attachments(pdf),
                    "inventory_version": "2",
                    "text_engine_version": "1",
                }
                with gzip.open(page_file, "wt", encoding="utf8") as output:
                    page_file.chmod(0o600)
                    for page in pdf:
                        output.write(json.dumps(page_inventory(page, DEFAULT_TESSDATA)) + "\n")
            write_json(cached, meta)
    with gzip.open(page_file, "rt", encoding="utf8") as stream:
        pages = list(map(json.loads, stream))
    if [p["page_number"] for p in pages] != list(range(1, source["page_count"] + 1)):
        raise ValueError("incomplete page inventory")
    # Only neutral text/geometry is reused. Old hints, decisions and masks are ignored.
    return pages, meta["attachments"]


def words_normalized(page):
    width, height = page["width"], page["height"]
    result = []
    for word in page["words"]:
        x0, y0, x1, y1 = word[:4]
        if page.get("rotation") == 90:
            x0, y0, x1, y1 = width - y1, x0, width - y0, x1
        elif page.get("rotation") == 180:
            x0, y0, x1, y1 = width - x1, height - y1, width - x0, height - y0
        elif page.get("rotation") == 270:
            x0, y0, x1, y1 = y0, height - x1, y1, height - x0
        if x1 > x0 and y1 > y0:
            result.append(
                [
                    max(0, x0 / width),
                    max(0, y0 / height),
                    min(1, x1 / width),
                    min(1, y1 / height),
                    word[4],
                ]
            )
    return result


def lines_from_words(words):
    """Cluster by baseline, retaining separate cells through word coordinates."""
    rows = []
    for word in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        center = (word[1] + word[3]) / 2
        match = next(
            (
                r
                for r in reversed(rows[-5:])
                if abs(r["center"] - center) <= 0.40 * max(word[3] - word[1], r["height"])
            ),
            None,
        )
        if match is None:
            rows.append({"center": center, "height": word[3] - word[1], "words": [word]})
        else:
            match["words"].append(word)
    result = []
    for index, row in enumerate(sorted(rows, key=lambda r: r["center"])):
        tokens = sorted(row["words"], key=lambda w: w[0])
        text = " ".join(w[4] for w in tokens)
        result.append(
            {
                "id": index,
                "text": text,
                "normalized": normalize(text),
                "rect": [
                    min(w[0] for w in tokens),
                    min(w[1] for w in tokens),
                    max(w[2] for w in tokens),
                    max(w[3] for w in tokens),
                ],
                "words": tokens,
            }
        )
    return result


def body_text(page):
    return normalize(" ".join(w[4] for w in page["words"] if w[1] < page["height"] * 0.94))


def heading_text(page):
    return normalize(" ".join(w[4] for w in page["words"] if w[1] < page["height"] * 0.24))
