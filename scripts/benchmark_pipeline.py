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
from datajud_scraper.adhesion.pipeline import anonymize_extraction, configuration, extract_document
from datajud_scraper.adhesion.resources import resource_scope, worker_limit
from datajud_scraper.adhesion.text import DEFAULT_TESSDATA, page_inventory
from datajud_scraper.adhesion.vision import detect_with_model
from datajud_scraper.pdf_validation import validate_pdf


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-root", type=Path, default=Path("data"))
    parser.add_argument("--document-id", action="append")
    parser.add_argument(
        "--phase", choices=("native", "inventory", "detect", "both"), default="inventory"
    )
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--memory-mb", type=int, default=2048)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.workers < 0 or args.memory_mb < 256 or args.repeat < 1:
        parser.error("workers >= 0, memory-mb >= 256, repeat >= 1")
    root = args.storage_root.resolve()
    connection = sqlite3.connect((root / "state/scraper.sqlite3").as_uri() + "?mode=ro", uri=True)
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
        for r in connection.execute(
            columns + selection,
            args.document_id or (),
        )
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
            workspace_scope(root, Path(temporary).name),
            resource_scope(workers=args.workers, memory_mb=args.memory_mb, progress=progress),
        ):
            model = None
            try:
                document_started = time.perf_counter()
                model_metadata_seconds = 0.0
                runtime_configuration_seconds = 0.0
                if args.phase in ("detect", "both"):
                    started = time.perf_counter()
                    model = LocalModel(Path(temporary) / "model-cache", model=args.model)
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
                    if args.phase == "both":
                        extraction = extract_document(root, source, model, config, role="scraper")
                        result = anonymize_extraction(
                            root, extraction["manifest_path"], model, config, role="scraper"
                        )
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
                        "model_metadata_seconds": round(model_metadata_seconds, 4)
                        if not run
                        else 0,
                        "runtime_configuration_seconds": round(runtime_configuration_seconds, 4)
                        if not run
                        else 0,
                        "measured_total_seconds": round(total_seconds, 4),
                        "model": args.model if model else None,
                        "model_calls": model.calls - calls if model else 0,
                        "model_seconds": round(model.seconds - model_seconds, 4) if model else 0,
                        "model_backend_seconds": inference_timings(model, model_timings),
                        "inventory_stats": stats,
                        "cache_bytes": cache_bytes,
                        "instruments": len(detection.get("instruments", [])),
                        "unresolved": len(detection.get("unresolved", [])),
                        "status": status,
                    }
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
