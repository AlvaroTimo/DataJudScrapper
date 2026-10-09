"""Per-document scraper integration; evaluation cohorts remain separate."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from .adhesion.common import digest, read_json, workspace, workspace_scope, write_json
from .adhesion.local_model import BACKEND_DURATIONS, inference_snapshot, inference_timings
from .adhesion.resources import resource_scope
from .adhesion.stages import load_phase, managed, verified_artifact
from .errors import PauseError
from .pdf_validation import hash_file
from .runtime import StorageLock


def owned_path(root, path, parent):
    """Cleanup only owns canonical paths within this document's artifact folders."""
    original = Path(path).absolute()
    resolved = managed(root, original)
    if resolved != original or not resolved.is_relative_to(Path(parent).resolve()):
        raise ValueError("cleanup artifact outside its owned folder or through a symlink")
    return resolved


class ContractProcessor:
    WORKSPACE = "adhesion-scraper"

    def __init__(self, config, logger):
        self.config, self.logger = config, logger
        self.root = config.storage_root
        self.model = None
        self.signature = None
        self.pipeline_config = None
        self.progress = None

    def _progress(self, event):
        if self.logger is not None:
            self.logger.emit(event["event"], **{k: v for k, v in event.items() if k != "event"})
        if self.progress:
            self.progress(event)

    def prepare(self):
        if self.model is not None:
            return
        from .adhesion.inventory import extraction_configuration
        from .adhesion.local_model import LocalModel
        from .adhesion.pipeline import configuration

        try:
            with workspace_scope(self.root, self.WORKSPACE):
                profile = extraction_configuration()
                if set(profile["models"]) != {"por.traineddata", "eng.traineddata"}:
                    raise ValueError("OCR models missing")
                if self.config.contract_mode == "both":
                    from .adhesion.ocr import neural_engine
                    from .ocr_models import MODEL_ROOT

                    if any(
                        not (MODEL_ROOT / "best" / name).is_file()
                        for name in ("por.traineddata", "eng.traineddata")
                    ):
                        raise ValueError("privacy OCR language models missing")
                    neural_engine()
                model = LocalModel(
                    workspace(self.root) / "model-cache" / "preflight",
                    model=self.config.local_model,
                    context_tokens=self.config.local_context_tokens,
                    output_tokens=self.config.local_output_tokens,
                    progress=self._progress,
                )
                try:
                    config = configuration(
                        model,
                        include_privacy=self.config.contract_mode == "both",
                        include_reference=False,
                    )
                except BaseException:
                    model.close()
                    raise
                self.model, self.pipeline_config = model, config
                self.signature = digest(
                    config if self.config.contract_mode == "both" else config["extraction"]
                )
        except Exception as exc:
            raise PauseError(
                "contract_processing_unavailable",
                "el procesamiento local no esta disponible; instale el extra contracts, "
                f"los modelos OCR y el modelo local {self.config.local_model} antes de descargar, "
                "o seleccione --contract-mode none",
            ) from exc

    def close(self):
        if self.model is not None:
            self.model.close()
            self.model = None

    def cached(self, stored):
        self.prepare()
        with (
            workspace_scope(self.root, self.WORKSPACE),
            resource_scope(
                workers=self.config.ocr_workers,
                memory_mb=self.config.ocr_memory_mb,
                progress=self._progress,
            ),
        ):
            path = workspace(self.root) / "scraper-releases" / f"{stored.document_id}.json"
            if not path.exists():
                return None
            release = read_json(path)
            if (
                release["source_sha256"] != stored.sha256
                or release["configuration_sha256"] != self.signature
                or release["mode"] != self.config.contract_mode
            ):
                return None
            if (
                hash_file(managed(self.root, release["manifest_path"]))
                != release["manifest_sha256"]
            ):
                raise ValueError("processing manifest changed")
            for output in release["outputs"]:
                verified_artifact(self.root, output)
            if self.config.contract_mode == "extract":
                load_phase(self.root, release["manifest_path"], "extraction")
            source_exists = (self.root / stored.relative_path).exists()
            if source_exists and not release["source_retained"]:
                verified_artifact(
                    self.root,
                    {
                        "path": str(self.root / stored.relative_path),
                        "sha256": stored.sha256,
                    },
                )
                release = {**release, "source_retained": True}
                write_json(path, release)
            if release.get("cleanup_status") == "pending" or (
                self.config.contract_retention == "purge"
                and release["status"] == "completed"
                and release["mode"] == "both"
                and release["outputs"]
                and release["source_retained"]
            ):
                release = self._purge(stored, release, path)
            return release

    def process(self, stored):
        from .adhesion.pipeline import anonymize_extraction, extract_document

        self.prepare()
        with (
            workspace_scope(self.root, self.WORKSPACE),
            resource_scope(
                workers=self.config.ocr_workers,
                memory_mb=self.config.ocr_memory_mb,
                progress=self._progress,
            ),
        ):
            cached = self.cached(stored)
            if cached is not None:
                return {
                    **cached,
                    "execution_metrics": {
                        "cache_hit": True,
                        "extraction_seconds": 0.0,
                        "anonymization_seconds": 0.0,
                        "model_calls": 0,
                        "model_seconds": 0.0,
                        "model_backend_seconds": dict.fromkeys(BACKEND_DURATIONS, 0.0),
                        "inventory": {"native_reads": 0, "ocr_pages": 0, "ocr_retries": 0},
                    },
                }
            source = {
                "document_id": stored.document_id,
                "case_id": stored.case_id,
                "relative_path": stored.relative_path,
                "sha256": stored.sha256,
                "page_count": stored.page_count,
            }
            self.model.cache = workspace(self.root) / "model-cache" / stored.document_id
            started = time.monotonic()
            calls_before = getattr(self.model, "calls", 0)
            model_seconds_before = getattr(self.model, "seconds", 0)
            model_timings_before = inference_snapshot(self.model)
            self._progress(
                {
                    "event": "contract_phase_started",
                    "document_id": stored.document_id,
                    "phase": "extraction",
                }
            )
            extraction = extract_document(
                self.root, source, self.model, self.pipeline_config, role="scraper"
            )
            extraction_seconds = time.monotonic() - started
            self._progress(
                {
                    "event": "contract_phase_finished",
                    "document_id": stored.document_id,
                    "phase": "extraction",
                    "status": extraction["status"],
                    "manifest_path": extraction["manifest_path"],
                    "elapsed_seconds": round(extraction_seconds, 3),
                }
            )
            anonymization_seconds = 0.0
            result = extraction
            phases = {
                "extraction": {
                    "status": extraction["status"],
                    "manifest_path": extraction["manifest_path"],
                    "paths": [i["output"]["path"] for i in extraction["instruments"]],
                }
            }
            if self.config.contract_mode == "both":
                started = time.monotonic()
                self._progress(
                    {
                        "event": "contract_phase_started",
                        "document_id": stored.document_id,
                        "phase": "anonymization",
                    }
                )
                result = anonymize_extraction(
                    self.root,
                    extraction["manifest_path"],
                    self.model,
                    self.pipeline_config,
                    role="scraper",
                )
                phases["anonymization"] = {
                    "status": result["status"],
                    "manifest_path": result["manifest_path"],
                    "paths": [i["output"]["path"] for i in result["instruments"]],
                }
                anonymization_seconds = time.monotonic() - started
                self._progress(
                    {
                        "event": "contract_phase_finished",
                        "document_id": stored.document_id,
                        "phase": "anonymization",
                        "status": result["status"],
                        "manifest_path": result["manifest_path"],
                        "elapsed_seconds": round(anonymization_seconds, 3),
                    }
                )
            release = {
                "document_id": stored.document_id,
                "source_sha256": stored.sha256,
                "configuration_sha256": self.signature,
                "mode": self.config.contract_mode,
                "status": result["status"],
                "source_retained": True,
                "phases": phases,
                "manifest_path": result["manifest_path"],
                "manifest_sha256": result["manifest_sha256"],
                "outputs": [
                    i["output"]
                    for i in result["instruments"]
                    if i["status"] in ("completed", "extracted")
                ],
                "extraction_manifest": extraction["manifest_path"],
                "execution_metrics": {
                    "cache_hit": False,
                    "extraction_seconds": round(extraction_seconds, 3),
                    "anonymization_seconds": round(anonymization_seconds, 3),
                    "model_calls": getattr(self.model, "calls", 0) - calls_before,
                    "model_seconds": round(
                        getattr(self.model, "seconds", 0) - model_seconds_before, 3
                    ),
                    "model_backend_seconds": inference_timings(self.model, model_timings_before),
                    "inventory": extraction.get("inventory_stats", {}),
                },
            }
            path = workspace(self.root) / "scraper-releases" / f"{stored.document_id}.json"
            write_json(path, release)
            if (
                self.config.contract_mode == "both"
                and self.config.contract_retention == "purge"
                and release["status"] == "completed"
                and release["outputs"]
            ):
                release = self._purge(stored, release, path)
            return release

    def _purge(self, stored, release, release_path):
        work = workspace(self.root)
        extraction_path = owned_path(
            self.root, release["extraction_manifest"], work / "extractions" / stored.document_id
        )
        run_path = owned_path(
            self.root, release["manifest_path"], work / "runs" / stored.document_id
        )
        extraction = read_json(extraction_path)
        base_id = digest([stored.document_id, stored.sha256, extraction["configuration_sha256"]])[
            :24
        ]
        base = work / "extractions" / stored.document_id / base_id
        with (
            StorageLock(base / "phase.lock", timeout_seconds=0),
            StorageLock(run_path.parent / "run.lock", timeout_seconds=0),
        ):
            return self._purge_locked(stored, release, release_path)

    def _purge_locked(self, stored, release, release_path):
        """Idempotent cleanup only after every automatic privacy/preservation check passed."""
        if release["status"] != "completed" or release["mode"] != "both" or not release["outputs"]:
            raise ValueError("unverified contracts cannot authorize source cleanup")
        for output in release["outputs"]:
            verified_artifact(self.root, output)
        run = read_json(managed(self.root, release["manifest_path"]))
        if (
            run["phase"] != "anonymization"
            or run["status"] != "completed"
            or run["document_id"] != stored.document_id
            or run["source_sha256"] != stored.sha256
            or any(i["status"] != "completed" for i in run["instruments"])
            or digest([i["output"] for i in run["instruments"]]) != digest(release["outputs"])
        ):
            raise ValueError("cleanup is not bound to a complete verified anonymization")
        extraction_path = verified_artifact(
            self.root,
            {
                "path": release["extraction_manifest"],
                "sha256": run["extraction_manifest_sha256"],
            },
        )
        extraction = read_json(extraction_path)
        work = workspace(self.root)
        source = owned_path(self.root, self.root / stored.relative_path, self.root / "pdfs")
        targets = [{"path": str(source), "sha256": stored.sha256}]
        for item in extraction["instruments"]:
            owned_path(
                self.root,
                item["output"]["path"],
                work / "extracted" / extraction["configuration_sha256"] / stored.document_id,
            )
            owned_path(self.root, item["layout"]["path"], extraction_path.parent)
            targets.extend((item["output"], item["layout"]))
        for artifact in targets:
            if Path(artifact["path"]).exists():
                verified_artifact(self.root, artifact)
        private_folders = [
            work / "model-cache" / stored.document_id,
            work / "inventory-v3" / stored.document_id,
            work / "inventory" / stored.document_id,
        ]
        private_folders.extend(
            Path(release["manifest_path"]).parent / i["contract_id"] for i in run["instruments"]
        )
        private_folders.extend(
            Path(release["extraction_manifest"]).parent / i["contract_id"]
            for i in extraction["instruments"]
        )
        for folder in private_folders:
            owned_path(self.root, folder, work)
        release = {**release, "cleanup_status": "pending"}
        write_json(release_path, release)
        write_json(
            extraction_path.parent / "purged.json",
            {
                "manifest_sha256": hash_file(extraction_path),
                "reason": "verified_anonymization",
                "source_sha256": stored.sha256,
            },
        )
        for artifact in targets:
            path = Path(artifact["path"])
            if path.exists():
                verified_artifact(self.root, artifact)
                path.unlink()
        for folder in private_folders:
            folder = managed(self.root, folder)
            if folder.exists():
                shutil.rmtree(folder)
        release = {**release, "source_retained": False, "cleanup_status": "completed"}
        release["phases"]["extraction"]["artifacts_available"] = False
        write_json(release_path, release)
        return release
