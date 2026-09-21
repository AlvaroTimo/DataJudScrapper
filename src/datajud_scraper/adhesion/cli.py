"""Prepare, execute and evaluate the complete-adhesion pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .common import read_json, workspace


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-root", type=Path, default=Path("data"))
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser(
        "prepare", help="archive legacy outputs and freeze independent samples"
    )
    prepare.add_argument("--batch", required=True)
    prepare.add_argument("--seed", type=int, default=20260920)
    sub.add_parser("freeze", help="freeze code, model and rules before blind evaluation")
    run = sub.add_parser("run", help="run a development, holdout or corpus manifest")
    select = run.add_mutually_exclusive_group(required=True)
    select.add_argument("--manifest", type=Path)
    select.add_argument("--document")
    sub.add_parser("reference", help="blind AI visual review of EVERY source page of the holdout")
    sub.add_parser(
        "review", help="render original/cleaned pairs; rendering is not review"
    ).add_argument("--document", required=True)
    for name in ("record-source", "record-output"):
        sub.add_parser(name).add_argument("assessment", type=Path)
    sub.add_parser("evaluate", help="report actual 25-case metrics and publication eligibility")
    sub.add_parser("verify-backup")
    sub.add_parser("restore-backup").add_argument("archive", type=Path)
    args = parser.parse_args(argv)
    root = args.storage_root.resolve()
    if args.command == "prepare":
        from .prepare import prepare

        result = prepare(root, args.batch, seed=args.seed)
    elif args.command == "freeze":
        from .pipeline import freeze
        from .vision import LocalModel

        model = LocalModel(workspace(root) / "model-cache")
        try:
            result = freeze(root, model)
        finally:
            model.close()
    elif args.command == "run":
        from .pipeline import configuration, run_document, run_manifest
        from .vision import LocalModel

        if args.manifest:
            return run_manifest(root, args.manifest)
        sources = read_json(workspace(root) / "corpus.json")["documents"]
        source = next((s for s in sources if s["document_id"] == args.document), None)
        if source is None:
            parser.error("document not in prepared corpus")
        test_ids = {
            r["document_id"] for r in read_json(workspace(root) / "holdout.json")["documents"]
        }
        model = LocalModel(workspace(root) / "model-cache")
        try:
            result = run_document(
                root,
                source,
                model,
                configuration(model),
                role="holdout" if args.document in test_ids else "development",
            )
        finally:
            model.close()
    elif args.command == "reference":
        from .reference import run_reference

        run_reference(root)
        return 0
    elif args.command == "record-source":
        from .reference import record_gold

        result = record_gold(root, read_json(args.assessment))
    elif args.command == "review":
        from .review import render_review

        result = render_review(root, args.document)
    elif args.command == "record-output":
        from .evaluation import record_output_review

        result = record_output_review(root, read_json(args.assessment))
    elif args.command == "evaluate":
        from .evaluation import evaluate

        result = evaluate(root)
    elif args.command == "verify-backup":
        from .archive import verify_archive

        result = verify_archive(read_json(workspace(root) / "archive.json")["path"])
        result = {"status": result["status"], "files": len(result["files"]), "path": result["path"]}
    else:
        from .archive import restore

        result = restore(root, args.archive)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
