from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import respx
from conftest import DOWNLOAD_URL, PROCESS_NUMBER, PUBLIC_URL, SOURCE_URL, case_html

from datajud_scraper.adhesion import pipeline
from datajud_scraper.adhesion.common import digest, read_json
from datajud_scraper.adhesion.pdf import render_contract_page
from datajud_scraper.batch import BatchService
from datajud_scraper.contract_processing import ContractProcessor
from datajud_scraper.errors import PauseError
from datajud_scraper.pdf_validation import hash_file

pymupdf = pytest.importorskip("pymupdf")
pytest.importorskip("PIL.Image")


@pytest.fixture
def phases(monkeypatch):
    state = SimpleNamespace(
        calls=Counter(),
        status="completed",
        fail=False,
        no_target=False,
        unresolved=False,
        config={"extraction": {"version": 1}, "anonymization": {"version": 1}},
    )

    class Inventory(list):
        attachments = []

    @contextmanager
    def inventory(root, source, *, pdf):
        state.calls["inventory"] += 1
        yield Inventory(
            [
                {
                    "page_number": n + 1,
                    "width": p.rect.width,
                    "height": p.rect.height,
                    "words": [list(w[:5]) for w in p.get_text("words")],
                    "text_method": "native",
                }
                for n, p in enumerate(pdf)
            ]
        )

    def detect(*args):
        state.calls["detection"] += 1
        return {
            "instruments": [] if state.no_target else [{"pages": [1], "family": "generic"}],
            "unresolved": [{"page": 2}] if state.unresolved else [],
            "fallback_complete": True,
        }

    def clean(model, image, current, folder):
        state.calls["anonymization"] += 1
        if state.fail:
            state.fail = False
            raise InterruptedError("simulated privacy interruption")
        return {
            "status": state.status,
            "masks": [{"rect": [0, 0, 0.4, 0.5], "category": "personal", "origin": "automatic"}],
        }

    def prepare(processor):
        if processor.model is not None:
            return
        processor.model = SimpleNamespace(cache=None, close=lambda: None)
        processor.pipeline_config = state.config
        processor.signature = digest(
            state.config if processor.config.contract_mode == "both" else state.config["extraction"]
        )

    monkeypatch.setattr(pipeline, "open_inventory", inventory)
    monkeypatch.setattr(pipeline, "detect_with_model", detect)
    monkeypatch.setattr(pipeline, "anonymize", clean)
    monkeypatch.setattr(ContractProcessor, "prepare", prepare)
    return state


def synthetic_pdf(path):
    with pymupdf.open() as pdf:
        p = pdf.new_page(width=120, height=160)
        p.draw_rect((10, 25, 35, 50), fill=(0, 0, 0))
        p.draw_rect((80, 110, 100, 130), fill=(0, 0, 0))
        p.insert_text((5, 10), f"Processo n: {PROCESS_NUMBER}", fontsize=3)
        p.insert_text((5, 100), "Credit terms: 2.00%", fontsize=5)
        pdf.save(path)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "pdfs/original.pdf"
    path.parent.mkdir()
    synthetic_pdf(path)
    return {
        "document_id": "synthetic",
        "relative_path": "pdfs/original.pdf",
        "sha256": hash_file(path),
        "page_count": 1,
    }


@pytest.fixture
def portal(respx_mock, tmp_path):
    path = tmp_path / "portal.pdf"
    synthetic_pdf(path)
    respx_mock.get(PUBLIC_URL).respond(200, content=case_html())
    respx_mock.get(SOURCE_URL).respond(200, content=case_html())
    return respx_mock.get(DOWNLOAD_URL).respond(
        200, content=path.read_bytes(), headers={"Content-Type": "application/pdf"}
    )


def result(summary):
    return json.loads(Path(summary["results_path"]).read_text().splitlines()[0])["result"]


def processor_files(root):
    return root / "adhesion-scraper"


def test_phases_are_independent_and_preserve_exact_pixels(tmp_path, source, phases, monkeypatch):
    extraction = pipeline.extract_document(tmp_path, source, None, phases.config)
    item = extraction["instruments"][0]
    raw = Path(item["output"]["path"])
    raw_hash = hash_file(raw)
    assert phases.calls == {"inventory": 1, "detection": 1}
    assert raw.stat().st_mode & 0o777 == 0o600
    (tmp_path / source["relative_path"]).unlink()

    def forbidden(*args, **kwargs):
        pytest.fail("phase 2 called source detection/inventory")

    monkeypatch.setattr(pipeline, "detect_with_model", forbidden)
    monkeypatch.setattr(pipeline, "open_inventory", forbidden)
    cleaned = pipeline.anonymize_extraction(
        tmp_path, extraction["manifest_path"], None, phases.config
    )
    output = cleaned["instruments"][0]["output"]
    assert hash_file(raw) == raw_hash
    assert output["page_count"] == 1
    with pymupdf.open(raw) as pdf:
        before = render_contract_page(pdf, {"page": 1}, raster_source=True)
    with pymupdf.open(output["path"]) as pdf:
        after = render_contract_page(pdf, {"page": 1}, raster_source=True)
        assert not pdf[0].get_text().strip() and not pdf[0].get_links()
    assert before.size == after.size
    assert before.getpixel((80, 150)) == (0, 0, 0)
    assert after.getpixel((80, 150)) == (255, 255, 255)
    assert before.getpixel((370, 500)) == after.getpixel((370, 500)) == (0, 0, 0)
    assert (
        output["pages"][0]["original_pixels_sha256"]
        == item["output"]["pages"][0]["cleaned_pixels_sha256"]
    )


def test_privacy_config_changes_reuse_extraction(tmp_path, source, phases):
    first = pipeline.run_document(tmp_path, source, None, phases.config)
    second_config = {**phases.config, "anonymization": {"version": 2}}
    second = pipeline.run_document(tmp_path, source, None, second_config)
    assert first["run_id"] != second["run_id"]
    assert first["extraction_id"] == second["extraction_id"]
    assert phases.calls == {"inventory": 1, "detection": 1, "anonymization": 2}


@pytest.mark.parametrize("artifact", ["output", "layout"])
def test_phase_two_rejects_tampered_inputs(tmp_path, source, phases, artifact):
    extraction = pipeline.extract_document(tmp_path, source, None, phases.config)
    path = Path(extraction["instruments"][0][artifact]["path"])
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="missing or changed"):
        pipeline.anonymize_extraction(tmp_path, extraction["manifest_path"], None, phases.config)
    assert phases.calls["anonymization"] == 0
    with pipeline.state_connection(tmp_path) as connection:
        assert connection.execute("SELECT status FROM runs").fetchone()[0] == "error"


@pytest.mark.parametrize("mode", ["both", "extract", "none"])
@respx.mock
def test_scraper_modes(mode, phases, portal, dataset_factory, test_config):
    config = replace(test_config, contract_mode=mode)
    summary = BatchService(config).start(*dataset_factory())
    saved = result(summary)
    assert summary["status"] == "completed"
    assert portal.call_count == 1
    if mode == "none":
        assert "contract_processing" not in saved
        assert Path(saved["pdf_path"]).exists()
        assert not processor_files(config.storage_root).exists()
        assert not phases.calls
    else:
        metadata = saved["contract_processing"]
        assert metadata["mode"] == mode and metadata["status"] == "completed"
        assert phases.calls["detection"] == 1
        assert phases.calls["anonymization"] == (1 if mode == "both" else 0)
        assert len(saved["contract_paths"]) == 1
        assert Path(saved["contract_paths"][0]).is_file()
        assert metadata["source_retained"] == (mode == "extract")
        assert (saved["pdf_path"] is not None) == (mode == "extract")


@respx.mock
def test_default_purge_removes_private_artifacts_and_resume_uses_clean_pdf(
    phases, portal, dataset_factory, test_config
):
    config = replace(test_config, contract_mode="both")
    service = BatchService(config)
    first = service.start(*dataset_factory())
    metadata = result(first)["contract_processing"]
    raw_manifest = read_json(metadata["phases"]["extraction"]["manifest_path"])
    raw_item = raw_manifest["instruments"][0]
    assert not list((config.storage_root / "pdfs").rglob("*.pdf"))
    assert not Path(raw_item["output"]["path"]).exists()
    assert not Path(raw_item["layout"]["path"]).exists()
    assert metadata["phases"]["extraction"]["artifacts_available"] is False
    assert not list(processor_files(config.storage_root).rglob("privacy.json"))
    second = service.resume(first["batch_id"])
    assert second["counts"] == {"contracts_preserved": 1}
    assert portal.call_count == 1
    assert phases.calls == {"inventory": 1, "detection": 1, "anonymization": 1}
    assert Path(result(second)["contract_paths"][0]).exists()


@respx.mock
def test_keep_then_purge_reuses_verified_output(phases, portal, dataset_factory, test_config):
    keep = replace(test_config, contract_mode="both", contract_retention="keep")
    first = BatchService(keep).start(*dataset_factory())
    kept = result(first)
    assert Path(kept["pdf_path"]).is_file()
    raw = Path(kept["contract_processing"]["phases"]["extraction"]["paths"][0])
    assert raw.is_file()
    report = Path(first["report_path"]).read_text()
    assert "Fases de contratos" in report and "extraction: completed" in report
    assert "anonymization: completed" in report
    purged = BatchService(replace(keep, contract_retention="purge")).resume(first["batch_id"])
    assert purged["counts"] == {"contracts_preserved": 1}
    assert not raw.exists() and not Path(kept["pdf_path"]).exists()
    assert phases.calls["anonymization"] == portal.call_count == 1


@respx.mock
def test_extract_then_both_uses_phase_one_without_downloading(
    phases, portal, dataset_factory, test_config
):
    extract = replace(test_config, contract_mode="extract")
    first = BatchService(extract).start(*dataset_factory())
    second = BatchService(replace(extract, contract_mode="both")).resume(first["batch_id"])
    assert second["counts"] == {"already_exists": 1}
    assert not result(second)["contract_processing"]["source_retained"]
    assert phases.calls == {"inventory": 1, "detection": 1, "anonymization": 1}
    assert portal.call_count == 1


@respx.mock
def test_phase_two_failure_preserves_source_and_retries_without_detection(
    phases, portal, dataset_factory, test_config
):
    phases.fail = True
    service = BatchService(replace(test_config, contract_mode="both"))
    first = service.start(*dataset_factory())
    assert first["counts"] == {"failed": 1}
    assert first["status"] == "completed_with_errors"
    assert list((test_config.storage_root / "pdfs").rglob("*.pdf"))
    assert list(processor_files(test_config.storage_root).rglob("private-layout.json"))
    second = service.resume(first["batch_id"], retry_failed=True)
    assert second["status"] == "completed"
    assert phases.calls == {"inventory": 1, "detection": 1, "anonymization": 2}
    assert portal.call_count == 1


@pytest.mark.parametrize("reason", ["privacy", "extraction", "no_target"])
@respx.mock
def test_unverified_or_no_target_never_purges_source(
    reason, phases, portal, dataset_factory, test_config
):
    phases.status = "needs_review" if reason == "privacy" else "completed"
    phases.unresolved = reason == "extraction"
    phases.no_target = reason == "no_target"
    service = BatchService(replace(test_config, contract_mode="both"))
    summary = service.start(*dataset_factory())
    saved = result(summary)
    assert saved["contract_processing"]["source_retained"]
    assert Path(saved["pdf_path"]).exists()
    if reason == "no_target":
        assert summary["status"] == "completed"
        assert saved["contract_processing"]["status"] == "no_target"
    else:
        assert summary["status"] == "completed_with_errors"
        assert summary["counts"] == {"processing_needs_review": 1}
        if reason == "privacy":
            assert not saved.get("contract_paths")
            quarantine = saved["contract_processing"]["phases"]["anonymization"]["paths"][0]
            assert "quarantine" in quarantine and Path(quarantine).is_file()
        resumed = service.resume(summary["batch_id"])
        assert resumed["metrics"]["bytes_received"] == summary["metrics"]["bytes_received"]
        assert portal.call_count == 1


@respx.mock(assert_all_called=False)
def test_unavailable_backend_pauses_before_pdf_download(
    portal, dataset_factory, test_config, monkeypatch
):
    def unavailable(*args):
        raise PauseError("contract_processing_unavailable", "missing backend")

    monkeypatch.setattr(ContractProcessor, "prepare", unavailable)
    summary = BatchService(replace(test_config, contract_mode="both")).start(*dataset_factory())
    assert summary["status"] == "paused"
    assert summary["stop_reason"] == "contract_processing_unavailable"
    assert portal.call_count == 0
    assert not list((test_config.storage_root / "pdfs").rglob("*.pdf"))


@respx.mock
def test_interrupted_purge_resumes_without_source_or_model_calls(
    phases, portal, dataset_factory, test_config, monkeypatch
):
    original_unlink = Path.unlink
    attempts = 0

    def interrupt(path, *args, **kwargs):
        nonlocal attempts
        if path.name == "private-layout.json" and attempts == 0:
            attempts += 1
            raise InterruptedError("simulated cleanup interruption")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interrupt)
    service = BatchService(replace(test_config, contract_mode="both"))
    first = service.start(*dataset_factory())
    assert first["counts"] == {"failed": 1}
    release = next(processor_files(test_config.storage_root).glob("scraper-releases/*.json"))
    assert read_json(release)["cleanup_status"] == "pending"
    assert not list((test_config.storage_root / "pdfs").rglob("*.pdf"))
    second = service.resume(first["batch_id"], retry_failed=True)
    assert second["counts"] == {"contracts_preserved": 1}
    assert read_json(release)["cleanup_status"] == "completed"
    assert phases.calls["anonymization"] == portal.call_count == 1


@respx.mock
def test_modes_after_purge_create_new_immutable_generation(
    phases, portal, dataset_factory, test_config
):
    both = replace(test_config, contract_mode="both")
    first = BatchService(both).start(*dataset_factory())
    initial = result(first)["contract_processing"]["phases"]["extraction"]["manifest_path"]
    saved_manifest = Path(initial).read_bytes()
    extract = BatchService(replace(both, contract_mode="extract")).resume(first["batch_id"])
    newer = result(extract)["contract_processing"]["phases"]["extraction"]["manifest_path"]
    assert newer != initial and Path(initial).read_bytes() == saved_manifest
    assert Path(result(extract)["pdf_path"]).exists()
    again = BatchService(both).resume(first["batch_id"])
    assert again["status"] == "completed"
    assert not result(again)["contract_processing"]["source_retained"]
    assert phases.calls == {"inventory": 2, "detection": 2, "anonymization": 2}
    assert portal.call_count == 2


@respx.mock
def test_altered_final_pdf_blocks_cleanup_and_redownload(
    phases, portal, dataset_factory, test_config
):
    service = BatchService(replace(test_config, contract_mode="both", contract_retention="keep"))
    first = service.start(*dataset_factory())
    saved = result(first)
    clean = Path(saved["contract_paths"][0])
    clean.write_bytes(clean.read_bytes() + b"changed")
    second = BatchService(replace(service.config, contract_retention="purge")).resume(
        first["batch_id"]
    )
    assert second["counts"] == {"failed": 1}
    assert Path(saved["pdf_path"]).exists()
    assert Path(saved["contract_processing"]["phases"]["extraction"]["paths"][0]).exists()
    assert portal.call_count == 1


@respx.mock
def test_refresh_identical_source_is_purged_again(phases, portal, dataset_factory, test_config):
    service = BatchService(replace(test_config, contract_mode="both"))
    first = service.start(*dataset_factory())
    second = service.resume(first["batch_id"], refresh=True)
    assert second["status"] == "completed"
    assert not result(second)["contract_processing"]["source_retained"]
    assert not list((test_config.storage_root / "pdfs").rglob("*.pdf"))
    assert phases.calls["detection"] == phases.calls["anonymization"] == 1
    assert portal.call_count == 2


def test_extractor_preflight_does_not_load_privacy_runtime(test_config, monkeypatch):
    from datajud_scraper.adhesion import inventory, local_model, ocr

    monkeypatch.setattr(
        inventory,
        "extraction_configuration",
        lambda: {"models": {"por.traineddata": "por", "eng.traineddata": "eng"}},
    )
    monkeypatch.setattr(
        local_model, "LocalModel", lambda *args: SimpleNamespace(close=lambda: None)
    )

    def config(model, *, include_privacy, include_reference):
        assert include_privacy is False and include_reference is False
        return {"extraction": {"version": 1}}

    def forbidden():
        pytest.fail("extraction preflight initialized anonymization OCR")

    monkeypatch.setattr(pipeline, "configuration", config)
    monkeypatch.setattr(ocr, "neural_engine", forbidden)
    processor = ContractProcessor(replace(test_config, contract_mode="extract"), None)
    processor.prepare()
    assert processor.signature == digest({"version": 1})
    processor.close()


def test_both_preflight_rejects_missing_privacy_models_before_local_model(test_config, monkeypatch):
    from datajud_scraper import ocr_models
    from datajud_scraper.adhesion import inventory, local_model

    monkeypatch.setattr(
        inventory,
        "extraction_configuration",
        lambda: {"models": {"por.traineddata": "por", "eng.traineddata": "eng"}},
    )
    monkeypatch.setattr(ocr_models, "MODEL_ROOT", test_config.storage_root / "missing-models")

    def forbidden(*args):
        pytest.fail("unprepared privacy runtime reached local model initialization")

    monkeypatch.setattr(local_model, "LocalModel", forbidden)
    processor = ContractProcessor(replace(test_config, contract_mode="both"), None)
    with pytest.raises(PauseError, match="procesamiento local no esta disponible"):
        processor.prepare()
    assert processor.model is None


@respx.mock
def test_restore_original_then_resume_purges_restored_copy(
    phases, portal, dataset_factory, test_config, tmp_path
):
    service = BatchService(replace(test_config, contract_mode="both"))
    first = service.start(*dataset_factory())
    extraction = read_json(
        result(first)["contract_processing"]["phases"]["extraction"]["manifest_path"]
    )
    original = test_config.storage_root / extraction["source"]["relative_path"]
    original.write_bytes((tmp_path / "portal.pdf").read_bytes())
    second = service.resume(first["batch_id"])
    assert second["counts"] == {"contracts_preserved": 1}
    assert not original.exists()
    assert phases.calls["anonymization"] == portal.call_count == 1


@respx.mock
def test_changed_extracted_pdf_blocks_purge_before_any_deletion(
    phases, portal, dataset_factory, test_config
):
    keep = replace(test_config, contract_mode="both", contract_retention="keep")
    first = BatchService(keep).start(*dataset_factory())
    saved = result(first)
    raw = Path(saved["contract_processing"]["phases"]["extraction"]["paths"][0])
    raw.write_bytes(raw.read_bytes() + b"changed")
    second = BatchService(replace(keep, contract_retention="purge")).resume(first["batch_id"])
    assert second["counts"] == {"failed": 1}
    assert Path(saved["pdf_path"]).is_file() and raw.is_file()
    assert portal.call_count == 1


@respx.mock
def test_private_cache_symlink_never_deletes_another_documents_files(
    phases, portal, dataset_factory, test_config
):
    keep = replace(test_config, contract_mode="both", contract_retention="keep")
    first = BatchService(keep).start(*dataset_factory())
    saved = result(first)
    extraction = read_json(saved["contract_processing"]["phases"]["extraction"]["manifest_path"])
    work = processor_files(keep.storage_root)
    other = work / "model-cache/another-document"
    other.mkdir(parents=True)
    sentinel = other / "private.json"
    sentinel.write_text("synthetic private cache")
    (other.parent / extraction["document_id"]).symlink_to(other, target_is_directory=True)
    second = BatchService(replace(keep, contract_retention="purge")).resume(first["batch_id"])
    assert second["counts"] == {"failed": 1}
    assert sentinel.read_text() == "synthetic private cache"
    assert Path(saved["pdf_path"]).is_file()


@respx.mock
def test_independent_cli_commands_work_without_prepared_corpus(
    phases, portal, dataset_factory, test_config, monkeypatch, capsys
):
    from datajud_scraper.adhesion import vision
    from datajud_scraper.adhesion.cli import main

    BatchService(test_config).start(*dataset_factory())
    with sqlite3.connect(test_config.storage_root / "state/scraper.sqlite3") as connection:
        document_id, relative = connection.execute(
            "SELECT document_id,relative_path FROM documents"
        ).fetchone()
    monkeypatch.setattr(vision, "LocalModel", lambda *args: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(pipeline, "configuration", lambda *args, **kwargs: phases.config)
    arguments = ["--storage-root", str(test_config.storage_root), "--workspace", "adhesion-scraper"]
    assert main([*arguments, "extract", "--document", document_id]) == 0
    extraction = json.loads(capsys.readouterr().out)
    assert extraction["phase"] == "extraction"
    assert phases.calls["anonymization"] == 0
    (test_config.storage_root / relative).unlink()
    assert (
        main([*arguments, "anonymize", "--extraction-manifest", extraction["manifest_path"]]) == 0
    )
    anonymized = json.loads(capsys.readouterr().out)
    assert anonymized["phase"] == "anonymization" and anonymized["status"] == "completed"
    assert Path(extraction["instruments"][0]["output"]["path"]).is_file()
    assert phases.calls["detection"] == phases.calls["anonymization"] == 1
