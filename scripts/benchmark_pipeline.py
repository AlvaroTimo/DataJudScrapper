"""Benchmark downloaded PDFs in an isolated workspace; never contacts the court.

Cold means no inventory, extraction, inference or output cache for the measured run.
OS disk caching and Ollama's in-memory model state are not reset. Model metadata lookup
and backend-reported loading durations are measured separately.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path

from datajud_scraper.adhesion.common import workspace_scope
from datajud_scraper.adhesion.inventory import open_inventory
from datajud_scraper.adhesion.local_model import (
    DEFAULT_MODEL,
    LocalModel,
    inference_snapshot,
    inference_timings,
)
from datajud_scraper.adhesion.ocr import neural_engine
from datajud_scraper.adhesion.pdf import pixel_box, render_contract_page
from datajud_scraper.adhesion.pipeline import anonymize_extraction, configuration, extract_document
from datajud_scraper.adhesion.resources import resource_scope, worker_limit
from datajud_scraper.adhesion.text import DEFAULT_TESSDATA, page_inventory
from datajud_scraper.adhesion.vision import detect_with_model
from datajud_scraper.pdf_validation import hash_file, validate_pdf


def synthetic_source(root, folder, *, scanned=False, seed=0):
    """A fictitious positive exercises actual extraction and privacy, not model mocks."""
    import pymupdf

    path = folder / "synthetic.pdf"
    process_number = "0003714-33.2025.8.05.0274"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=420, height=550)
        for y, text, size in (
            (35, "TERMO DE ADESAO AO CARTAO DE CREDITO", 13),
            (58, "BANCO EXEMPLO S.A.", 11),
            (95, "DADOS PESSOAIS", 11),
            (117, f"Nome: PESSOA FICTICIA {seed}", 11),
            (139, "CPF: 000.000.000-00", 11),
            (161, f"Endereco: Rua de Teste, {100 + seed}", 11),
            (205, "CLAUSULAS E CONDICOES CONTRATUAIS", 11),
            (230, "Solicito a emissao do cartao de credito e adiro ao regulamento.", 10),
            (252, "Autorizo o desconto consignado das faturas do cartao.", 10),
            (274, f"Limite de credito: R$ {500 + seed},00. Taxa de juros: 2,00% ao mes.", 10),
            (296, "Local e Data: Salvador, 01/01/2026", 10),
            (318, "Declaro que recebi e aceitei todas as condicoes deste termo.", 10),
            (350, "ACEITE ELETRONICO CONCLUIDO. Atendimento SAC: 0800 000 0000.", 9),
            (520, "Assinado eletronicamente por PESSOA FICTICIA", 8),
            (534, f"Processo: {process_number}", 8),
        ):
            page.insert_text((20, y), text, fontsize=size)
        pii_rects = [
            [word[0] / 420, word[1] / 550, word[2] / 420, word[3] / 550]
            for word in page.get_text("words")
            if 100 < word[1] < 170 and word[4] not in ("Nome:", "CPF:", "Endereco:")
        ]
        footer_rects = [
            [word[0] / 420, word[1] / 550, word[2] / 420, word[3] / 550]
            for word in page.get_text("words") if word[1] > 500
        ]
        if scanned:
            pixmap = page.get_pixmap(dpi=200, alpha=False)
            with pymupdf.open() as raster:
                target = raster.new_page(width=420, height=550)
                target.insert_image(target.rect, pixmap=pixmap)
                target.insert_text((20, 546), f"Processo: {process_number}", fontsize=5)
                footer_rects.extend([
                    [word[0] / 420, word[1] / 550, word[2] / 420, word[3] / 550]
                    for word in target.get_text("words")
                ])
                raster.save(path)
        else:
            pdf.save(path)
    path.chmod(0o600)
    return {
        "document_id": "synthetic-positive",
        "case_id": "synthetic-positive",
        "relative_path": str(path.relative_to(root)),
        "sha256": hash_file(path),
        "page_count": 1,
        "process_number": process_number,
        "fixture_pii_rects": pii_rects,
        "fixture_footer_rects": footer_rects,
    }


def synthetic_checks(root, source, result):
    """Verify known fictitious identifiers and clauses against output pixels independently."""
    import numpy as np
    import pymupdf

    completed = result["status"] == "completed" and len(result["instruments"]) == 1
    checks = {"positive_completed": completed, "personal_pixels_erased": None,
              "footer_pixels_erased": None, "contractual_pixels_preserved": None}
    if not completed:
        return checks
    instrument = result["instruments"][0]
    region = instrument["regions"][0]
    if region.get("rect", [0, 0, 1, 1]) != [0, 0, 1, 1] or region.get("rotation", 0):
        return {**checks, "unsupported_fixture_geometry": True}
    with pymupdf.open(root / source["relative_path"]) as pdf:
        original = render_contract_page(pdf, {"page": 1}, dpi=300)
    with pymupdf.open(instrument["output"]["path"]) as pdf:
        cleaned = render_contract_page(pdf, {"page": 1}, raster_source=True)
    if original.size != cleaned.size:
        return {**checks, "unsupported_fixture_geometry": True}
    checks["personal_pixels_erased"] = bool(source["fixture_pii_rects"]) and all(
        np.all(np.asarray(cleaned.crop(pixel_box(rect, *cleaned.size))) == 255)
        for rect in source["fixture_pii_rects"]
    )
    checks["footer_pixels_erased"] = bool(source["fixture_footer_rects"]) and all(
        np.all(np.asarray(cleaned.crop(pixel_box(rect, *cleaned.size))) == 255)
        for rect in source["fixture_footer_rects"]
    )
    contractual = pixel_box([0, 0.35, 1, 0.66], *original.size)
    checks["contractual_pixels_preserved"] = np.array_equal(
        np.asarray(original.crop(contractual)), np.asarray(cleaned.crop(contractual))
    )
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-root", type=Path, default=Path("data"))
    parser.add_argument("--document-id", action="append")
    parser.add_argument("--synthetic-positive", choices=("native", "scanned"))
    parser.add_argument("--fixture-seed", type=int, default=0)
    parser.add_argument(
        "--phase", choices=("native", "inventory", "detect", "both"), default="inventory"
    )
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--memory-mb", type=int, default=2048)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--context-tokens", type=int, default=32768)
    parser.add_argument("--output-tokens", type=int, default=2048)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.workers < 0 or args.memory_mb < 256 or args.repeat < 1:
        parser.error("workers >= 0, memory-mb >= 256, repeat >= 1")
    if args.synthetic_positive and (args.document_id or args.phase != "both"):
        parser.error("synthetic-positive requires phase both and no document-id")
    if not 0 <= args.fixture_seed <= 999:
        parser.error("fixture-seed must be between 0 and 999")
    if (
        not 2048 <= args.context_tokens <= 131072
        or not 128 <= args.output_tokens < args.context_tokens
    ):
        parser.error("invalid context/output token limits")
    root = args.storage_root.resolve()
    if args.synthetic_positive:
        (root / "pdfs").mkdir(parents=True, exist_ok=True, mode=0o700)
        documents = [{}]
    else:
        connection = sqlite3.connect(
            (root / "state/scraper.sqlite3").as_uri() + "?mode=ro", uri=True
        )
        connection.row_factory = sqlite3.Row
        columns = (
            "SELECT d.document_id,d.case_id,d.relative_path,d.sha256,d.page_count,c.process_number "
            "FROM documents d JOIN cases c ON c.case_id=d.case_id "
        )
        selection = (
            "ORDER BY d.rowid DESC LIMIT 2"
            if not args.document_id
            else f"WHERE d.document_id IN ({','.join('?' for _ in args.document_id)})"
        )
        documents = [
            dict(r)
            for r in connection.execute(columns + selection, args.document_id or ())
        ]
        connection.close()
    if not documents or (args.document_id and len(documents) != len(set(args.document_id))):
        parser.error("document missing in the catalog")
    measurements = []
    for source in documents:
        parallel_workers = []

        def progress(event, workers=parallel_workers):
            if event.get("event") == "inventory_progress":
                workers.append(event["workers"])

        with (
            tempfile.TemporaryDirectory(prefix="adhesion-benchmark-", dir=root) as temporary,
            tempfile.TemporaryDirectory(prefix="benchmark-fixture-", dir=root / "pdfs")
            if args.synthetic_positive
            else nullcontext(None) as fixture_folder,
            workspace_scope(root, Path(temporary).name),
            resource_scope(workers=args.workers, memory_mb=args.memory_mb, progress=progress),
        ):
            if args.synthetic_positive:
                source = synthetic_source(
                    root, Path(fixture_folder), scanned=args.synthetic_positive == "scanned",
                    seed=args.fixture_seed,
                )
            model = None
            try:
                document_started = time.perf_counter()
                model_metadata_seconds = 0.0
                runtime_configuration_seconds = 0.0
                if args.phase in ("detect", "both"):
                    started = time.perf_counter()
                    model = LocalModel(
                        Path(temporary) / "model-cache", model=args.model,
                        context_tokens=args.context_tokens, output_tokens=args.output_tokens,
                    )
                    model_metadata_seconds = time.perf_counter() - started
                started = time.perf_counter()
                if args.phase == "both":
                    neural_engine()
                config = (
                    configuration(
                        model, include_privacy=args.phase == "both", include_reference=False
                    )
                    if args.phase == "both"
                    else None
                )
                runtime_configuration_seconds = time.perf_counter() - started
                for run in range(args.repeat):
                    parallel_workers.clear()
                    run_started = time.perf_counter() if run else document_started
                    started = time.perf_counter()
                    validated = validate_pdf(
                        root / source["relative_path"],
                        expected_sha256=source["sha256"],
                        expected_process_number=source["process_number"],
                    )
                    validation_seconds = time.perf_counter() - started
                    started = time.perf_counter()
                    calls = model.calls if model else 0
                    model_seconds = model.seconds if model else 0.0
                    model_timings = inference_snapshot(model)
                    stats, detection, status = ({}, {}, None)
                    extraction_seconds = anonymization_seconds = None
                    if args.phase == "both":
                        phase_started = time.perf_counter()
                        extraction = extract_document(root, source, model, config, role="scraper")
                        extraction_seconds = time.perf_counter() - phase_started
                        phase_started = time.perf_counter()
                        result = anonymize_extraction(
                            root, extraction["manifest_path"], model, config, role="scraper"
                        )
                        anonymization_seconds = time.perf_counter() - phase_started
                        stats, detection, status = (
                            extraction.get("inventory_stats", {}),
                            extraction["detection"],
                            result["status"],
                        )
                        if run:
                            stats = {
                                "native_reads": 0,
                                "ocr_pages": 0,
                                "ocr_retries": 0,
                                "cache_hits": 0,
                            }
                    else:
                        with open_inventory(root, source) as pages:
                            if args.phase == "native":
                                for page in pages.pdf:
                                    page_inventory(page, DEFAULT_TESSDATA, recognize=False)
                            elif args.phase == "inventory":
                                pages.prefetch(range(1, len(pages) + 1))
                            else:
                                detection = detect_with_model(
                                    model, pages.pdf, pages, pages.attachments
                                )
                            stats = dict(pages.stats)
                    elapsed = time.perf_counter() - started
                    cache_bytes = sum(p.stat().st_size for p in Path(temporary).rglob("*.json"))
                    total_seconds = time.perf_counter() - run_started
                    measurement = {
                        "fixture": args.synthetic_positive,
                        "fixture_seed": args.fixture_seed if args.synthetic_positive else None,
                        "document_id": source["document_id"],
                        "pages": validated.page_count,
                        "phase": args.phase,
                        "cache": "not_used"
                        if args.phase == "native"
                        else ("cold" if run == 0 else "warm"),
                        "workers_requested": args.workers,
                        "worker_limit": worker_limit(
                            args.workers,
                            args.memory_mb,
                            (root / source["relative_path"]).stat().st_size,
                        ),
                        "parallel_workers_used": max(parallel_workers, default=0),
                        "validation_seconds": round(validation_seconds, 4),
                        "processing_seconds": round(elapsed, 4),
                        "extraction_seconds": round(extraction_seconds, 4)
                        if extraction_seconds is not None
                        else None,
                        "anonymization_seconds": round(anonymization_seconds, 4)
                        if anonymization_seconds is not None
                        else None,
                        "model_metadata_seconds": round(model_metadata_seconds, 4)
                        if not run
                        else 0,
                        "runtime_configuration_seconds": round(runtime_configuration_seconds, 4)
                        if not run
                        else 0,
                        "measured_total_seconds": round(total_seconds, 4),
                        "model": args.model if model else None,
                        "neural_ocr_device": config["neural_ocr"]["device"] if config else None,
                        "inference_options": model.options if model else None,
                        "model_calls": model.calls - calls if model else 0,
                        "model_seconds": round(model.seconds - model_seconds, 4) if model else 0,
                        "model_backend_seconds": inference_timings(model, model_timings),
                        "inventory_stats": stats,
                        "cache_bytes": cache_bytes,
                        "instruments": len(detection.get("instruments", [])),
                        "unresolved": len(detection.get("unresolved", [])),
                        "status": status,
                    }
                    if args.synthetic_positive:
                        measurement["fixture_checks"] = synthetic_checks(root, source, result)
                    measurements.append(measurement)
                    print(json.dumps(measurement), flush=True)
            finally:
                if model is not None:
                    model.close()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(measurements, indent=2) + "\n")


if __name__ == "__main__":
    main()
