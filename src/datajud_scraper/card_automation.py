"""Reproducible automatic card-contract extraction and anonymization.

Run without human-provided ranges or masks. Human annotations are a separate validation
input and are never read by the extraction/inference code.
"""

from __future__ import annotations

import argparse
import json
import traceback
import uuid
from pathlib import Path

from .card_assembly import assemble_occurrences, ground_excerpt_regions
from .card_corpus import load_pages
from .card_detection import (
    SCOPE,
    classify_text,
    complete_excerpt_images,
    contract_continuations,
    contractual_page_rect,
    ground_native_excerpt_regions,
    locate_regions,
    needs_visual_segmentation,
    page_text,
    signature_annexes,
)
from .card_model import DEFAULT_MODEL, PROMPT_VERSION, LocalModel, canonical_hash, private_json
from .card_ocr import merge_reading_words, neural_configuration, transform_layout_words
from .card_privacy import anonymize_page, ocr_words
from .contract_catalog import connect_catalog, initialize_contract_schema
from .contract_redaction import render_contract_page, write_cleaned_contract
from .pdf_validation import hash_file
from .runtime import StorageLock, utc_now

PIPELINE_VERSION = "1"
IMPLEMENTATION_FILES = (
    "card_automation.py",
    "card_assembly.py",
    "card_detection.py",
    "card_privacy.py",
    "card_ocr.py",
    "card_model.py",
    "contract_redaction.py",
)
# Bind a long-running batch to the code loaded by this process. Later workspace
# edits must not relabel old imported functions as the new implementation.
IMPLEMENTATION_SHA256 = canonical_hash(
    {name: hash_file(Path(__file__).with_name(name)) for name in IMPLEMENTATION_FILES}
)


def initialize_automation_schema(connection):
    initialize_contract_schema(connection)
    with connection:
        for table in ("cases", "documents"):
            existing = {r[1] for r in connection.execute(f"PRAGMA table_info({table})")}
            for name, declaration in {
                "has_card_contract": "INTEGER CHECK(has_card_contract IN (0,1))",
                "card_contract_count": "INTEGER CHECK(card_contract_count >= 0)",
                "card_contracts_had_sensitive_data": (
                    "INTEGER CHECK(card_contracts_had_sensitive_data IN (0,1))"
                ),
                "card_automation_status": "TEXT NOT NULL DEFAULT 'pending'",
            }.items():
                if name not in existing:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS card_automation_runs (
                run_id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents,
                source_sha256 TEXT NOT NULL, pipeline_version TEXT NOT NULL,
                model_digest TEXT NOT NULL, configuration_sha256 TEXT NOT NULL,
                status TEXT NOT NULL, manifest_path TEXT, manifest_sha256 TEXT,
                started_at TEXT NOT NULL, finished_at TEXT,
                UNIQUE(document_id,source_sha256,configuration_sha256)
            );
            CREATE TABLE IF NOT EXISTS card_automatic_contracts (
                contract_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES card_automation_runs,
                document_id TEXT NOT NULL REFERENCES documents, occurrence INTEGER NOT NULL,
                kind TEXT NOT NULL, source_regions_json TEXT NOT NULL,
                had_sensitive_data INTEGER CHECK(had_sensitive_data IN (0,1)),
                extraction_status TEXT NOT NULL, redaction_status TEXT NOT NULL,
                cleaned_path TEXT, cleaned_sha256 TEXT, page_count INTEGER,
                evidence_json TEXT NOT NULL, UNIQUE(run_id,occurrence)
            );
            CREATE TABLE IF NOT EXISTS card_validation_reviews (
                document_id TEXT NOT NULL REFERENCES documents,
                run_id TEXT NOT NULL REFERENCES card_automation_runs,
                source_sha256 TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
                method TEXT NOT NULL CHECK(method='manual_visual'),
                reviewer TEXT NOT NULL, reviewed_at TEXT NOT NULL,
                source_pages_reviewed INTEGER NOT NULL, output_pages_reviewed INTEGER NOT NULL,
                gold_contracts_json TEXT NOT NULL, assessment_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL, PRIMARY KEY(document_id,run_id)
            );
            CREATE TABLE IF NOT EXISTS card_automatic_releases (
                run_id TEXT PRIMARY KEY REFERENCES card_automation_runs,
                validation_report_sha256 TEXT NOT NULL, manifest_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)


def configuration(model, dpi):
    return {
        "scope": SCOPE,
        "pipeline_version": PIPELINE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model_digest": model.digest,
        "runtime": model.runtime,
        "dpi": dpi,
        "privacy": "word_spans_and_vision_with_residual_pass",
        "ocr": neural_configuration(),
        "implementation_sha256": IMPLEMENTATION_SHA256,
    }


def group_occurrences(predictions, pages):
    return assemble_occurrences(predictions, pages)


def segment_document(model, source_path, pages, attachments, work):
    """Run extraction decisions independently of the more expensive privacy stage."""
    import pymupdf
    from PIL import Image, ImageEnhance

    # Re-read scans before semantic classification. Old PDF OCR can omit small
    # contract continuations or scramble forms; no annotation supplies this text.
    enriched = []
    with pymupdf.open(source_path) as pdf:
        for page in pages:
            source_page = pdf[page["page_number"] - 1]
            page["native_words"] = source_page.get_text("words", sort=True)
            if page.get("text_method") not in ("ocr", "ocr_failed"):
                continue
            pix = source_page.get_pixmap(dpi=180, alpha=False)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            words = ocr_words(image, work.parent / "ocr-cache")
            # A photographed heading can disappear during full-page downscaling.
            # Reread the top band at native resolution before deciding whether a
            # continuation starts a new instrument. This is independent of gold data.
            band_height = max(1, image.height // 4)
            heading = ocr_words(
                image.crop((0, 0, image.width, band_height)), work.parent / "ocr-cache"
            )
            words = merge_reading_words(
                words, transform_layout_words(heading, [0, 0, 1, band_height / image.height])
            )
            if len(" ".join(w[4] for w in words if w[1] < 0.9)) < 120:
                # Sparse photographed continuations can be missed entirely because
                # the paper is dark. Contrast changes OCR input, never output pixels.
                clearer = ocr_words(
                    ImageEnhance.Contrast(image).enhance(2), work.parent / "ocr-cache"
                )
                words = merge_reading_words(words, clearer)
            if words:
                page["words"] = [
                    [
                        *(
                            v * (page["width"] if i % 2 == 0 else page["height"])
                            for i, v in enumerate(w[:4])
                        ),
                        w[4],
                    ]
                    for w in words
                ]
                page["ocr_engine"] = "rapidocr"
            enriched.append({"page": page["page_number"], "words": len(words)})
            private_json(work / "ocr-progress.json", enriched)
    predictions = classify_text(model, pages, attachments)
    signature_annexes(predictions, pages)
    contract_continuations(predictions, pages)
    private_json(work / "text-classification.json", predictions)
    with pymupdf.open(source_path) as pdf:
        for position, page in enumerate(pages):
            number = page["page_number"]
            source_page = pdf[number - 1]
            prediction = predictions[number]
            if not needs_visual_segmentation(page, prediction):
                if prediction["state"] == "card_contract":
                    prediction["regions"] = [
                        {"page": number, "rect": contractual_page_rect(page), "rotation": 0}
                    ]
                continue
            pix = source_page.get_pixmap(dpi=150, alpha=False)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            context = "\n".join(
                f"Neighbor {p['page_number']}: {page_text(p)[:3500]}"
                for p in pages[max(0, position - 4) : position + 2]
                if p["page_number"] != number
                and p["attachment_position"] == page["attachment_position"]
            )
            opening = next(
                p for p in pages if p["attachment_position"] == page["attachment_position"]
            )
            context += (
                f"\nAttachment opening {opening['page_number']} (context only; an attachment "
                f"may contain several instruments): {page_text(opening)[:5000]}"
            )
            visual = locate_regions(model, image, page, context)
            image_boxes = [
                [
                    v / (source_page.rect.width if i % 2 == 0 else source_page.rect.height)
                    for i, v in enumerate(item["bbox"])
                ]
                for item in source_page.get_image_info()
            ]
            visual = ground_excerpt_regions(visual, image_boxes)
            visual = complete_excerpt_images(model, image, visual, image_boxes, page, context)
            visual = ground_native_excerpt_regions(model, visual, source_page)
            predictions[number] = {**prediction, **visual, "basis": "local_model_vision"}
            private_json(work / "segmentation-progress.json", predictions)
    contract_continuations(predictions, pages)
    groups = group_occurrences(predictions, pages)
    private_json(work / "segmentation.json", predictions)
    private_json(work / "groups.json", groups)
    return predictions, groups


def native_crop_layout(page, region):
    """Transform existing PDF text/image boxes into the displayed contract crop."""
    import pymupdf

    crop = region["rect"]
    rotation = region.get("rotation", 0)

    def transform(rect):
        rect = pymupdf.Rect(rect) * page.rotation_matrix
        rect = [
            rect.x0 / page.rect.width,
            rect.y0 / page.rect.height,
            rect.x1 / page.rect.width,
            rect.y1 / page.rect.height,
        ]
        if rect[0] < crop[0] or rect[1] < crop[1] or rect[2] > crop[2] or rect[3] > crop[3]:
            return None
        x0, x1 = [(rect[i] - crop[0]) / (crop[2] - crop[0]) for i in (0, 2)]
        y0, y1 = [(rect[i] - crop[1]) / (crop[3] - crop[1]) for i in (1, 3)]
        return {
            0: [x0, y0, x1, y1],
            90: [1 - y1, x0, 1 - y0, x1],
            180: [1 - x1, 1 - y1, 1 - x0, 1 - y0],
            270: [y0, 1 - x1, y1, 1 - x0],
        }[rotation]

    # Word-level proportional interpolation is inaccurate for variable-width PDF
    # fonts, especially a signature hash fused to its label. Match actual glyphs
    # spatially: get_text(sort=True) block/line numbers are not rawdict indices.
    raw = page.get_text("rawdict", flags=pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
    lines = [
        (line["bbox"], [char for span in line["spans"] for char in span["chars"]])
        for block in raw["blocks"]
        if block["type"] == 0
        for line in block["lines"]
    ]
    words, images = [], []
    for word in page.get_text("words", sort=True):
        rect = transform(word[:4])
        if rect:
            chars = [
                char
                for bbox, line_chars in lines
                if min(bbox[2], word[2]) > max(bbox[0], word[0])
                and min(bbox[3], word[3]) > max(bbox[1], word[1])
                for char in line_chars
                if word[0] - 0.01 <= (char["bbox"][0] + char["bbox"][2]) / 2 <= word[2] + 0.01
                and word[1] - 0.01 <= (char["bbox"][1] + char["bbox"][3]) / 2 <= word[3] + 0.01
            ]
            glyphs = [transform(char["bbox"]) for char in chars]
            item = [*rect, word[4]]
            if "".join(char["c"] for char in chars) == word[4] and all(glyphs):
                item.append({"glyphs": glyphs})
            words.append(item)
    for item in page.get_image_info():
        rect = transform(item["bbox"])
        if rect:
            images.append(rect)
    return words, images


def save_result(connection, source, manifest, manifest_path, manifest_hash):
    contracts = manifest["contracts"]
    # Pending/error must remain unknown, not turn into a false negative.
    complete = manifest["status"] == "automatic_checks_passed"
    has_contract = 1 if contracts else (0 if complete else None)
    count = len(contracts) if complete else None
    sensitive = (
        int(any(c["had_sensitive_data"] for c in contracts)) if contracts and complete else None
    )
    with connection:
        connection.execute(
            "UPDATE card_automation_runs SET status=?,manifest_path=?,manifest_sha256=?,"
            "finished_at=? WHERE run_id=?",
            (
                manifest["status"],
                str(manifest_path),
                manifest_hash,
                utc_now().isoformat(),
                manifest["run_id"],
            ),
        )
        for occurrence, contract in enumerate(contracts, 1):
            output = contract["output"]
            connection.execute(
                """INSERT OR REPLACE INTO card_automatic_contracts VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    contract["contract_id"],
                    manifest["run_id"],
                    source["document_id"],
                    occurrence,
                    contract["kind"],
                    json.dumps(contract["regions"]),
                    int(contract["had_sensitive_data"]),
                    "automatic",
                    contract["status"],
                    output["path"],
                    output["sha256"],
                    output["page_count"],
                    json.dumps(contract["privacy"], ensure_ascii=False),
                ),
            )
        values = (has_contract, count, sensitive, manifest["status"])
        connection.execute(
            "UPDATE documents SET has_card_contract=?,card_contract_count=?,"
            "card_contracts_had_sensitive_data=?,card_automation_status=? WHERE document_id=?",
            (*values, source["document_id"]),
        )
        newest = connection.execute(
            "SELECT document_id FROM documents WHERE case_id=? AND validation_status='valid' "
            "ORDER BY retrieved_at DESC,document_id DESC LIMIT 1",
            (source["case_id"],),
        ).fetchone()
        if newest and newest[0] == source["document_id"]:
            connection.execute(
                "UPDATE cases SET has_card_contract=?,card_contract_count=?,"
                "card_contracts_had_sensitive_data=?,card_automation_status=? WHERE case_id=?",
                (*values, source["case_id"]),
            )


def run_document(root: Path, document_id: str, model, *, dpi=240):
    import pymupdf

    if not 150 <= dpi <= 600:
        raise ValueError("resolucion fuera del intervalo 150..600")
    with connect_catalog(root / "state/scraper.sqlite3") as connection:
        initialize_automation_schema(connection)
        row = connection.execute(
            "SELECT d.*,s.inventory_path,s.source_sha256,s.ocr_failures "
            "FROM documents d JOIN contract_sources s USING(document_id) WHERE document_id=?",
            (document_id,),
        ).fetchone()
        if not row:
            raise ValueError("documento sin inventario")
        source = dict(row)
        if source["source_disposition"] != "retained" or not source["inventory_path"]:
            raise ValueError("original descartado en el flujo anterior; no se puede reprocesar")
        source_path = root / source["relative_path"]
        if (
            hash_file(source_path) != source["sha256"]
            or source["source_sha256"] != source["sha256"]
        ):
            raise ValueError("original e inventario no corresponden a la misma huella")
        config = configuration(model, dpi)
        config_hash = canonical_hash(config)
        run_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"card:{document_id}:{source['sha256']}:{config_hash}")
        )
        work = root / "card-work" / document_id / run_id
        # Keep PII-bearing model answers under their source, so the final retention
        # policy can remove all sensitive derivatives of that document together.
        model.cache = root / "card-work" / document_id / "inference-cache"
        manifest_path = work / "manifest.json"
        prior = connection.execute(
            "SELECT * FROM card_automation_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if prior and prior["status"] in ("automatic_checks_passed", "needs_review"):
            if hash_file(Path(prior["manifest_path"])) != prior["manifest_sha256"]:
                raise ValueError("el manifiesto automatico ha cambiado")
            manifest = json.loads(manifest_path.read_text())
            for contract in manifest["contracts"]:
                if hash_file(Path(contract["output"]["path"])) != contract["output"]["sha256"]:
                    raise ValueError("salida automatica modificada")
            return manifest
        work.mkdir(parents=True, exist_ok=True, mode=0o700)
        with connection:
            connection.execute(
                "INSERT INTO card_automation_runs VALUES (?,?,?,?,?,?,'running',NULL,"
                "NULL,?,NULL) ON CONFLICT(run_id) DO UPDATE SET status='running',finished_at=NULL",
                (
                    run_id,
                    document_id,
                    source["sha256"],
                    PIPELINE_VERSION,
                    model.digest,
                    config_hash,
                    utc_now().isoformat(),
                ),
            )
        manifest = {
            "run_id": run_id,
            "document_id": document_id,
            "source_sha256": source["sha256"],
            "configuration": config,
            "status": "running",
            "contracts": [],
            "manual_validation": "pending",
            "source_retained": True,
        }
        try:
            inventory_path = Path(source["inventory_path"])
            pages = load_pages(inventory_path)
            metadata = json.loads(inventory_path.with_name("inventory.json").read_text())
            if {p["page_number"] for p in pages} != set(
                range(1, source["page_count"] + 1)
            ) or metadata["source_sha256"] != source["sha256"]:
                raise ValueError("inventario incompleto o de otra fuente")
            predictions, groups = segment_document(
                model, source_path, pages, metadata["attachments"], work
            )
            unresolved = [
                n
                for n, p in predictions.items()
                if p["state"] == "uncertain"
                or (p.get("confidence") is not None and p["confidence"] < 0.85)
            ]
            manifest["source_pages"] = len(pages)
            manifest["unresolved_pages"] = unresolved
            manifest["automatic_page_decisions"] = len(predictions)
            for occurrence, group in enumerate(groups, 1):
                contract_id = str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:{canonical_hash(group)}")
                )
                contract_work = work / contract_id
                privacy_path = contract_work / "privacy.json"
                if privacy_path.exists():
                    privacy = json.loads(privacy_path.read_text())
                else:
                    privacy = []
                    with pymupdf.open(source_path) as pdf:
                        for page_number, region in enumerate(group["regions"], 1):
                            checkpoint = contract_work / f"privacy-page-{page_number:05d}.json"
                            if checkpoint.exists():
                                decision = json.loads(checkpoint.read_text())
                            else:
                                image = render_contract_page(pdf, region, dpi=dpi)
                                native_words, image_regions = native_crop_layout(
                                    pdf[region["page"] - 1], region
                                )
                                decision = anonymize_page(
                                    model,
                                    image,
                                    work / "ocr-cache",
                                    native_words=native_words,
                                    image_regions=image_regions,
                                )
                                private_json(checkpoint, decision)
                            privacy.append({"page": page_number, **decision})
                    private_json(privacy_path, privacy)
                evidence_path = contract_work / "output-evidence.json"
                if evidence_path.exists():
                    output = json.loads(evidence_path.read_text())
                    if hash_file(Path(output["path"])) != output["sha256"]:
                        raise ValueError("salida cacheada modificada")
                else:
                    output = write_cleaned_contract(
                        source_path,
                        contract_work / "cleaned.pdf",
                        group["regions"],
                        {p["page"]: p["masks"] for p in privacy},
                        dpi=dpi,
                        decision_mode="automatic",
                    )
                    private_json(evidence_path, output)
                manifest["contracts"].append(
                    {
                        "contract_id": contract_id,
                        "occurrence": occurrence,
                        **group,
                        "had_sensitive_data": any(p["had_sensitive_data"] for p in privacy),
                        "status": "automatic_checks_passed"
                        if all(p["status"] == "automatic_checks_passed" for p in privacy)
                        else "needs_review",
                        "privacy": privacy,
                        "output": output,
                    }
                )
                private_json(work / "progress.json", manifest)
            manifest["status"] = (
                "needs_review"
                if unresolved
                or any(c["status"] != "automatic_checks_passed" for c in manifest["contracts"])
                else "automatic_checks_passed"
            )
            if hash_file(source_path) != source["sha256"]:
                raise ValueError("el original cambio durante el procesamiento")
            private_json(manifest_path, manifest)
            save_result(connection, source, manifest, manifest_path, hash_file(manifest_path))
            return manifest
        except BaseException as exc:
            manifest["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "error"
            manifest["error_type"] = type(exc).__name__
            private_json(work / "error.json", manifest)
            with connection:
                connection.execute(
                    "UPDATE card_automation_runs SET status=?,finished_at=? WHERE run_id=?",
                    (manifest["status"], utc_now().isoformat(), run_id),
                )
                connection.execute(
                    "UPDATE documents SET card_automation_status=? WHERE document_id=?",
                    (manifest["status"], document_id),
                )
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-root", type=Path, default=Path("data"))
    select = parser.add_mutually_exclusive_group(required=True)
    select.add_argument("--document")
    select.add_argument("--manifest", type=Path, help="manifiesto de corpus o validacion")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--dpi", type=int, default=240)
    args = parser.parse_args()
    root = args.storage_root.resolve()
    documents = (
        [args.document]
        if args.document
        else [
            r["document_id"]
            for r in json.loads(args.manifest.read_text())["documents"]
            if r.get("available", True)
        ]
    )
    model = LocalModel(root / "card-work/model-cache", endpoint=args.endpoint, model=args.model)
    failures = 0
    try:
        with StorageLock(root / "state/card-automation.lock", timeout_seconds=0):
            with connect_catalog(root / "state/scraper.sqlite3") as catalog:
                initialize_automation_schema(catalog)
                with catalog:
                    catalog.execute(
                        "UPDATE card_automation_runs SET status='interrupted' "
                        "WHERE status='running'"
                    )
            for position, document_id in enumerate(documents, 1):
                print(
                    json.dumps(
                        {
                            "event": "started",
                            "position": position,
                            "total": len(documents),
                            "document_id": document_id,
                        }
                    ),
                    flush=True,
                )
                try:
                    result = run_document(root, document_id, model, dpi=args.dpi)
                    print(
                        json.dumps(
                            {
                                "event": "completed",
                                "document_id": document_id,
                                "status": result["status"],
                                "contracts": len(result["contracts"]),
                                "calls": model.calls,
                                "cache_hits": model.cache_hits,
                            }
                        ),
                        flush=True,
                    )
                except Exception:
                    failures += 1
                    traceback.print_exc()
                    print(json.dumps({"event": "error", "document_id": document_id}), flush=True)
    finally:
        model.close()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
