"""Resumable removal of sources and private caches after validated card publication."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .card_automation import initialize_automation_schema
from .card_model import canonical_hash, private_json
from .card_publish import publish
from .card_validation import metrics, verified_manifest
from .contract_catalog import connect_catalog
from .contract_release import managed_path
from .pdf_validation import hash_file
from .runtime import StorageLock, utc_now


def initialize_retention_schema(connection):
    initialize_automation_schema(connection)
    with connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS card_retention_events (
                run_id TEXT PRIMARY KEY REFERENCES card_automation_runs,
                document_id TEXT NOT NULL UNIQUE REFERENCES documents,
                status TEXT NOT NULL CHECK(status IN ('purging','complete')),
                intent_json TEXT NOT NULL, intent_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL, finished_at TEXT
            )
        """)


def verified_card_release(connection, root, document_id, *, allow_pending=False):
    event = connection.execute(
        "SELECT * FROM card_retention_events WHERE document_id=?", (document_id,)
    ).fetchone()
    if not event or (event["status"] != "complete" and not allow_pending):
        raise ValueError("descarte automatico pendiente o inexistente")
    intent = json.loads(event["intent_json"])
    if canonical_hash(intent) != event["intent_sha256"] or intent["document_id"] != document_id:
        raise ValueError("intencion de descarte modificada")
    report = intent["validation_report"]
    if not report["passed"] or report["manually_validated_documents"] != 100:
        raise ValueError("descarte sin validacion completa de los 100 expedientes")
    run = connection.execute(
        "SELECT * FROM card_automation_runs WHERE run_id=?", (event["run_id"],)
    ).fetchone()
    source = connection.execute(
        "SELECT * FROM documents WHERE document_id=?", (document_id,)
    ).fetchone()
    if (
        not run
        or run["status"] != "automatic_checks_passed"
        or run["configuration_sha256"] != report["configuration_sha256"]
        or source["sha256"] != intent["source_sha256"]
        or source["source_disposition"]
        != ("purged" if event["status"] == "complete" else "purging")
    ):
        raise ValueError("estado de conservacion automatico incoherente")
    manifest = verified_manifest(run)
    expected = {c["contract_id"]: c for c in manifest["contracts"]}
    actual = intent["contracts"]
    if len(actual) != len(expected) or {c["contract_id"] for c in actual} != set(expected):
        raise ValueError("el descarte no conserva todos los contratos extraidos")
    for item in actual:
        if (
            item["sha256"] != expected[item["contract_id"]]["output"]["sha256"]
            or hash_file(managed_path(root, item["path"])) != item["sha256"]
        ):
            raise ValueError("contrato publicado modificado")
    return intent


def prune_sensitive_files(root, intent):
    """Keep only the approved clean PDFs and their immutable nontextual manifest."""
    document_id = intent["document_id"]
    if Path(document_id).name != document_id or document_id in ("", ".", ".."):
        raise ValueError("identificador de documento invalido")
    source = managed_path(root, intent["source_path"])
    if source.exists() and hash_file(source) != intent["source_sha256"]:
        raise ValueError("original modificado antes del descarte")
    if source.is_relative_to(root / "card-contracts") or source.is_relative_to(root / "contracts"):
        raise ValueError("el original no puede ser un contrato publicado")
    keep = {managed_path(root, p) for p in intent["keep_paths"]}
    work = root / "card-work" / document_id
    if work.is_symlink() or work.resolve() != work:
        raise ValueError("directorio de trabajo redirigido")
    if any(not p.is_relative_to(work) for p in keep):
        raise ValueError("artefacto conservado fuera del expediente")
    # Validate all private locations before deleting the first file. Symlinks
    # cannot redirect cleanup into another document or outside this storage.
    private = []
    for folder in (root / "contract-work" / document_id, root / "card-validation" / document_id):
        if folder.is_symlink() or folder.resolve() != folder:
            raise ValueError("directorio privado redirigido")
        private.append(folder)
    for item in work.rglob("*"):
        if item.is_symlink():
            raise ValueError("enlace inesperado en temporales privados")
    source.unlink(missing_ok=True)
    for folder in private:
        if folder.exists():
            shutil.rmtree(folder)
    for item in sorted(work.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if item.is_file() and item not in keep:
            item.unlink()
        elif item.is_dir() and not any(item.iterdir()):
            item.rmdir()


def purge_source(root: Path, run_id: str):
    root = root.resolve()
    with connect_catalog(root / "state/scraper.sqlite3") as connection:
        initialize_retention_schema(connection)
        existing = connection.execute(
            "SELECT 1 FROM card_retention_events WHERE run_id=?", (run_id,)
        ).fetchone()
    # Publication and the measured 100-case method gate precede every new intent.
    # An interrupted intent resumes from its durable snapshot, never from absence
    # of the source or from fabricated reviews.
    if not existing:
        publish(root, run_id)
    with (
        StorageLock(root / "state/card-automation.lock", timeout_seconds=0),
        StorageLock(root / "state/scraper.lock", timeout_seconds=0),
        connect_catalog(root / "state/scraper.sqlite3") as connection,
    ):
        initialize_retention_schema(connection)
        existing = connection.execute(
            "SELECT * FROM card_retention_events WHERE run_id=?", (run_id,)
        ).fetchone()
        if existing:
            intent = verified_card_release(
                connection, root, existing["document_id"], allow_pending=True
            )
            if existing["status"] == "complete":
                return intent
        else:
            report = metrics(root)
            run = connection.execute(
                "SELECT * FROM card_automation_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if (
                not report["passed"]
                or report["configuration_sha256"] != run["configuration_sha256"]
            ):
                raise ValueError("la configuracion no tiene validacion completa")
            source = connection.execute(
                "SELECT * FROM documents WHERE document_id=?", (run["document_id"],)
            ).fetchone()
            if source["source_disposition"] != "retained":
                raise ValueError("fuente en un estado de conservacion incompatible")
            if hash_file(managed_path(root, source["relative_path"])) != run["source_sha256"]:
                raise ValueError("original modificado")
            manifest = verified_manifest(run)
            release = json.loads(
                connection.execute(
                    "SELECT manifest_json FROM card_automatic_releases WHERE run_id=?", (run_id,)
                ).fetchone()[0]
            )
            intent = {
                "run_id": run_id,
                "document_id": run["document_id"],
                "source_path": source["relative_path"],
                "source_sha256": source["sha256"],
                "validation_report": report,
                "contracts": release["contracts"],
                "keep_paths": [run["manifest_path"]]
                + [c["output"]["path"] for c in manifest["contracts"]],
            }
            with connection:
                connection.execute(
                    "INSERT INTO card_retention_events VALUES (?,?,'purging',?,?,?,NULL)",
                    (
                        run_id,
                        run["document_id"],
                        json.dumps(intent),
                        canonical_hash(intent),
                        utc_now().isoformat(),
                    ),
                )
                connection.execute(
                    "UPDATE documents SET source_disposition='purging' WHERE document_id=?",
                    (run["document_id"],),
                )
        intent = verified_card_release(connection, root, intent["document_id"], allow_pending=True)
        prune_sensitive_files(root, intent)
        with connection:
            for row in connection.execute(
                "SELECT attachment_id,evidence_json FROM contract_attachments WHERE document_id=?",
                (intent["document_id"],),
            ).fetchall():
                evidence = json.loads(row["evidence_json"])
                evidence.pop("title", None)
                connection.execute(
                    "UPDATE contract_attachments SET title=NULL,evidence_json=? "
                    "WHERE attachment_id=?",
                    (json.dumps(evidence), row["attachment_id"]),
                )
            connection.execute(
                "UPDATE contract_sources SET inventory_path=NULL WHERE document_id=?",
                (intent["document_id"],),
            )
            connection.execute(
                "UPDATE documents SET source_disposition='purged',original_filename=NULL "
                "WHERE document_id=?",
                (intent["document_id"],),
            )
            connection.execute(
                "UPDATE card_retention_events SET status='complete',finished_at=? WHERE run_id=?",
                (utc_now().isoformat(), run_id),
            )
        private_json(root / "reports/card-automation/retention" / f"{run_id}.json", intent)
        return intent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--storage-root", type=Path, default=Path("data"))
    args = parser.parse_args()
    print(json.dumps(purge_source(args.storage_root, args.run_id), indent=2))


if __name__ == "__main__":
    main()
