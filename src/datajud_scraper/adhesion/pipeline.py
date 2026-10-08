"""Resumable extraction with frozen configuration and separate accepted/quarantine PDFs."""

from __future__ import annotations

import importlib.metadata
import json
import sqlite3
from pathlib import Path

from ..pdf_validation import hash_file
from ..runtime import StorageLock
from .common import digest, now, read_json, source_path, workspace, write_json
from .inventory import extraction_configuration, open_inventory
from .pdf import region_inventory, render_contract_page, write_cleaned_contract
from .privacy import anonymize
from .vision import LocalModel, detect_with_model


def configuration(model):
    package = Path(__file__).resolve().parents[1]
    code = {
        str(p.relative_to(package)): hash_file(p)
        for p in sorted(package.rglob("*"))
        if p.is_file() and p.suffix in (".py", ".json")
    }
    versions = {
        name: importlib.metadata.version(name)
        for name in ("pymupdf", "pillow", "opencv-python", "rapidocr", "onnxruntime")
    }
    from ..ocr_models import MODEL_ROOT
    from .local_model import REFERENCE_MODEL
    from .ocr import neural_configuration, table_configuration

    installed = {m["name"]: m["digest"] for m in model.client.get("/api/tags").json()["models"]}
    if REFERENCE_MODEL not in installed:
        raise ValueError("independent visual reference model is not installed")

    return {
        "pipeline": "complete_card_adhesion_index_v2",
        "page_inventory": extraction_configuration(),
        "detection": {
            "routing": "index-content-v1",
            "fallback": "exhaustive_residual",
            "boundary_verification": "adjacent_visual_v1",
            "measured_regions": "v1",
        },
        "dpi": 300,
        "code": code,
        "packages": versions,
        "model": model.model,
        "model_digest": model.digest,
        "runtime": model.runtime,
        "tesseract": table_configuration(),
        "reference_model": REFERENCE_MODEL,
        "reference_model_digest": installed[REFERENCE_MODEL],
        "neural_ocr": neural_configuration(),
        "tesseract_models": {
            str(p.relative_to(MODEL_ROOT)): hash_file(p)
            for p in sorted(MODEL_ROOT.rglob("*.traineddata"))
        },
        "seed": 20260920,
        "max_privacy_repairs": 1,
    }


def freeze(root, model):
    work = workspace(root)
    path = work / "frozen-configuration.json"
    config = configuration(model)
    result = {
        "created_at": now(),
        "configuration": config,
        "configuration_sha256": digest(config),
        "selection_sha256": read_json(work / "holdout.json")["selection_sha256"],
    }
    if path.exists():
        old = read_json(path)
        if old["configuration_sha256"] != result["configuration_sha256"]:
            raise ValueError("configuration already frozen; create a new evaluation cohort")
        return old
    package = Path(__file__).resolve().parents[1]
    snapshot = work / "frozen-code" / result["configuration_sha256"]
    for relative, expected in config["code"].items():
        source = package / relative
        if hash_file(source) != expected:
            raise ValueError("source code changed while freezing")
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(source.read_bytes())
        target.chmod(0o600)
    result["code_snapshot"] = str(snapshot)
    write_json(path, result)
    return result


def check_frozen(root, config):
    frozen = read_json(workspace(root) / "frozen-configuration.json")
    if frozen["configuration_sha256"] != digest(config):
        raise ValueError("code/model/configuration changed after freezing")
    holdout = read_json(workspace(root) / "holdout.json")
    if (
        frozen["selection_sha256"] != holdout["selection_sha256"]
        or digest(holdout["documents"]) != holdout["selection_sha256"]
        or len(holdout["documents"]) != 25
    ):
        raise ValueError("holdout selection changed")
    return frozen


def state_connection(root):
    path = workspace(root) / "state.sqlite3"
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("""CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, source_sha256 TEXT NOT NULL,
        configuration_sha256 TEXT NOT NULL, role TEXT NOT NULL,
        status TEXT NOT NULL, manifest_path TEXT, manifest_sha256 TEXT,
        started_at TEXT NOT NULL, finished_at TEXT,
        UNIQUE(document_id,source_sha256,configuration_sha256))""")
    connection.commit()
    return connection


def verify_manifest(path):
    manifest = read_json(path)
    for item in manifest["instruments"]:
        if hash_file(Path(item["output"]["path"])) != item["output"]["sha256"]:
            raise ValueError("output PDF changed")
    return manifest


def run_document(root, source, model, config, *, role="development"):
    import pymupdf

    root, work = Path(root).resolve(), workspace(root)
    reserved = {r["document_id"] for r in read_json(work / "holdout.json")["documents"]}
    if source["document_id"] in reserved and role == "development":
        raise ValueError("reserved holdout cannot be used for development")
    original = source_path(root, source)
    signature = digest(config)
    config_path = work / "configurations" / f"{signature}.json"
    if not config_path.exists():
        write_json(config_path, config)
    run_id = digest([source["document_id"], source["sha256"], signature])[:24]
    folder = work / "runs" / source["document_id"] / run_id
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest_path = folder / "manifest.json"
    if role == "holdout":
        check_frozen(root, config)
        reference = read_json(work / "reference" / source["document_id"] / "gold.json")
        if (
            not reference.get("complete")
            or reference["source_sha256"] != source["sha256"]
            or reference["configuration_sha256"] != signature
            or reference["packet_sha256"]
            != hash_file(work / "reference" / source["document_id"] / "packet.json")
        ):
            raise ValueError("independent source reference is not complete")
    with StorageLock(folder / "run.lock", timeout_seconds=0):
        if manifest_path.exists():
            return verify_manifest(manifest_path)
        with state_connection(root) as connection:
            connection.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?,'running',NULL,NULL,?,NULL) "
                "ON CONFLICT(run_id) DO UPDATE SET status='running',finished_at=NULL",
                (run_id, source["document_id"], source["sha256"], signature, role, now()),
            )
        manifest = {
            "document_id": source["document_id"],
            "source_sha256": source["sha256"],
            "source_pages": source["page_count"],
            "run_id": run_id,
            "configuration_sha256": signature,
            "role": role,
            "started_at": now(),
            "instruments": [],
            "status": "error",
        }
        try:
            with pymupdf.open(original) as pdf, open_inventory(root, source, pdf=pdf) as pages:
                attachments = pages.attachments
                detection_path = folder / "detection.json"
                if detection_path.exists():
                    detection = read_json(detection_path)
                else:
                    detection = detect_with_model(model, pdf, pages, attachments)
                    write_json(detection_path, detection)
                manifest["detection"] = detection
                for position, instrument in enumerate(detection["instruments"], 1):
                    contract_id = f"{run_id}-{position:03d}"
                    contract_folder = folder / contract_id
                    regions = instrument.get(
                        "regions", [{"page": n, "rect": [0, 0, 1, 1]} for n in instrument["pages"]]
                    )
                    if [r["page"] for r in regions] != instrument["pages"]:
                        raise ValueError("contract regions do not match source pages")
                    decisions, masks = [], {}
                    for output_number, region in enumerate(regions, 1):
                        number = region["page"]
                        region_hash = digest(region)
                        page_folder = contract_folder / f"source-{number:05d}-{region_hash[:12]}"
                        decision_path = page_folder / "privacy.json"
                        if decision_path.exists():
                            decision = read_json(decision_path)
                            if decision.get("region_sha256") != region_hash:
                                raise ValueError("privacy cache belongs to another contract region")
                        else:
                            page = {**pages[number - 1], "family": instrument["family"]}
                            image, geometry = render_contract_page(
                                pdf, region, dpi=300, with_geometry=True
                            )
                            if region.get("rect", [0, 0, 1, 1]) != [0, 0, 1, 1] or region.get(
                                "rotation", 0
                            ):
                                page = region_inventory(page, region, geometry)
                            decision = anonymize(model, image, page, page_folder)
                            decision = {
                                **decision,
                                "source_region": region,
                                "region_sha256": region_hash,
                            }
                            write_json(decision_path, decision)
                        decisions.append(decision)
                        masks[output_number] = decision["masks"]
                    accepted = all(d["status"] == "completed" for d in decisions)
                    output = work / ("development-outputs" if role == "development" else "outputs")
                    output = (
                        output / signature / ("accepted" if accepted else "quarantine") / run_id
                    )
                    output = output / f"{contract_id}.pdf"
                    output_cache = contract_folder / "output.json"
                    if output_cache.exists():
                        result = read_json(output_cache)
                        if hash_file(Path(result["path"])) != result["sha256"]:
                            raise ValueError("previous output changed during resumption")
                    else:
                        if output.exists():
                            # Preserve a file linked just before an interrupted evidence write.
                            import uuid

                            orphan = contract_folder / f"interrupted-{uuid.uuid4().hex}.pdf"
                            output.rename(orphan)
                        result = write_cleaned_contract(
                            original, output, regions, masks, dpi=300, decision_mode="automatic"
                        )
                        write_json(output_cache, result)
                    record = {
                        **instrument,
                        "contract_id": contract_id,
                        "status": "completed" if accepted else "needs_review",
                        "output": result,
                        "privacy": decisions,
                    }
                    manifest["instruments"].append(record)
                    write_json(folder / "progress.json", manifest)
                if hasattr(pages, "stats"):
                    manifest["inventory_stats"] = dict(pages.stats)
            if (
                detection["unresolved"]
                or not detection.get("fallback_complete", True)
                or any(i["status"] != "completed" for i in manifest["instruments"])
            ):
                status = "needs_review"
            else:
                status = "completed" if manifest["instruments"] else "no_target"
            manifest.update(status=status, finished_at=now())
            write_json(manifest_path, manifest)
            with state_connection(root) as connection:
                connection.execute(
                    "UPDATE runs SET status=?,manifest_path=?,manifest_sha256=?,"
                    "finished_at=? WHERE run_id=?",
                    (status, str(manifest_path), hash_file(manifest_path), now(), run_id),
                )
            return manifest
        except BaseException as exc:
            manifest.update(status="error", error_type=type(exc).__name__, finished_at=now())
            error_path = folder / "error.json"
            write_json(error_path, manifest)
            with state_connection(root) as connection:
                connection.execute(
                    "UPDATE runs SET status='error',finished_at=?,manifest_path=?,"
                    "manifest_sha256=? WHERE run_id=?",
                    (now(), str(error_path), hash_file(error_path), run_id),
                )
            raise


def run_manifest(root, manifest_path):
    work = workspace(root)
    selection = read_json(manifest_path)
    role = selection.get("role", "corpus")
    model = LocalModel(work / "model-cache", timeout=180)
    try:
        config = configuration(model)
        if role in ("holdout", "corpus"):
            check_frozen(root, config)
        if role == "corpus":
            from .evaluation import evaluate

            if not evaluate(root)["passed"]:
                raise ValueError("full corpus blocked: independent pilot has not met 80% targets")
        errors = 0
        for source in selection["documents"]:
            if not source.get("available", True):
                continue
            try:
                result = run_document(root, source, model, config, role=role)
                print(
                    json.dumps(
                        {
                            "document_id": source["document_id"],
                            "status": result["status"],
                            "instruments": len(result["instruments"]),
                            "run_id": result["run_id"],
                        }
                    ),
                    flush=True,
                )
            except Exception as exc:
                errors += 1
                print(
                    json.dumps(
                        {
                            "document_id": source["document_id"],
                            "status": "error",
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        }
                    ),
                    flush=True,
                )
    finally:
        model.close()
    return 1 if errors else 0
