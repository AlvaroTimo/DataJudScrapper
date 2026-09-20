"""Publish automatically processed card documents after the 100-case validation gate.

Publication is separate from irreversible retention cleanup. This command preserves
sources: the pipeline is still under validation and its measured errors must be resolved.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .card_automation import initialize_automation_schema
from .card_model import canonical_hash, private_json
from .card_validation import metrics
from .contract_catalog import connect_catalog
from .contract_release import managed_path, publish_copy
from .pdf_validation import hash_file
from .runtime import StorageLock, utc_now


def publish(root: Path, run_id: str):
    report = metrics(root)
    if not report["passed"]:
        raise ValueError("los 100 expedientes no han superado la validacion del metodo automatico")
    with (
        StorageLock(root / "state/card-automation.lock", timeout_seconds=0),
        connect_catalog(root / "state/scraper.sqlite3") as connection,
    ):
        initialize_automation_schema(connection)
        run = connection.execute(
            "SELECT * FROM card_automation_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if (
            not run
            or run["status"] != "automatic_checks_passed"
            or run["configuration_sha256"] != report["configuration_sha256"]
        ):
            raise ValueError("la salida no usa la configuracion validada o tiene pendientes")
        source = connection.execute(
            "SELECT * FROM documents WHERE document_id=?",
            (run["document_id"],),
        ).fetchone()
        path = managed_path(root, source["relative_path"])
        if hash_file(path) != run["source_sha256"]:
            raise ValueError("el original cambio despues de la extraccion")
        manifest_path = managed_path(root, run["manifest_path"])
        if hash_file(manifest_path) != run["manifest_sha256"]:
            raise ValueError("el manifiesto automatico ha cambiado")
        manifest = json.loads(manifest_path.read_text())
        output = {
            "run_id": run_id,
            "document_id": source["document_id"],
            "source_sha256": source["sha256"],
            "configuration_sha256": run["configuration_sha256"],
            "validation_report_sha256": canonical_hash(report),
            "contracts": [],
            "source_retained": True,
        }
        for contract in manifest["contracts"]:
            original = managed_path(root, contract["output"]["path"])
            expected = contract["output"]["sha256"]
            if hash_file(original) != expected:
                raise ValueError("PDF limpio modificado")
            relative = (
                Path("card-contracts")
                / source["document_id"]
                / (f"{contract['occurrence']:03d}-{contract['contract_id']}.pdf")
            )
            destination = managed_path(root, str(relative))
            publish_copy(original, destination, expected)
            output["contracts"].append(
                {
                    "contract_id": contract["contract_id"],
                    "kind": contract["kind"],
                    "path": str(relative),
                    "sha256": expected,
                    "pages": contract["output"]["page_count"],
                    "had_sensitive_data": contract["had_sensitive_data"],
                }
            )
        with connection:
            connection.execute(
                "INSERT OR REPLACE INTO card_automatic_releases VALUES (?,?,?,?)",
                (run_id, canonical_hash(report), json.dumps(output), utc_now().isoformat()),
            )
        private_json(root / "reports/card-automation/releases" / f"{run_id}.json", output)
        return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--storage-root", type=Path, default=Path("data"))
    args = parser.parse_args()
    print(json.dumps(publish(args.storage_root.resolve(), args.run_id), indent=2))


if __name__ == "__main__":
    main()
