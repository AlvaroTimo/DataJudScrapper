"""Re-run only the anonymization phase on the contracts already extracted in a batch.

Extraction is reused as-is (its signature excludes privacy.py), so this measures
anonymization changes in minutes instead of a full re-extraction. Outputs go to new
run folders; previous runs, releases and sources are left untouched.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from datajud_scraper.adhesion.common import workspace, workspace_scope
from datajud_scraper.adhesion.local_model import LocalModel
from datajud_scraper.adhesion.pipeline import anonymize_extraction, configuration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_id")
    parser.add_argument("--storage-root", type=Path, default=Path("data"))
    parser.add_argument("--local-model", default="qwen3-vl:8b-instruct-q8_0")
    parser.add_argument("--local-context-tokens", type=int, default=24576)
    parser.add_argument("--output", type=Path, required=True, help="JSON summary of the new runs")
    args = parser.parse_args()
    root = args.storage_root.resolve()
    manifests = []
    for line in (root / "reports" / args.batch_id / "results.jsonl").read_text().splitlines():
        entry = json.loads(line)
        phases = (entry["result"].get("contract_processing") or {}).get("phases", {})
        path = phases.get("extraction", {}).get("manifest_path")
        if path and json.loads(Path(path).read_text())["instruments"]:
            manifests.append((entry["position"], entry["process_number"], path))
    summary = []
    with workspace_scope(root, "adhesion-scraper"):
        model = LocalModel(
            workspace(root) / "model-cache" / "preflight",
            model=args.local_model,
            context_tokens=args.local_context_tokens,
        )
        config = configuration(model, include_privacy=True, include_reference=False)
        for position, process, path in manifests:
            document = json.loads(Path(path).read_text())["document_id"]
            model.cache = workspace(root) / "model-cache" / document
            started = time.monotonic()
            record = {"position": position, "process_number": process, "extraction_manifest": path}
            try:
                result = anonymize_extraction(root, path, model, config, role="scraper")
            except Exception as error:  # one dense or broken page must not stop the batch
                record.update(status="error", error=f"{type(error).__name__}: {error}")
                record["instruments"] = []
            else:
                record.update(
                    anonymization_manifest=result["manifest_path"],
                    status=result["status"],
                    instruments=[
                        {
                            "contract_id": i["contract_id"],
                            "status": i["status"],
                            "output": i["output"]["path"],
                        }
                        for i in result["instruments"]
                    ],
                )
            record["seconds"] = round(time.monotonic() - started, 1)
            summary.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
            args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        model.close()
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
