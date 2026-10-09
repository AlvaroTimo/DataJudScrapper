"""Resumable extraction with frozen configuration and separate accepted/quarantine PDFs."""

from __future__ import annotations

import importlib.metadata
import json
import sqlite3
from pathlib import Path

from ..pdf_validation import hash_file
from .common import digest, now, read_json, workspace, write_json
from .inventory import extraction_configuration, open_inventory
from .privacy import anonymize
from .vision import LocalModel, detect_with_model


def extraction_config(model):
    package = Path(__file__).parent
    names = (
        "stages",
        "text",
        "inventory",
        "pdf",
        "vision",
        "boundaries",
        "routing",
        "detection",
        "common",
        "local_model",
        "resources",
    )
    return {
        "phase": "extraction-v1",
        "dpi": 300,
        "code": {f"{name}.py": hash_file(package / f"{name}.py") for name in names},
        "packages": {name: importlib.metadata.version(name) for name in ("pymupdf", "pillow")},
        "page_inventory": extraction_configuration(),
        "model": model.model,
        "model_digest": model.digest,
        "runtime": model.runtime,
        "inference": getattr(model, "options", None),
    }


def configuration(model, *, include_privacy=True, include_reference=True):
    package = Path(__file__).resolve().parents[1]
    code = {
        str(p.relative_to(package)): hash_file(p)
        for p in sorted(package.rglob("*"))
        if p.is_file() and p.suffix in (".py", ".json")
    }
    package_names = (
        ("pymupdf", "pillow", "opencv-python", "rapidocr", "onnxruntime")
        if include_privacy
        else ("pymupdf", "pillow")
    )
    versions = {name: importlib.metadata.version(name) for name in package_names}
    from ..ocr_models import MODEL_ROOT
    from .local_model import REFERENCE_MODEL
    from .ocr import neural_configuration, table_configuration

    installed = {m["name"]: m["digest"] for m in model.client.get("/api/tags").json()["models"]}
    if include_reference and REFERENCE_MODEL not in installed:
        raise ValueError("independent visual reference model is not installed")

    config = {
        "pipeline": "complete_card_adhesion_phases_v3",
        "extraction": extraction_config(model),
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
        "inference": getattr(model, "options", None),
        "tesseract": table_configuration() if include_privacy else None,
        "reference_model": REFERENCE_MODEL,
        "reference_model_digest": installed.get(REFERENCE_MODEL) if include_reference else None,
        "neural_ocr": neural_configuration() if include_privacy else None,
        "tesseract_models": {
            str(p.relative_to(MODEL_ROOT)): hash_file(p)
            for p in sorted(MODEL_ROOT.rglob("*.traineddata"))
        },
        "seed": 20260920,
        "max_privacy_repairs": 1,
    }
    config["anonymization"] = (
        {
            "phase": "anonymization-v1",
            "dpi": 300,
            "code": {
                name: code[f"adhesion/{name}.py"]
                for name in (
                    "stages",
                    "privacy",
                    "ocr",
                    "pdf",
                    "common",
                    "local_model",
                    "inventory",
                    "vision",
                    "text",
                )
            },
            "packages": versions,
            "model": model.model,
            "model_digest": model.digest,
            "runtime": model.runtime,
            "inference": getattr(model, "options", None),
            "tesseract": config["tesseract"],
            "neural_ocr": config["neural_ocr"],
            "tesseract_models": config["tesseract_models"],
        }
        if include_privacy
        else None
    )
    return config


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


def authorize_source(root, source, config, role):
    work = workspace(root)
    holdout_path = work / "holdout.json"
    reserved = (
        {r["document_id"] for r in read_json(holdout_path)["documents"]}
        if holdout_path.exists()
        else set()
    )
    if source["document_id"] in reserved and role == "development":
        raise ValueError("reserved holdout cannot be used for development")
    if role == "holdout":
        check_frozen(root, config)
        reference = read_json(work / "reference" / source["document_id"] / "gold.json")
        if (
            not reference.get("complete")
            or reference["source_sha256"] != source["sha256"]
            or reference["configuration_sha256"] != digest(config)
            or reference["packet_sha256"]
            != hash_file(work / "reference" / source["document_id"] / "packet.json")
        ):
            raise ValueError("independent source reference is not complete")


def extract_document(root, source, model, config, *, role="development"):
    from .stages import extract_document as extract

    authorize_source(root, source, config, role)
    return extract(
        root,
        source,
        model,
        config,
        role=role,
        detector=detect_with_model,
        inventory_factory=open_inventory,
    )


def anonymize_extraction(root, extraction_path, model, config, *, role=None):
    from .stages import anonymize_extraction as clean

    extraction_path = Path(extraction_path)
    extraction = read_json(extraction_path)
    role = role or extraction["role"]
    authorize_source(root, extraction["source"], config, role)
    signature = digest(config)
    run_id = digest(
        [
            extraction["document_id"],
            extraction["source_sha256"],
            signature,
            hash_file(extraction_path),
        ]
    )[:24]
    with state_connection(root) as connection:
        connection.execute(
            "INSERT INTO runs VALUES(?,?,?,?,?,'running',NULL,NULL,?,NULL) "
            "ON CONFLICT(document_id,source_sha256,configuration_sha256) DO UPDATE SET "
            "run_id=excluded.run_id,status='running',role=excluded.role,"
            "manifest_path=NULL,manifest_sha256=NULL,started_at=excluded.started_at,finished_at=NULL",
            (
                run_id,
                extraction["document_id"],
                extraction["source_sha256"],
                signature,
                role,
                now(),
            ),
        )
    try:
        result = clean(root, extraction_path, model, config, role=role, anonymizer=anonymize)
        path = Path(result["manifest_path"])
    except BaseException:
        path = workspace(root) / "runs" / extraction["document_id"] / run_id / "error.json"
        with state_connection(root) as connection:
            connection.execute(
                "UPDATE runs SET status='error',finished_at=?,manifest_path=?,"
                "manifest_sha256=? WHERE run_id=?",
                (
                    now(),
                    str(path) if path.exists() else None,
                    hash_file(path) if path.exists() else None,
                    run_id,
                ),
            )
        raise
    with state_connection(root) as connection:
        connection.execute(
            "UPDATE runs SET status=?,manifest_path=?,manifest_sha256=?,finished_at=? "
            "WHERE run_id=?",
            (result["status"], str(path), hash_file(path), now(), run_id),
        )
    return result


def run_document(root, source, model, config, *, role="development"):
    extraction = extract_document(root, source, model, config, role=role)
    result = anonymize_extraction(root, extraction["manifest_path"], model, config, role=role)
    return {
        **result,
        "phases": {
            "extraction": {
                "status": extraction["status"],
                "manifest_path": extraction["manifest_path"],
                "manifest_sha256": extraction["manifest_sha256"],
            },
            "anonymization": {"status": result["status"], "manifest_path": result["manifest_path"]},
        },
    }


def run_manifest(root, manifest_path, *, phase="run"):
    work = workspace(root)
    selection = read_json(manifest_path)
    if phase not in ("run", "extract"):
        raise ValueError("phase must be run or extract")
    role = selection.get("role", "corpus")
    model = LocalModel(work / "model-cache", timeout=180)
    try:
        config = configuration(
            model,
            include_privacy=phase == "run" or role in ("holdout", "corpus"),
            include_reference=role in ("holdout", "corpus"),
        )
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
                runner = extract_document if phase == "extract" else run_document
                result = runner(root, source, model, config, role=role)
                print(
                    json.dumps(
                        {
                            "document_id": source["document_id"],
                            "status": result["status"],
                            "instruments": len(result["instruments"]),
                            "run_id": result.get("run_id", result.get("extraction_id")),
                            "phase": result["phase"],
                            "manifest_path": result["manifest_path"],
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
