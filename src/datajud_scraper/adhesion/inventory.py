"""Source-hash-bound text/layout inventories, independent of old classifications."""

from __future__ import annotations

import gzip
import json
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path

from ..pdf_validation import hash_file
from .common import digest, read_json, source_path, workspace, write_json
from .text import (
    DEFAULT_TESSDATA,
    describe_attachments,
    normalize,
    page_inventory,
    text_quality,
)


def extraction_configuration():
    import pymupdf

    return {
        "inventory_version": "3",
        "text_engine_version": "3",
        "pymupdf": pymupdf.VersionBind,
        "dpi": 200,
        "retry_dpi": 300,
        "tessdata": str(DEFAULT_TESSDATA),
        "models": {
            name: hash_file(DEFAULT_TESSDATA / name)
            for name in ("por.traineddata", "eng.traineddata")
            if (DEFAULT_TESSDATA / name).is_file()
        },
    }


class PageInventory(Sequence):
    """Lazy, resumable neutral page data; iteration intentionally recognizes all pages."""

    def __init__(self, root, source, pdf):
        if len(pdf) != source["page_count"]:
            raise ValueError("inventory page count changed")
        self.pdf, self.source = pdf, source
        self.attachments = describe_attachments(pdf)
        self.profile = extraction_configuration()
        self.signature = digest(self.profile)
        self.folder = (
            workspace(root)
            / "inventory-v3"
            / source["document_id"]
            / source["sha256"]
            / self.signature
        )
        self._native, self._recognized, self._retried = {}, {}, {}
        self.stats = {"native_reads": 0, "ocr_pages": 0, "ocr_retries": 0, "cache_hits": 0}
        legacy_ocr_profile = {k: self.profile[k] for k in ("dpi", "pymupdf", "models")}
        self._legacy_path, self._legacy = None, None
        pointer = workspace(root) / "archive.json"
        if not pointer.exists():
            pointer = Path(root) / "adhesion-v1/archive.json"
        locations = [workspace(root) / "inventory" / source["document_id"]]
        if pointer.exists():
            locations.append(
                Path(read_json(pointer)["path"]) / "contract-work" / source["document_id"]
            )
        for folder in locations:
            meta_file = folder / "inventory.json"
            if not meta_file.exists():
                continue
            meta = read_json(meta_file)
            if (
                meta.get("source_sha256") == source["sha256"]
                and meta.get("page_count") == len(pdf)
                and meta.get("inventory_version") == "2"
                and meta.get("text_engine_version") == "1"
                and meta.get("tessdata") == str(DEFAULT_TESSDATA)
                and meta.get("ocr_configuration") == legacy_ocr_profile
                and (folder / "pages.jsonl.gz").exists()
            ):
                self._legacy_path = folder / "pages.jsonl.gz"
                break

    def __len__(self):
        return self.source["page_count"]

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[n] for n in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        return self.get(index + 1)

    def _read(self, number, mode):
        if type(number) is not int or not 1 <= number <= len(self):
            raise IndexError(number)
        path = self.folder / f"{number:05d}-{mode}.json"
        if not path.exists():
            return None
        value = read_json(path)
        if (
            value.get("source_sha256") != self.source["sha256"]
            or value.get("configuration_sha256") != self.signature
            or value["page"]["page_number"] != number
        ):
            raise ValueError("page cache belongs to another source/configuration")
        self.stats["cache_hits"] += 1
        return value["page"]

    def _write(self, number, mode, page):
        write_json(
            self.folder / f"{number:05d}-{mode}.json",
            {
                "source_sha256": self.source["sha256"],
                "configuration_sha256": self.signature,
                "page": page,
            },
        )
        return page

    def get_native(self, number):
        if number not in self._native:
            page = self._read(number, "native")
            if page is None:
                page = page_inventory(self.pdf[number - 1], DEFAULT_TESSDATA, recognize=False)
                self.stats["native_reads"] += 1
                self._write(number, "native", page)
            self._native[number] = page
        return self._native[number]

    def _legacy_page(self, number):
        if self._legacy_path is None:
            return None
        if self._legacy is None:
            with gzip.open(self._legacy_path, "rt", encoding="utf8") as stream:
                rows = list(map(json.loads, stream))
            if [p["page_number"] for p in rows] != list(range(1, len(self) + 1)):
                raise ValueError("incomplete legacy page inventory")
            self._legacy = rows
        old = self._legacy[number - 1]
        if old.get("text_method") != "ocr" or old.get("ocr_error"):
            return None
        # Import only neutral text/geometry. No legacy labels, masks or candidate hints.
        native = self.get_native(number)
        result = {
            **native,
            **{
                k: old[k]
                for k in ("text", "words", "text_chars", "text_method", "ocr_error")
                if k in old
            },
            "ocr_dpi": 200,
        }
        result["text_quality"] = text_quality(result["text"], result["words"], result["height"])
        return result

    def get(self, number):
        if number not in self._recognized:
            page = self._read(number, "recognized")
            if page is None:
                native = self.get_native(number)
                page = native
                if native["needs_ocr"]:
                    page = self._legacy_page(number)
                    if page is None or native["native_quality"] or page.get("text_quality"):
                        page = page_inventory(self.pdf[number - 1], DEFAULT_TESSDATA)
                        self.stats["ocr_pages"] += 1
                self._write(number, "recognized", page)
            self._recognized[number] = page
        return self._recognized[number]

    def retry(self, number):
        """Return an alternative, keeping good native/OCR data intact if retry worsens it."""
        if number not in self._retried:
            page = self._read(number, "retry")
            if page is None:
                page = page_inventory(
                    self.pdf[number - 1], DEFAULT_TESSDATA, force_ocr=True, dpi=300
                )
                self.stats["ocr_retries"] += 1
                self._write(number, "retry", page)
            self._retried[number] = page
        return self._retried[number]


@contextmanager
def open_inventory(root, source, *, pdf=None):
    import pymupdf

    original = source_path(root, source)
    if pdf is not None:
        yield PageInventory(root, source, pdf)
    else:
        with pymupdf.open(original) as document:
            yield PageInventory(root, source, document)


def load_inventory(root, source):
    """Compatibility API for independent reviews that deliberately visit every page."""
    with open_inventory(root, source) as inventory:
        return list(inventory), inventory.attachments


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
    return normalize(" ".join(w[4] for w in words_normalized(page) if w[1] < 0.94))


def heading_text(page):
    return normalize(" ".join(w[4] for w in words_normalized(page) if w[1] < 0.24))
