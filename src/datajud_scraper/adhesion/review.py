"""Render every output page and its exact source for visual adjudication."""

from __future__ import annotations

from ..pdf_validation import hash_file
from .common import read_json, source_path, workspace, write_json
from .evaluation import current_run


def render_review(root, document_id):
    import pymupdf

    work = workspace(root)
    frozen = read_json(work / "frozen-configuration.json")
    run = current_run(root, document_id, frozen["configuration_sha256"])
    if run is None:
        raise ValueError("no completed pilot run for this source")
    source = next(
        r for r in read_json(work / "holdout.json")["documents"] if r["document_id"] == document_id
    )
    folder = work / "review-views" / document_id / run["run_id"]
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    views = []
    with pymupdf.open(source_path(root, source)) as original:
        for instrument in run["instruments"]:
            with pymupdf.open(instrument["output"]["path"]) as cleaned:
                for page, number in zip(cleaned, instrument["pages"], strict=True):
                    pair = {
                        "contract_id": instrument["contract_id"],
                        "source_page": number,
                        "output_page": page.number + 1,
                    }
                    for kind, image_page in (("source", original[number - 1]), ("cleaned", page)):
                        path = (
                            folder / f"{instrument['contract_id']}-{page.number + 1:03d}-{kind}.png"
                        )
                        if not path.exists():
                            image_page.get_pixmap(dpi=150, alpha=False).save(path)
                            path.chmod(0o600)
                        pair[kind] = {"path": str(path), "sha256": hash_file(path)}
                    views.append(pair)
    packet = {
        "document_id": document_id,
        "run_id": run["run_id"],
        "views": views,
        "rendering_is_not_review": True,
    }
    write_json(folder / "packet.json", packet)
    return packet
