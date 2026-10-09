"""Independent, resumable extraction and anonymization artifacts."""

from __future__ import annotations

from pathlib import Path

from ..pdf_validation import hash_file
from ..runtime import StorageLock
from .common import digest, now, read_json, source_path, workspace, write_json
from .pdf import region_inventory, render_contract_page, write_cleaned_contract


def managed(root, path):
    path = Path(path).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError("phase artifact outside storage root")
    return path


def verified_artifact(root, artifact):
    path = managed(root, artifact["path"])
    if not path.is_file() or hash_file(path) != artifact["sha256"]:
        raise ValueError("phase artifact missing or changed")
    return path


def load_phase(root, path, phase, *, verify_layout=True):
    path = managed(root, path)
    manifest = read_json(path)
    if manifest.get("phase") != phase:
        raise ValueError("incorrect phase manifest")
    for item in manifest["instruments"]:
        verified_artifact(root, item["output"])
        if verify_layout and phase == "extraction":
            verified_artifact(root, item["layout"])
    return {**manifest, "manifest_path": str(path), "manifest_sha256": hash_file(path)}


def save_phase(path, manifest):
    write_json(path, manifest)
    return {**manifest, "manifest_path": str(path), "manifest_sha256": hash_file(path)}


def publish_contract(root, source, target, regions, masks, cache, *, raster_source=False):
    """Resume an atomic PDF publication without replacing a verified earlier output."""
    if cache.exists():
        result = read_json(cache)
        verified_artifact(root, result)
        return result
    if target.exists():
        import uuid

        # An interruption after PDF publication must not silently overwrite evidence.
        target.rename(cache.parent / f"interrupted-{uuid.uuid4().hex}.pdf")
    result = write_cleaned_contract(
        source,
        target,
        regions,
        masks,
        dpi=300,
        decision_mode="automatic",
        raster_source=raster_source,
    )
    target.chmod(0o600)
    write_json(cache, result)
    return result


def extract_document(root, source, model, config, *, role, detector, inventory_factory):
    import pymupdf

    root, work = Path(root).resolve(), workspace(root)
    phase_config = config.get("extraction", config)
    signature = digest(phase_config)
    extraction_id = digest([source["document_id"], source["sha256"], signature])[:24]
    folder = work / "extractions" / source["document_id"] / extraction_id
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    base_folder = folder
    latest = base_folder / "latest.json"
    with StorageLock(base_folder / "phase.lock", timeout_seconds=0):
        if latest.exists():
            folder = managed(root, read_json(latest)["folder"])
            if not folder.is_relative_to(base_folder):
                raise ValueError("extraction generation outside its source folder")
            extraction_id = folder.name
        manifest_path = folder / "manifest.json"
        if manifest_path.exists():
            if not (folder / "purged.json").exists():
                return load_phase(root, manifest_path, "extraction")
            marker = read_json(folder / "purged.json")
            if marker["manifest_sha256"] != hash_file(manifest_path):
                raise ValueError("purged extraction manifest changed")
            source_path(root, source)
            import uuid

            folder = base_folder / f"{base_folder.name}-{uuid.uuid4().hex[:12]}"
            folder.mkdir(mode=0o700)
            extraction_id = folder.name
            manifest_path = folder / "manifest.json"
            write_json(latest, {"folder": str(folder)})
        original = source_path(root, source)
        write_json(work / "extraction-configurations" / f"{signature}.json", phase_config)
        manifest = {
            "phase": "extraction",
            "document_id": source["document_id"],
            "source": source,
            "source_sha256": source["sha256"],
            "source_pages": source["page_count"],
            "extraction_id": extraction_id,
            "configuration_sha256": signature,
            "role": role,
            "status": "running",
            "started_at": now(),
            "instruments": [],
        }
        write_json(folder / "progress.json", manifest)
        try:
            with pymupdf.open(original) as pdf, inventory_factory(root, source, pdf=pdf) as pages:
                detection_path = folder / "detection.json"
                if detection_path.exists():
                    detection = read_json(detection_path)
                else:
                    detection = detector(model, pdf, pages, pages.attachments)
                    write_json(detection_path, detection)
                manifest["detection"] = detection
                for position, instrument in enumerate(detection["instruments"], 1):
                    contract_id = f"{extraction_id}-{position:03d}"
                    contract_folder = folder / contract_id
                    contract_folder.mkdir(mode=0o700, exist_ok=True)
                    regions = instrument.get(
                        "regions", [{"page": n, "rect": [0, 0, 1, 1]} for n in instrument["pages"]]
                    )
                    if [r["page"] for r in regions] != instrument["pages"]:
                        raise ValueError("contract regions do not match source pages")
                    target = (
                        work
                        / "extracted"
                        / signature
                        / source["document_id"]
                        / f"{contract_id}.pdf"
                    )
                    output = publish_contract(
                        root, original, target, regions, {}, contract_folder / "output.json"
                    )
                    layout_path = contract_folder / "private-layout.json"
                    if layout_path.exists():
                        layout = read_json(layout_path)
                        if layout["pdf_sha256"] != output["sha256"]:
                            raise ValueError("layout belongs to another extracted PDF")
                    else:
                        layout_pages = []
                        for output_number, region in enumerate(regions):
                            page = {**pages[region["page"] - 1], "family": instrument["family"]}
                            geometry = output["pages"][output_number].get("geometry")
                            if geometry is None:
                                _, geometry = render_contract_page(
                                    pdf, region, dpi=300, with_geometry=True
                                )
                            layout_pages.append(region_inventory(page, region, geometry))
                        write_json(
                            layout_path, {"pdf_sha256": output["sha256"], "pages": layout_pages}
                        )
                    manifest["instruments"].append(
                        {
                            **instrument,
                            "regions": regions,
                            "contract_id": contract_id,
                            "status": "extracted",
                            "output": output,
                            "layout": {"path": str(layout_path), "sha256": hash_file(layout_path)},
                        }
                    )
                    write_json(folder / "progress.json", manifest)
                if hasattr(pages, "stats"):
                    manifest["inventory_stats"] = dict(pages.stats)
            incomplete = detection["unresolved"] or not detection.get("fallback_complete", True)
            status = (
                "needs_review"
                if incomplete
                else ("completed" if manifest["instruments"] else "no_target")
            )
            manifest.update(status=status, finished_at=now())
            return save_phase(manifest_path, manifest)
        except BaseException as exc:
            manifest.update(status="error", error_type=type(exc).__name__, finished_at=now())
            write_json(folder / "error.json", manifest)
            raise


def anonymize_extraction(root, extraction_path, model, config, *, role, anonymizer):
    import pymupdf

    root, work = Path(root).resolve(), workspace(root)
    # The source case PDF and the extraction model are not inputs to this phase.
    extraction = load_phase(root, extraction_path, "extraction")
    signature = digest(config)
    phase_config = config.get("anonymization", config)
    if phase_config is None:
        raise ValueError("anonymization configuration is required")
    anonymization_id = digest([extraction["manifest_sha256"], digest(phase_config)])[:24]
    run_id = digest(
        [
            extraction["document_id"],
            extraction["source_sha256"],
            signature,
            extraction["manifest_sha256"],
        ]
    )[:24]
    folder = work / "runs" / extraction["document_id"] / run_id
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest_path = folder / "manifest.json"
    with StorageLock(folder / "run.lock", timeout_seconds=0):
        if manifest_path.exists():
            result = load_phase(root, manifest_path, "anonymization", verify_layout=False)
            if result["extraction_manifest_sha256"] != extraction["manifest_sha256"]:
                raise ValueError("anonymization refers to another extraction manifest")
            return result
        write_json(work / "configurations" / f"{signature}.json", config)
        manifest = {
            "phase": "anonymization",
            "document_id": extraction["document_id"],
            "source_sha256": extraction["source_sha256"],
            "source_pages": extraction["source_pages"],
            "run_id": run_id,
            "anonymization_id": anonymization_id,
            "configuration_sha256": signature,
            "anonymization_configuration_sha256": digest(phase_config),
            "role": role,
            "started_at": now(),
            "status": "running",
            "instruments": [],
            "extraction_manifest": str(extraction_path),
            "extraction_manifest_sha256": extraction["manifest_sha256"],
            "extraction_id": extraction["extraction_id"],
            "detection": extraction["detection"],
        }
        write_json(folder / "progress.json", manifest)
        try:
            for instrument in extraction["instruments"]:
                extracted = verified_artifact(root, instrument["output"])
                layout = read_json(verified_artifact(root, instrument["layout"]))
                if layout["pdf_sha256"] != instrument["output"]["sha256"]:
                    raise ValueError("layout does not match extraction input")
                contract_id = instrument["contract_id"]
                contract_folder = folder / contract_id
                contract_folder.mkdir(mode=0o700, exist_ok=True)
                decisions, masks = [], {}
                with pymupdf.open(extracted) as pdf:
                    if len(pdf) != len(layout["pages"]) or len(pdf) != len(instrument["pages"]):
                        raise ValueError("extracted page count does not match layout")
                    for output_number, page in enumerate(layout["pages"], 1):
                        region = instrument["regions"][output_number - 1]
                        region_hash = digest(region)
                        page_folder = (
                            contract_folder / f"source-{region['page']:05d}-{region_hash[:12]}"
                        )
                        decision_path = page_folder / "privacy.json"
                        if decision_path.exists():
                            decision = read_json(decision_path)
                            if (
                                decision.get("region_sha256") != region_hash
                                or decision.get("extraction_sha256")
                                != instrument["output"]["sha256"]
                            ):
                                raise ValueError(
                                    "privacy cache belongs to another extraction input"
                                )
                        else:
                            image = render_contract_page(
                                pdf, {"page": output_number}, raster_source=True
                            )
                            if image.size != (page["width"], page["height"]):
                                raise ValueError("extracted pixels and privacy coordinates differ")
                            decision = anonymizer(model, image, page, page_folder)
                            decision = {
                                **decision,
                                "source_region": region,
                                "region_sha256": region_hash,
                                "extraction_sha256": instrument["output"]["sha256"],
                            }
                            write_json(decision_path, decision)
                        decisions.append(decision)
                        masks[output_number] = decision["masks"]
                accepted = all(d["status"] == "completed" for d in decisions)
                output = work / ("development-outputs" if role == "development" else "outputs")
                output /= signature
                output /= "accepted" if accepted else "quarantine"
                output = output / run_id / f"{contract_id}.pdf"
                result = publish_contract(
                    root,
                    extracted,
                    output,
                    [{"page": n} for n in range(1, len(instrument["pages"]) + 1)],
                    masks,
                    contract_folder / "output.json",
                    raster_source=True,
                )
                manifest["instruments"].append(
                    {
                        **{
                            k: v
                            for k, v in instrument.items()
                            if k not in ("layout", "output", "status")
                        },
                        "status": "completed" if accepted else "needs_review",
                        "extracted_output": instrument["output"],
                        "output": result,
                        "privacy": decisions,
                    }
                )
                write_json(folder / "progress.json", manifest)
            needs_review = extraction["status"] == "needs_review" or any(
                i["status"] != "completed" for i in manifest["instruments"]
            )
            status = (
                "needs_review"
                if needs_review
                else ("completed" if manifest["instruments"] else "no_target")
            )
            manifest.update(status=status, finished_at=now())
            return save_phase(manifest_path, manifest)
        except BaseException as exc:
            manifest.update(status="error", error_type=type(exc).__name__, finished_at=now())
            write_json(folder / "error.json", manifest)
            raise
