"""Freeze a uniform holdout before any new fitting or inspection of its contents."""

from __future__ import annotations

import random
import re
from pathlib import Path

from .archive import archive
from .common import digest, now, read_json, readonly, workspace, write_json


def freeze_split(rows, excluded, *, seed=20260920, holdout_size=25, development_size=30):
    eligible = sorted(
        [row for row in rows if row["available"] and row["document_id"] not in excluded],
        key=lambda r: r["document_id"],
    )
    if len(eligible) < holdout_size + development_size:
        raise ValueError("not enough independent originals for the frozen split")
    test = random.Random(seed).sample(eligible, holdout_size)
    test_ids = {r["document_id"] for r in test}
    rest = [r for r in eligible if r["document_id"] not in test_ids]
    development = random.Random(seed + 1).sample(rest, development_size)
    for field in ("document_id", "case_id", "sha256"):
        if {r[field] for r in test} & {r[field] for r in development}:
            raise ValueError("duplicate source or case crosses the split")
    return test, development


def exposed_documents(root, excluded_manifests=()):
    excluded = set()
    manifest_hashes = {}
    for path in excluded_manifests:
        path = Path(path).resolve()
        value = read_json(path)
        rows = value.get("documents")
        if not isinstance(rows, list) or any(
            not isinstance(r.get("document_id"), str) or not r["document_id"] for r in rows
        ):
            raise ValueError("invalid prior-exposure manifest")
        excluded.update(r["document_id"] for r in rows)
        manifest_hashes[str(path)] = digest(value)
    for work in root.glob("adhesion-*"):
        for name in ("holdout.json", "development.json", "development-existing.json"):
            path = work / name
            if path.exists():
                excluded.update(r["document_id"] for r in read_json(path).get("documents", []))
        for name in ("runs", "reference", "extractions"):
            folder = work / name
            if folder.is_dir():
                excluded.update(p.name for p in folder.iterdir() if p.is_dir())
    return excluded, manifest_hashes


def prepare(root, batch_id, *, seed=20260920, excluded_manifests=()):
    root = Path(root).resolve()
    work = workspace(root)
    exposed, manifest_hashes = exposed_documents(root, excluded_manifests)
    if (work / "preparation.json").exists():
        previous = read_json(work / "preparation.json")
        if (
            previous["batch_id"] != batch_id
            or previous["seed"] != seed
            or previous.get("exclusion_manifests", {}) != manifest_hashes
        ):
            raise ValueError("another corpus or seed is already frozen")
        # Restoring and preparing again must not resample the untouched holdout.
        if work == root / "adhesion-v1":
            previous["archive"] = archive(root)["path"]
            write_json(work / "preparation.json", previous)
        return previous
    legacy = root / "adhesion-v1"
    if work != legacy and (legacy / "preparation.json").exists():
        prior = read_json(legacy / "preparation.json")
        if prior["batch_id"] != batch_id:
            raise ValueError("new cohort must use the existing prepared corpus")
        journal = read_json(legacy / "archive.json")
    else:
        journal = archive(root)
    backup = Path(journal["path"])
    with readonly(backup / "state/scraper.sqlite3") as connection:
        rows = [
            dict(r)
            for r in connection.execute(
                "SELECT DISTINCT d.document_id,d.case_id,d.relative_path,d.sha256,d.page_count "
                "FROM documents d JOIN dataset_records r ON r.case_id=d.case_id "
                "JOIN batch_items b ON b.record_id=r.record_id WHERE b.batch_id=? "
                "ORDER BY d.document_id",
                (batch_id,),
            )
        ]
        tables = {
            r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        excluded = set(exposed)
        for table in ("contract_page_reviews", "card_automation_runs", "card_validation_reviews"):
            if table in tables:
                excluded.update(
                    r[0] for r in connection.execute(f"SELECT document_id FROM {table}")
                )
    for relative in ("card-work", "card-validation"):
        if (backup / relative).is_dir():
            excluded.update(
                p.name
                for p in (backup / relative).iterdir()
                if p.is_dir() and re.fullmatch(r"[0-9a-f-]{36}", p.name)
            )
    # Notes and regression tests are also prior exposure, even without a DB review.
    project = Path(__file__).resolve().parents[3]
    for directory in (project / "docs", project / "tests"):
        for path in directory.rglob("*"):
            if path.suffix in (".md", ".py", ".json"):
                excluded.update(
                    re.findall(
                        r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b",
                        path.read_text(errors="replace"),
                    )
                )
    for row in rows:
        row["available"] = (root / row["relative_path"]).is_file()
    if not rows or len({r["case_id"] for r in rows}) != len(rows):
        raise ValueError("corpus must contain one original per case")
    holdout, development = freeze_split(rows, excluded, seed=seed)
    write_json(work / "archive.json", journal)
    corpus = {"batch_id": batch_id, "documents": rows, "sha256": digest(rows)}
    write_json(work / "corpus.json", corpus)
    for name, selected in (("holdout", holdout), ("development", development)):
        write_json(
            work / f"{name}.json",
            {
                "role": name,
                "seed": seed if name == "holdout" else seed + 1,
                "method": "uniform_without_replacement",
                "created_at": now(),
                "corpus_sha256": corpus["sha256"],
                "documents": selected,
                "selection_sha256": digest(selected),
            },
        )
    write_json(
        work / "development-existing.json",
        {
            "role": "development",
            "documents": [r for r in rows if r["available"] and r["document_id"] in excluded],
        },
    )
    write_json(
        work / "missing.json",
        {"documents": [r for r in rows if not r["available"]], "status": "recovery_deferred"},
    )
    result = {
        "created_at": now(),
        "batch_id": batch_id,
        "seed": seed,
        "exclusion_manifests": manifest_hashes,
        "archive": str(backup),
        "corpus": len(rows),
        "available": sum(r["available"] for r in rows),
        "excluded_prior_exposure": sorted(excluded & {r["document_id"] for r in rows}),
        "holdout_count": len(holdout),
        "development_count": len(development),
        "holdout_selection_sha256": digest(holdout),
    }
    write_json(work / "preparation.json", result)
    return result
