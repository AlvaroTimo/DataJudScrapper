"""Source-hash-bound text/layout inventories, independent of old classifications."""

from __future__ import annotations

import gzip
import json
import multiprocessing
import os
import signal
import time
from collections import OrderedDict
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path

from ..pdf_validation import hash_file
from .common import digest, read_json, source_path, workspace, write_json
from .resources import runtime_options, worker_limit
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
        "text_engine_version": "4",
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


_WORKER = None


def _initialize_worker(path, folder, source_hash, signature, tessdata):
    import pymupdf

    global _WORKER
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    os.environ["OMP_THREAD_LIMIT"] = "1"
    _WORKER = (pymupdf.open(path), Path(folder), source_hash, signature, Path(tessdata))


def _recognize_worker(number):
    pdf, folder, source_hash, signature, tessdata = _WORKER
    stats = {"native_reads": 0, "ocr_pages": 0, "cache_hits": 0}

    def load(mode):
        path = folder / f"{number:05d}-{mode}.json"
        if not path.exists():
            return None
        value = read_json(path)
        if (
            value.get("source_sha256") != source_hash
            or value.get("configuration_sha256") != signature
            or value["page"]["page_number"] != number
        ):
            raise ValueError("page cache belongs to another source/configuration")
        stats["cache_hits"] += 1
        return value["page"]

    def save(mode, page):
        write_json(
            folder / f"{number:05d}-{mode}.json",
            {
                "source_sha256": source_hash,
                "configuration_sha256": signature,
                "page": page,
            },
            compact=True,
            durable=False,
        )

    if load("recognized") is None:
        native = load("native")
        if native is None:
            native = page_inventory(pdf[number - 1], tessdata, recognize=False)
            stats["native_reads"] += 1
            save("native", native)
        recognized = native
        if native["needs_ocr"]:
            recognized = page_inventory(pdf[number - 1], tessdata, native=native)
            stats["ocr_pages"] += 1
        save("recognized", recognized)
    # Return counters only. Large OCR dictionaries stay on disk, out of IPC queues.
    return number, stats


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
        self._native, self._recognized, self._retried = OrderedDict(), OrderedDict(), OrderedDict()
        self.memory_pages = 64
        self.stats = {"native_reads": 0, "ocr_pages": 0, "ocr_retries": 0, "cache_hits": 0}
        options = runtime_options()
        self.workers = worker_limit(
            options.get("workers", 0),
            options.get("memory_mb", 2048),
            Path(pdf.name).stat().st_size if pdf.name else 0,
        )
        self.progress = options.get("progress")
        self._pool = None
        self._pool_workers = 0
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

    def close(self, *, interrupted=False):
        if self._pool is not None:
            pool, self._pool = self._pool, None
            if interrupted:
                pool.terminate()
            else:
                pool.close()
            pool.join()

    def prefetch(self, numbers):
        """Visit all requested pages, with isolated MuPDF processes and bounded RAM."""
        numbers = sorted(set(numbers))
        if any(type(n) is not int or not 1 <= n <= len(self) for n in numbers):
            raise IndexError("invalid prefetch pages")
        pending = [n for n in numbers if n not in self._recognized]
        # Legacy migration deliberately follows the same verified sequential path.
        if self.workers == 1 or len(pending) < 4 or self._legacy_path or not self.pdf.name:
            for n in pending:
                self.get(n)
            return
        # Avoid starting processes on a cache hit (common when resuming a batch).
        jobs = []
        for number in pending:
            cached = self._read(number, "recognized")
            if cached is None:
                jobs.append(number)
            else:
                self._store(self._recognized, number, self._remember(cached))
        if not jobs:
            return
        if self._pool is None:
            self._pool_workers = min(self.workers, len(jobs))
            self._pool = multiprocessing.get_context("spawn").Pool(
                self._pool_workers,
                initializer=_initialize_worker,
                initargs=(
                    self.pdf.name,
                    str(self.folder),
                    self.source["sha256"],
                    self.signature,
                    self.profile["tessdata"],
                ),
            )
        started = time.monotonic()
        last_update = started
        try:
            for completed, (number, stats) in enumerate(
                self._pool.imap_unordered(_recognize_worker, jobs), 1
            ):
                for key, value in stats.items():
                    self.stats[key] += value
                self._store(
                    self._recognized, number, self._remember(self._read(number, "recognized"))
                )
                self.stats["cache_hits"] -= 1  # The worker just generated this page.
                if self.progress and (
                    time.monotonic() - last_update >= 2 or completed == len(jobs)
                ):
                    self.progress(
                        {
                            "event": "inventory_progress",
                            "completed": completed,
                            "page_total": len(jobs),
                            "workers": self._pool_workers,
                            "elapsed_seconds": round(time.monotonic() - started, 3),
                        }
                    )
                    last_update = time.monotonic()
        except BaseException:
            self.close(interrupted=True)
            raise

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
            compact=True,
            durable=False,  # Regenerable cache; atomic replacement still prevents partial reads.
        )
        return page

    def get_native(self, number):
        if number not in self._native:
            page = self._read(number, "native")
            if page is None:
                page = page_inventory(self.pdf[number - 1], DEFAULT_TESSDATA, recognize=False)
                self.stats["native_reads"] += 1
                self._write(number, "native", page)
            self._store(self._native, number, page)
        self._native.move_to_end(number)
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
                        page = page_inventory(self.pdf[number - 1], DEFAULT_TESSDATA, native=native)
                        self.stats["ocr_pages"] += 1
                self._write(number, "recognized", page)
            self._store(self._recognized, number, self._remember(page))
        self._recognized.move_to_end(number)
        return self._recognized[number]

    def _store(self, cache, number, page):
        cache[number] = page
        cache.move_to_end(number)
        while len(cache) > self.memory_pages:
            cache.popitem(last=False)

    @staticmethod
    def _remember(page):
        # Ephemeral values: persisted OCR inventories and layouts remain neutral.
        page["_normalized_body"] = body_text(page)
        page["_normalized_heading"] = heading_text(page)
        return page

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
            self._store(self._retried, number, page)
        self._retried.move_to_end(number)
        return self._retried[number]


@contextmanager
def open_inventory(root, source, *, pdf=None):
    import pymupdf

    original = source_path(root, source)
    if pdf is not None:
        inventory = PageInventory(root, source, pdf)
        try:
            yield inventory
        finally:
            inventory.close()
    else:
        with pymupdf.open(original) as document:
            inventory = PageInventory(root, source, document)
            try:
                yield inventory
            finally:
                inventory.close()


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
    if "_normalized_body" in page:
        return page["_normalized_body"]
    return normalize(" ".join(w[4] for w in words_normalized(page) if w[1] < 0.94))


def heading_text(page):
    if "_normalized_heading" in page:
        return page["_normalized_heading"]
    return normalize(" ".join(w[4] for w in words_normalized(page) if w[1] < 0.24))
