"""Verified clean publication followed by resumable, explicitly recorded source removal."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import uuid
from pathlib import Path

from .contract_catalog import (
    audit,
    connect_catalog,
    initialize_contract_schema,
    require_release_reviews,
)
from .contract_outputs import refresh_sensitive_state, verified_cleaned_path
from .contract_review import source_record
from .pdf_validation import hash_file
from .runtime import StorageLock, utc_now


def managed_path(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("ruta fuera del almacenamiento administrado")
    return path


def publish_copy(source: Path, destination: Path, expected_sha: str) -> None:
    destination.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    if destination.exists():
        if not destination.is_file() or hash_file(destination) != expected_sha:
            raise ValueError("el destino contiene una version distinta del contrato")
        return
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4()}.part")
    try:
        with source.open("rb") as reader, temporary.open("xb") as writer:
            os.fchmod(writer.fileno(), 0o640)
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        if hash_file(temporary) != expected_sha:
            raise ValueError("la copia del contrato no coincide con la version aprobada")
        os.link(temporary, destination)
        fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def verified_release(connection, root: Path, document_id: str, *, allow_pending=False) -> dict:
    """Verify the durable manifest against current approvals and actual clean files."""
    row = connection.execute(
        "SELECT * FROM contract_releases WHERE document_id=?", (document_id,)
    ).fetchone()
    if row is None or (row["status"] != "complete" and not allow_pending):
        raise ValueError("la publicacion y el descarte todavia no estan completos")
    require_release_reviews(connection, document_id)
    manifest = json.loads(row["manifest_json"])
    source = connection.execute(
        "SELECT * FROM documents WHERE document_id=?", (document_id,)
    ).fetchone()
    if source["sha256"] != row["source_sha256"] or source["source_disposition"] != (
        "purged" if row["status"] == "complete" else "purging"
    ):
        raise ValueError("estado de la fuente incoherente con la publicacion")
    contracts = {
        r["contract_id"]: r
        for r in connection.execute("SELECT * FROM contracts WHERE document_id=?", (document_id,))
    }
    if set(contracts) != {c["contract_id"] for c in manifest["contracts"]}:
        raise ValueError("el manifiesto no contiene exactamente los contratos aprobados")
    for item in manifest["contracts"]:
        record = contracts[item["contract_id"]]
        path, _ = verified_cleaned_path(root, record)
        if path != managed_path(root, item["path"]) or item["sha256"] != record["cleaned_sha256"]:
            raise ValueError("el manifiesto corresponde a otra version del contrato")
    return manifest


def remove_source_files(root: Path, manifest: dict) -> None:
    """Called only after clean files and approvals have passed all release gates."""
    source = managed_path(root, manifest["source_path"])
    if source.exists() and (not source.is_file() or hash_file(source) != manifest["source_sha256"]):
        raise ValueError("el original cambio; no se puede descartar")
    document_id = manifest["document_id"]
    if Path(document_id).name != document_id or document_id in ("", ".", ".."):
        raise ValueError("identificador de documento no valido para descartar temporales")
    work = root / "contract-work" / document_id
    if work.is_symlink() or not work.resolve().is_relative_to(root.resolve() / "contract-work"):
        raise ValueError("directorio temporal fuera de la ubicacion prevista")
    if source.is_relative_to(root.resolve() / "contracts"):
        raise ValueError("el original no puede ocupar la carpeta de contratos publicados")
    source.unlink(missing_ok=True)
    if work.exists():
        shutil.rmtree(work)


def release_source(connection, root: Path, document_id: str) -> dict:
    """The user's retention policy authorizes removal only after complete manual review.

    A durable intent precedes unlinking. Interrupted deletion resumes from that intent;
    it never interprets a missing original as proof of review or successful cleaning.
    The scraper lock prevents concurrent downloads; SQLite serializes review changes.
    """
    root = root.resolve()
    (root / "state").mkdir(mode=0o750, parents=True, exist_ok=True)
    with StorageLock(root / "state/scraper.lock", timeout_seconds=0):
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT status FROM contract_releases WHERE document_id=?", (document_id,)
            ).fetchone()
            if existing:
                manifest = verified_release(connection, root, document_id, allow_pending=True)
                if existing["status"] == "complete":
                    connection.commit()
                    return manifest
            else:
                require_release_reviews(connection, document_id)
                source, source_path = source_record(connection, root, document_id)
                if Path(document_id).name != document_id or document_id in ("", ".", ".."):
                    raise ValueError("identificador de documento invalido")
                manifest = {
                    "document_id": document_id,
                    "source_sha256": source["sha256"],
                    "source_path": str(source_path.relative_to(root)),
                    "contracts": [],
                }
                records = connection.execute(
                    "SELECT * FROM contracts WHERE document_id=? ORDER BY occurrence",
                    (document_id,),
                ).fetchall()
                for record in records:
                    draft, evidence = verified_cleaned_path(root, record)
                    relative = (
                        Path("contracts")
                        / document_id
                        / (
                            f"{record['occurrence']:03d}-{record['kind']}-{record['contract_id']}.pdf"
                        )
                    )
                    destination = managed_path(root, str(relative))
                    publish_copy(draft, destination, record["cleaned_sha256"])
                    evidence["path"] = str(destination)
                    connection.execute(
                        "UPDATE contracts SET cleaned_path=?,render_evidence_json=?,updated_at=? "
                        "WHERE contract_id=?",
                        (
                            str(destination),
                            json.dumps(evidence),
                            utc_now().isoformat(),
                            record["contract_id"],
                        ),
                    )
                    manifest["contracts"].append(
                        {
                            "contract_id": record["contract_id"],
                            "path": str(relative),
                            "sha256": record["cleaned_sha256"],
                            "pages": evidence["page_count"],
                        }
                    )
                now = utc_now().isoformat()
                connection.execute(
                    "INSERT INTO contract_releases VALUES (?,?,?,'purging',?,?)",
                    (document_id, source["sha256"], json.dumps(manifest), now, now),
                )
                connection.execute(
                    "UPDATE documents SET source_disposition='purging' WHERE document_id=?",
                    (document_id,),
                )
                refresh_sensitive_state(connection, document_id)
                audit(
                    connection,
                    "clean_contracts_published",
                    document_id=document_id,
                    contract_count=len(records),
                )
            connection.commit()  # Durable intent before the first deletion.
        except Exception:
            connection.rollback()
            raise

        connection.execute("BEGIN IMMEDIATE")
        try:
            manifest = verified_release(connection, root, document_id, allow_pending=True)
            remove_source_files(root, manifest)
            # Raw OCR, source renders and draft revisions are gone. Retain hashes,
            # coordinates, reviewed decisions and counts, without attachment names.
            for attachment in connection.execute(
                "SELECT attachment_id,evidence_json FROM contract_attachments WHERE document_id=?",
                (document_id,),
            ).fetchall():
                evidence = json.loads(attachment["evidence_json"])
                evidence.pop("title", None)
                connection.execute(
                    "UPDATE contract_attachments SET title=NULL,evidence_json=? "
                    "WHERE attachment_id=?",
                    (json.dumps(evidence), attachment["attachment_id"]),
                )
            connection.execute(
                "UPDATE contract_sources SET inventory_path=NULL WHERE document_id=?",
                (document_id,),
            )
            connection.execute(
                "UPDATE contracts SET extraction_path=NULL,extraction_sha256=NULL "
                "WHERE document_id=?",
                (document_id,),
            )
            connection.execute(
                "UPDATE documents SET source_disposition='purged',original_filename=NULL "
                "WHERE document_id=?",
                (document_id,),
            )
            connection.execute(
                "UPDATE contract_releases SET status='complete',updated_at=? WHERE document_id=?",
                (utc_now().isoformat(), document_id),
            )
            audit(connection, "source_and_sensitive_work_files_removed", document_id=document_id)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Publicar contratos revisados y descartar su fuente."
    )
    parser.add_argument("--storage-root", type=Path, default=Path("data"))
    parser.add_argument("document_id")
    args = parser.parse_args(argv)
    root = args.storage_root.resolve()
    with connect_catalog(root / "state/scraper.sqlite3") as connection:
        initialize_contract_schema(connection)
        result = release_source(connection, root, args.document_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
