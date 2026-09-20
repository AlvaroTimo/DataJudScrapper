"""Profile the discovery corpus and freeze an independent 100-case validation set."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .card_detection import SCOPE, candidate_pages
from .card_model import canonical_hash, private_json
from .contract_catalog import connect_catalog
from .contract_inventory import normalize
from .runtime import utc_now


def source_rows(connection, batch_id):
    return [
        dict(r)
        for r in connection.execute(
            """SELECT DISTINCT d.document_id,d.sha256,d.relative_path,d.page_count,
        d.source_disposition,s.inventory_path,s.ocr_pages,s.ocr_failures,
        s.attachment_count,s.candidate_attachment_count,
        (SELECT COUNT(*) FROM contract_page_reviews pr
          WHERE pr.document_id=d.document_id) AS previous_reviewed_pages,
        r.raw_json FROM documents d JOIN contract_sources s USING(document_id)
        JOIN dataset_records r ON r.case_id=d.case_id
        JOIN batch_items b ON b.record_id=r.record_id WHERE b.batch_id=?
        ORDER BY d.document_id""",
            (batch_id,),
        )
    ]


def load_pages(path: Path):
    with gzip.open(path, "rt", encoding="utf8") as stream:
        return [json.loads(line) for line in stream]


def choose_validation(rows, count=100, seed=20260920):
    eligible = [r for r in rows if r["available"] and not r["development_example"]]
    if len(eligible) < count:
        raise ValueError("no hay suficientes fuentes independientes disponibles")
    groups = defaultdict(list)
    for row in eligible:
        groups[row["stratum"]].append(row)

    def rank(identifier):
        return hashlib.sha256(f"{seed}:{identifier}".encode()).digest()

    keys = sorted(groups, key=lambda key: rank(key))
    if count < len(keys):
        selected = sorted(eligible, key=lambda r: rank(r["document_id"]))[:count]
    else:
        quotas = {key: 1 for key in keys}
        remaining = count - len(keys)
        capacity = len(eligible) - len(keys)
        remainders = {}
        for key in keys:
            extra, fraction = divmod(remaining * (len(groups[key]) - 1), capacity or 1)
            quotas[key] += extra
            remainders[key] = fraction
        for key in sorted(keys, key=lambda k: (-remainders[k], rank(k)))[
            : count - sum(quotas.values())
        ]:
            quotas[key] += 1
        selected = [
            row
            for key in keys
            for row in sorted(groups[key], key=lambda r: rank(r["document_id"]))[: quotas[key]]
        ]
    return sorted(selected, key=lambda r: rank("order:" + r["document_id"]))


def profile(root: Path, batch_id: str, *, seed=20260920, development=()):
    report_root = root / "reports/card-automation"
    with connect_catalog(root / "state/scraper.sqlite3") as connection:
        sources = source_rows(connection, batch_id)
    seen = set()
    records = []
    for source in sources:
        if source["document_id"] in seen:
            continue
        seen.add(source["document_id"])
        available = bool(source["inventory_path"] and (root / source["relative_path"]).is_file())
        metadata = {}
        routed = None
        if available:
            path = Path(source["inventory_path"])
            metadata = json.loads(path.with_name("inventory.json").read_text())
            pages = load_pages(path)
            if len(pages) != source["page_count"]:
                raise ValueError("inventario incompleto")
            routed = len(candidate_pages(pages, metadata["attachments"]))
        titles = " ".join(normalize(a.get("title") or "") for a in metadata.get("attachments", []))
        direct_title = bool(re.search(r"adesao|regulamento|condicoes.*cartao", titles))
        raw = json.loads(source["raw_json"])
        year = raw.get("process_year")
        size = (
            "small"
            if source["page_count"] <= 150
            else ("medium" if source["page_count"] <= 350 else "large")
        )
        ocr = "scan" if source["ocr_pages"] / source["page_count"] >= 0.5 else "text"
        stratum = f"{'2026' if year == 2026 else 'older'}:{size}:{ocr}:{direct_title}"
        records.append(
            {
                "document_id": source["document_id"],
                "source_sha256": source["sha256"],
                "page_count": source["page_count"],
                "ocr_pages": source["ocr_pages"],
                "available": available,
                "source_disposition": source["source_disposition"],
                "process_year": year,
                "stratum": stratum,
                "routed_pages": routed,
                "development_example": bool(
                    source["previous_reviewed_pages"] or source["document_id"] in development
                ),
            }
        )
    corpus = {
        "scope": SCOPE,
        "batch_id": batch_id,
        "created_at": utc_now().isoformat(),
        "documents": records,
        "count": len(records),
        "available": sum(r["available"] for r in records),
        "pages": sum(r["page_count"] for r in records),
        "routed_pages": sum(r["routed_pages"] or 0 for r in records),
    }
    private_json(report_root / "corpus.json", corpus)
    validation_path = report_root / "validation-set.json"
    if validation_path.exists():
        manifest = json.loads(validation_path.read_text())
        if manifest["batch_id"] != batch_id or manifest["scope"] != SCOPE:
            raise ValueError("ya existe una validacion congelada de otro lote o alcance")
    else:
        selected = choose_validation(records, 100, seed)
        manifest = {
            "scope": SCOPE,
            "batch_id": batch_id,
            "seed": seed,
            "size": 100,
            "created_at": utc_now().isoformat(),
            "method": "stratified_independent_holdout",
            "corpus_sha256": canonical_hash(records),
            "documents": selected,
            "selection_note": "Excluye fuentes usadas en desarrollo o ya descartadas. "
            "Estratifica por epoca, longitud, OCR y titulo contractual. "
            "No equivale a una muestra uniforme de todo el dataset.",
        }
        private_json(validation_path, manifest)
    print(
        json.dumps(
            {
                "corpus": corpus["count"],
                "available": corpus["available"],
                "routed_pages": corpus["routed_pages"],
                "validation": len(manifest["documents"]),
                "validation_pages": sum(r["page_count"] for r in manifest["documents"]),
                "strata": dict(Counter(r["stratum"] for r in manifest["documents"])),
            }
        )
    )
    return corpus, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_id")
    parser.add_argument("--storage-root", type=Path, default=Path("data"))
    parser.add_argument("--development-document", action="append", default=[])
    args = parser.parse_args()
    profile(args.storage_root.resolve(), args.batch_id, development=args.development_document)


if __name__ == "__main__":
    main()
