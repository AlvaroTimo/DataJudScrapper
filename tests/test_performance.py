from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest
import respx

from datajud_scraper.adhesion.common import read_json, write_json
from datajud_scraper.adhesion.inventory import open_inventory
from datajud_scraper.adhesion.local_model import LocalModel, inference_snapshot, inference_timings
from datajud_scraper.adhesion.resources import resource_scope, worker_limit
from datajud_scraper.pdf_validation import hash_file

pymupdf = pytest.importorskip("pymupdf")


@pytest.mark.parametrize("scanned", [False, True])
@pytest.mark.parametrize("defect", [None, "identifier", "clause"])
def test_positive_benchmark_checks_pixels_independently_of_completed_status(
    tmp_path, scanned, defect
):
    import io

    from datajud_scraper.adhesion.pdf import redact_pixels, render_contract_page

    script = Path(__file__).resolve().parents[1] / "scripts/benchmark_pipeline.py"
    benchmark = runpy.run_path(str(script))
    folder = tmp_path / "pdfs"
    folder.mkdir()
    source = benchmark["synthetic_source"](tmp_path, folder, scanned=scanned, seed=17)
    with pymupdf.open(tmp_path / source["relative_path"]) as pdf:
        image = render_contract_page(pdf, {"page": 1}, dpi=300)
    masks = [{"rect": [0, 0.9, 1, 1], "category": "test", "origin": "automatic"}]
    if defect != "identifier":
        masks.append({"rect": [0, 0.175, 1, 0.32], "category": "test", "origin": "automatic"})
    if defect == "clause":
        masks.append({"rect": [0, 0.35, 1, 0.66], "category": "test", "origin": "automatic"})
    cleaned, _ = redact_pixels(image, masks, decision_mode="automatic")
    buffer = io.BytesIO()
    cleaned.save(buffer, format="PNG")
    output = tmp_path / "cleaned.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=420, height=550)
        page.insert_image(page.rect, stream=buffer.getvalue())
        pdf.save(output)
    result = {
        "status": "completed",
        "instruments": [{"regions": [{"page": 1}], "output": {"path": str(output)}}],
    }
    checks = benchmark["synthetic_checks"](tmp_path, source, result)
    assert checks["positive_completed"] is True
    assert checks["personal_pixels_erased"] is (defect != "identifier")
    assert checks["footer_pixels_erased"] is True
    assert checks["contractual_pixels_preserved"] is (defect != "clause")


def make_source(root, count=8):
    path = root / "pdfs/source.pdf"
    path.parent.mkdir()
    with pymupdf.open() as pdf:
        for n in range(count):
            page = pdf.new_page(width=300, height=300)
            page.insert_text((20, 30), f"TERMO DE ADESAO AO CARTAO {n}")
            page.insert_text((20, 50), "Clausulas e condicoes contratuais.")
        pdf.save(path)
    return {
        "document_id": "parallel",
        "relative_path": "pdfs/source.pdf",
        "sha256": hash_file(path),
        "page_count": count,
    }


def test_workers_respect_memory_cpu_and_small_computers(monkeypatch):
    from datajud_scraper.adhesion import resources

    monkeypatch.setattr(resources, "available_cpus", lambda: 36)
    monkeypatch.setattr(resources, "available_memory_mb", lambda: 8192)
    assert worker_limit(0, 2048, 40 * 1024**2) == 6
    assert worker_limit(32, 1024, 40 * 1024**2) == 3
    monkeypatch.setattr(resources, "available_memory_mb", lambda: 512)
    assert worker_limit(32) == 1
    monkeypatch.setattr(resources, "available_cpus", lambda: 1)
    assert worker_limit(32) == 1


@pytest.mark.parametrize(
    "files,expected",
    [
        ({"cpu.max": "200000 100000"}, 2),
        ({"cpu.max": "50000 100000"}, 1),
        ({"cpu.max": "max 100000"}, 36),
        ({"cpu.max": "invalid"}, 36),
        ({"cpu.max": "100000 0"}, 36),
        ({"cpu.cfs_quota_us": "200000", "cpu.cfs_period_us": "100000"}, 2),
        ({"cpu.cfs_quota_us": "-1", "cpu.cfs_period_us": "100000"}, 36),
        ({}, 36),
    ],
)
def test_cpu_quota_limits_ocr_concurrency_even_with_host_affinity(monkeypatch, files, expected):
    from datajud_scraper.adhesion import resources

    def read(path):
        if path.name not in files:
            raise FileNotFoundError(path)
        return files[path.name]

    monkeypatch.setattr(resources.os, "sched_getaffinity", lambda _: set(range(36)))
    monkeypatch.setattr(resources.Path, "read_text", read)
    assert resources.available_cpus() == expected


def test_parallel_inventory_matches_serial_and_resumes_without_pool(tmp_path, monkeypatch):
    from datajud_scraper.adhesion import inventory

    source = make_source(tmp_path)
    monkeypatch.setattr(inventory, "worker_limit", lambda *args: 2)
    with resource_scope(workers=2), open_inventory(tmp_path, source) as pages:
        pages.prefetch(range(1, len(pages) + 1))
        recognized = list(pages)
        assert pages.stats["native_reads"] == 8
        assert pages.stats["ocr_pages"] == 0
        folder = pages.folder
    for current in folder.glob("*.json"):
        assert current.stat().st_mode & 0o777 == 0o600
        assert current.read_text().count("\n") == 1
        current.unlink()
    with resource_scope(workers=1), open_inventory(tmp_path, source) as pages:
        # Explicitly force the serial path to compare the same outputs.
        pages.workers = 1
        assert list(pages) == recognized
    with open_inventory(tmp_path, source) as pages:
        pages.prefetch(range(1, len(pages) + 1))
        assert pages._pool is None
        assert pages.stats["native_reads"] == 0
        assert pages.stats["cache_hits"] == 8
        assert list(pages) == recognized


def test_background_inventory_consumes_out_of_order_without_duplicate_reads(tmp_path, monkeypatch):
    from datajud_scraper.adhesion import inventory

    source = make_source(tmp_path)
    monkeypatch.setattr(inventory, "worker_limit", lambda *args: 2)
    with open_inventory(tmp_path, source) as pages:
        assert pages.start_prefetch(range(1, 9))
        assert len(pages._pending) == 8
        assert pages.stats["native_reads"] == 0
        assert "CARTAO 7" in pages[7]["text"]
        pages.prefetch(range(1, 9))
        assert not pages._pending
        assert pages.stats["native_reads"] == 8
        assert len(pages._recognized) == 1
        assert all("CARTAO" in page["text"] for page in pages)
        assert pages.stats["native_reads"] == 8


def test_interruption_terminates_background_workers(tmp_path, monkeypatch):
    from datajud_scraper.adhesion import inventory

    source = make_source(tmp_path)
    monkeypatch.setattr(inventory, "worker_limit", lambda *args: 2)
    with pytest.raises(KeyboardInterrupt), open_inventory(tmp_path, source) as pages:
        pages.start_prefetch(range(1, 9))
        processes = list(pages._pool._pool)
        raise KeyboardInterrupt
    assert pages._pool is None and not pages._pending
    assert all(not process.is_alive() for process in processes)


def test_parallel_retries_match_serial_and_keep_recognized_inventory(tmp_path, monkeypatch):
    from datajud_scraper.adhesion import inventory
    from datajud_scraper.adhesion.text import DEFAULT_TESSDATA

    if not all(
        (DEFAULT_TESSDATA / name).exists() for name in ("por.traineddata", "eng.traineddata")
    ):
        pytest.skip("requires pinned OCR language data")
    source = make_source(tmp_path, count=3)
    monkeypatch.setattr(inventory, "worker_limit", lambda *args: 2)
    with open_inventory(tmp_path, source) as pages:
        original = pages[0]
        assert pages.start_prefetch([1, 2], retry=True)
        parallel = [pages.retry(number) for number in (1, 2)]
        assert pages.stats["ocr_retries"] == 2
        assert pages[0] == original
        assert all(page["ocr_dpi"] == 300 for page in parallel)
        folder = pages.folder
    for path in folder.glob("*-retry.json"):
        path.unlink()
    with open_inventory(tmp_path, source) as pages:
        pages.workers = 1
        assert [pages.retry(number) for number in (1, 2)] == parallel


def test_worker_rejects_native_cache_from_another_source(tmp_path, monkeypatch):
    from datajud_scraper.adhesion import inventory

    source = make_source(tmp_path)
    monkeypatch.setattr(inventory, "worker_limit", lambda *args: 2)
    with open_inventory(tmp_path, source) as pages:
        pages.get_native(1)
        path = pages.folder / "00001-native.json"
        cached = read_json(path)
        write_json(path, {**cached, "source_sha256": "wrong"})
        with pytest.raises(ValueError, match="another source/configuration"):
            pages.prefetch(range(1, 9))
        assert pages._pool is None


def test_large_inventory_evicts_memory_and_reloads_verified_disk_cache(tmp_path):
    source = make_source(tmp_path, count=6)
    with open_inventory(tmp_path, source) as pages:
        pages.memory_pages = 2
        first = pages[0]
        for n in range(1, 6):
            pages[n]
        assert len(pages._recognized) == len(pages._native) == 2
        reads = pages.stats["native_reads"]
        assert pages[0] == first
        assert pages.stats["native_reads"] == reads
        assert pages.stats["cache_hits"] == 1


def test_parallel_ocr_matches_serial_on_scanned_pages(tmp_path, monkeypatch):
    from datajud_scraper.adhesion import inventory
    from datajud_scraper.adhesion.text import DEFAULT_TESSDATA

    if not all(
        (DEFAULT_TESSDATA / name).exists() for name in ("por.traineddata", "eng.traineddata")
    ):
        pytest.skip("requires pinned OCR language data")
    source = make_source(tmp_path)
    scanned_path = tmp_path / "pdfs/scanned.pdf"
    with pymupdf.open(tmp_path / source["relative_path"]) as original, pymupdf.open() as scanned:
        for page in original:
            pixels = page.get_pixmap(dpi=150, alpha=False)
            target = scanned.new_page(width=page.rect.width, height=page.rect.height)
            target.insert_image(target.rect, stream=pixels.tobytes("png"))
        scanned.save(scanned_path)
    source.update(relative_path="pdfs/scanned.pdf", sha256=hash_file(scanned_path))
    monkeypatch.setattr(inventory, "worker_limit", lambda *args: 2)
    with open_inventory(tmp_path, source) as pages:
        pages.prefetch(range(1, 9))
        parallel = list(pages)
        assert pages.stats["ocr_pages"] == 8
        assert all(p["text_method"] == "ocr" and not p["ocr_error"] for p in parallel)
        assert all("CARTAO" in p["text"] for p in parallel)
        folder = pages.folder
    for path in folder.glob("*.json"):
        path.unlink()
    with open_inventory(tmp_path, source) as pages:
        pages.workers = 1
        assert list(pages) == parallel


def test_neural_ocr_threads_respect_cpu_affinity(monkeypatch):
    from datajud_scraper.adhesion import ocr

    monkeypatch.setattr(ocr, "available_cpus", lambda: 1)
    monkeypatch.setattr(ocr, "version", lambda _: "test")
    config = ocr.neural_configuration()
    assert config["intra_threads"] == config["inter_threads"] == 1


def test_crop_does_not_reuse_normalized_text_from_outside_its_region():
    from datajud_scraper.adhesion.inventory import body_text
    from datajud_scraper.adhesion.pdf import region_inventory

    page = {
        "width": 100,
        "height": 100,
        "rotation": 0,
        "words": [[0, 0, 20, 10, "OUTSIDE"], [30, 40, 60, 50, "INSIDE"]],
        "_normalized_body": "outside inside",
        "_normalized_heading": "outside",
    }
    cropped = region_inventory(
        page,
        {"page": 1, "rect": [0.2, 0.2, 0.8, 0.8]},
        {
            "source_size": [100, 100],
            "crop_box": [20, 20, 80, 80],
            "rotation": 0,
            "output_size": [60, 60],
        },
    )
    assert body_text(cropped) == "inside"


def test_recognizing_native_inventory_does_not_parse_native_text_again():
    from datajud_scraper.adhesion.text import recognize_inventory

    class OCR:
        def extractText(self, *, sort):
            return "Texto reconocido"

    class Page:
        rect = pymupdf.Rect(0, 0, 300, 300)
        derotation_matrix = pymupdf.Matrix(1, 1)

        def get_textpage_ocr(self, **kwargs):
            return OCR()

        def get_text(self, *args, **kwargs):
            assert "textpage" in kwargs
            return [[10, 10, 100, 20, "Texto reconocido"]]

    original = {"text": "Native", "native_text": None, "words": [], "needs_ocr": True}
    result = recognize_inventory(Page(), original, Path("unused"))
    assert result["text_method"] == "ocr" and result["text"] == "Texto reconocido"
    assert original["text"] == "Native"


def test_local_model_cache_is_bound_to_inference_options(tmp_path):
    endpoint = "http://127.0.0.1:11434"
    with respx.mock as router:
        router.get(endpoint + "/api/tags").respond(
            200, json={"models": [{"name": "small:4b", "digest": "digest"}]}
        )
        router.get(endpoint + "/api/version").respond(200, json={"version": "test"})
        chat = router.post(endpoint + "/api/chat").respond(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "message": {"content": '{"roles":["N"]}'},
                "prompt_eval_count": 200,
                "eval_count": 12,
            },
        )
        model = LocalModel(tmp_path, model="small:4b", context_tokens=4096)
        assert model.ask("test", "system", "text", {}) == {"roles": ["N"]}
        model.ask("test", "system", "text", {})
        assert model.calls == 1 and model.cache_hits == 1
        payload = json.loads(chat.calls.last.request.content)
        assert payload["options"]["num_ctx"] == 4096
        model.close()
        changed = LocalModel(tmp_path, model="small:4b", context_tokens=8192)
        changed.ask("test", "system", "text", {})
        changed.close()
        assert chat.call_count == 2


def test_model_timings_include_loading_and_do_not_recount_cache_hits(tmp_path):
    endpoint = "http://127.0.0.1:11434"
    with respx.mock as router:
        router.get(endpoint + "/api/tags").respond(
            200, json={"models": [{"name": "small:4b", "digest": "digest"}]}
        )
        router.get(endpoint + "/api/version").respond(200, json={"version": "test"})
        chat = router.post(endpoint + "/api/chat").respond(
            200,
            json={
                "done": True,
                "message": {"content": "{}"},
                "load_duration": 1_000_000_000,
                "prompt_eval_duration": 2_000_000_000,
                "eval_duration": 3_000_000_000,
            },
        )
        model = LocalModel(tmp_path, model="small:4b")
        before = inference_snapshot(model)
        model.ask("test", "system", "text", {})
        assert inference_timings(model, before) == {
            "load_seconds": 1.0,
            "prompt_seconds": 2.0,
            "decode_seconds": 3.0,
        }
        before = inference_snapshot(model)
        model.ask("test", "system", "text", {})
        assert set(inference_timings(model, before).values()) == {0.0}
        chat.respond(200, json={"done": True, "message": {"content": "{}"}})
        model.ask("test", "system", "different text", {})
        assert set(inference_timings(model, before).values()) == {None}
        assert model.backend_seconds["load_seconds"] == 1.0
        model.close()


@pytest.mark.parametrize("reason,prompt", [("length", 1), ("stop", 4000)])
def test_local_model_never_caches_truncated_or_saturated_answers(tmp_path, reason, prompt):
    endpoint = "http://127.0.0.1:11434"
    with respx.mock as router:
        router.get(endpoint + "/api/tags").respond(
            200, json={"models": [{"name": "small:4b", "digest": "digest"}]}
        )
        router.get(endpoint + "/api/version").respond(200, json={"version": "test"})
        router.post(endpoint + "/api/chat").respond(
            200,
            json={
                "done": True,
                "done_reason": reason,
                "message": {"content": "{}"},
                "prompt_eval_count": prompt,
            },
        )
        model = LocalModel(tmp_path, model="small:4b", context_tokens=4096)
        with pytest.raises(ValueError):
            model.ask("test", "system", "text", {})
        model.close()
        assert not list(tmp_path.rglob("*.json"))
