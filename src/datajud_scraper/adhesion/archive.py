"""Verified, resumable archival and reversal of the retired contract pipelines."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..pdf_validation import hash_file
from ..runtime import StorageLock
from .common import now, read_json, readonly, workspace, write_json

LEGACY_PATHS = (
    "contracts",
    "card-contracts",
    "contract-work",
    "card-work",
    "card-validation",
    "reports/card-automation",
    "reports/contract-pilot",
)


def legacy_processes():
    found = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit() or int(path.name) == os.getpid():
            continue
        try:
            args = path.joinpath("cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if not args or b"python" not in args[0]:
            continue
        if any(
            a
            in (
                b"datajud_scraper.card_automation",
                b"datajud_scraper.card_retention",
                b"datajud_scraper.contract_release",
                b"datajud_scraper.contract_inventory",
            )
            for a in args
        ) or any(b"/diagnose-v" in a and a.endswith(b".py") for a in args):
            found.append(int(path.name))
    return found


def files_in(path):
    if path.is_symlink():
        raise ValueError("cannot archive a symbolic link")
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise ValueError("cannot archive symbolic links")
        if item.is_file():
            yield item


def archive(root, *, destination=None):
    root = Path(root).resolve()
    pointer = workspace(root) / "archive.json"
    if pointer.exists():
        return verify_archive(read_json(pointer)["path"])
    if legacy_processes():
        raise RuntimeError("stop legacy contract processes before archiving")
    if destination is None:
        pending = [
            p.parent
            for p in (root / "backups").glob("*/archive.json")
            if read_json(p).get("source_root") == str(root)
            and read_json(p).get("status") == "moving"
        ]
        if len(pending) > 1:
            raise ValueError("multiple interrupted backups; specify their destination")
        destination = pending[0] if pending else None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = Path(destination) if destination else root / "backups" / stamp
    destination = destination.resolve()
    if not destination.is_relative_to(root / "backups"):
        raise ValueError("backup must be inside data/backups")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    journal_path = destination / "archive.json"
    with StorageLock(root / "state/scraper.lock", timeout_seconds=0):
        if journal_path.exists():
            journal = read_json(journal_path)
            if journal["source_root"] != str(root):
                raise ValueError("archive belongs to another storage root")
        else:
            journal = {
                "version": 1,
                "created_at": now(),
                "source_root": str(root),
                "path": str(destination),
                "status": "moving",
                "moves": [],
                "files": [],
            }
            database = destination / "state/scraper.sqlite3"
            database.parent.mkdir(mode=0o700)
            with (
                readonly(root / "state/scraper.sqlite3") as source,
                sqlite3.connect(database) as target,
            ):
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("invalid SQLite backup")
            database.chmod(0o600)
            journal["database_sha256"] = hash_file(database)
            for relative in LEGACY_PATHS:
                path = root / relative
                if not path.exists():
                    continue
                journal["moves"].append({"relative_path": relative, "status": "pending"})
                for item in files_in(path):
                    journal["files"].append(
                        {
                            "relative_path": str(item.relative_to(root)),
                            "bytes": item.stat().st_size,
                            "sha256": hash_file(item),
                        }
                    )
            write_json(journal_path, journal)
        for move in journal["moves"]:
            relative = move["relative_path"]
            source, target = root / relative, destination / relative
            if source.exists() and target.exists():
                raise ValueError("both archive and active directory exist")
            if source.exists():
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                source.rename(target)
            if not target.is_dir():
                raise ValueError("missing archive directory")
            move["status"] = "moved"
            write_json(journal_path, journal)
        verify_archive(destination)
        journal["status"] = "verified"
        journal["verified_at"] = now()
        write_json(journal_path, journal)
        write_json(pointer, {"path": str(destination), "created_at": journal["created_at"]})
    return journal


def verify_archive(destination):
    destination = Path(destination).resolve()
    journal = read_json(destination / "archive.json")
    if hash_file(destination / "state/scraper.sqlite3") != journal["database_sha256"]:
        raise ValueError("SQLite backup changed")
    for item in journal["files"]:
        path = (destination / item["relative_path"]).resolve()
        if not path.is_relative_to(destination) or not path.is_file():
            raise ValueError("missing or unsafe archived file")
        if path.stat().st_size != item["bytes"] or hash_file(path) != item["sha256"]:
            raise ValueError("archive checksum mismatch")
    return journal


def restore(root, destination):
    """Restore archived directories without overwriting active files or the live database."""
    root, destination = Path(root).resolve(), Path(destination).resolve()
    journal = read_json(destination / "archive.json")
    if str(root) != journal["source_root"]:
        raise ValueError("archive belongs to another storage root")
    if legacy_processes():
        raise RuntimeError("legacy process is running")
    with StorageLock(root / "state/scraper.lock", timeout_seconds=0):
        # Preflight every move so a collision cannot cause a partial restoration.
        for move in journal["moves"]:
            relative = move["relative_path"]
            if relative not in LEGACY_PATHS:
                raise ValueError("unsafe archive movement")
            if (root / relative).exists() and (destination / relative).exists():
                raise ValueError("restore would overwrite an active directory")
        for item in journal["files"]:
            relative = item["relative_path"]
            if not (destination / relative).resolve().is_relative_to(destination):
                raise ValueError("unsafe archived file")
            if not (root / relative).resolve().is_relative_to(root):
                raise ValueError("unsafe restored file")
            path = destination / relative
            if not path.exists():
                path = root / relative
            if hash_file(path) != item["sha256"]:
                raise ValueError("cannot restore changed files")
        for move in reversed(journal["moves"]):
            relative = move["relative_path"]
            source, target = destination / relative, root / relative
            if source.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                source.rename(target)
            move["status"] = "restored"
            journal["status"] = "restoring"
            write_json(destination / "archive.json", journal)
        journal["status"] = "restored"
        write_json(destination / "archive.json", journal)
        (workspace(root) / "archive.json").unlink(missing_ok=True)
    return journal


def archived_release(root, document_id):
    """Minimal read-only compatibility for sources purged by the previous pipeline."""
    pointer = workspace(root) / "archive.json"
    if not pointer.exists():
        return None
    archive_root = Path(read_json(pointer)["path"])
    with readonly(archive_root / "state/scraper.sqlite3") as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract_releases'"
        ).fetchone():
            return None
        row = connection.execute(
            "SELECT manifest_json,status FROM contract_releases WHERE document_id=?",
            (document_id,),
        ).fetchone()
        if row is None or row["status"] != "complete":
            return None
        manifest = json.loads(row["manifest_json"])
    paths = []
    for contract in manifest["contracts"]:
        path = (archive_root / contract["path"]).resolve()
        if not path.is_relative_to(archive_root) or hash_file(path) != contract["sha256"]:
            raise ValueError("archived contract missing or changed")
        paths.append(path)
    return paths
