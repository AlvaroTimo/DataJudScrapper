"""Anonymized copy of a report, split like data/output/reportN, with the counts.

- data/output_anonim/reportN/{indices,primera_pagina,buscar_texto}/  terms anonymized by
  the pipeline (scripts/reanonimizar.py summary), each in the folder of how it was found
- data/output_anonim/reportN/contratos_genericos/  the bank's generic card contracts: no
  client data, so only the court stamp at the foot of each page is covered
- data/output_anonim/reportN/resumen.md

"Anonymized" counts accepted (completed) plus needs_review: needs_review means the
automatic checks still have doubts, not that the page is wrong, and it is listed apart.
"""

from __future__ import annotations

import argparse
import json
import unicodedata
from collections import Counter
from pathlib import Path

import pymupdf
from output_report import ROUTES, generic_contracts, place, route, span

from datajud_scraper.adhesion.common import workspace_scope

STAMP = ("assinado eletronicamente", "codigo de validacao", "projudi")


def plain(text):
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def cover_stamp(page):
    """Black out the court stamp lines (signer name, validation code) at the page foot."""
    lines = {}
    for _x0, y0, _x1, y1, word, block, line, _ in page.get_text("words"):
        lines.setdefault((block, line), []).append((y0, y1, word))
    tops = [
        min(w[0] for w in words)
        for words in lines.values()
        if min(w[0] for w in words) > 0.85 * page.rect.height
        and any(mark in plain(" ".join(w[2] for w in words)) for mark in STAMP)
    ]
    if not tops:
        return False
    page.add_redact_annot(
        pymupdf.Rect(0, min(tops) - 2, page.rect.width, page.rect.height), fill=(0, 0, 0)
    )
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path, help="JSON written by scripts/reanonimizar.py")
    parser.add_argument("batch_id")
    parser.add_argument("--report", required=True, help="e.g. report3")
    parser.add_argument("--storage-root", type=Path, default=Path("data"))
    args = parser.parse_args()
    root = args.storage_root.resolve()
    folder = root / "output_anonim" / args.report
    for path in (folder.parent, folder, *(folder / r for r in ROUTES)):
        path.mkdir(mode=0o700, exist_ok=True)

    status = Counter()
    for entry in json.loads(args.summary.read_text()):
        manifest = json.loads(Path(entry["extraction_manifest"]).read_text())
        detection = manifest.get("detection") or json.loads(
            (Path(entry["extraction_manifest"]).parent / "detection.json").read_text()
        )
        pages = {i["contract_id"]: i["pages"] for i in manifest["instruments"]}
        name = f"{entry['position']:03d}_{entry['process_number']}"
        anonymized = {i["contract_id"]: i for i in entry["instruments"]}
        for contract, numbers in pages.items():
            way = route(detection, numbers)
            done = anonymized.get(contract)
            state = done["status"] if done else f"error ({entry['status']})"
            status[(way, state)] += 1
            if done and Path(done["output"]).exists():
                place(Path(done["output"]), folder / way / f"{name}_{span(numbers)}.pdf")

    lines = (root / "reports" / args.batch_id / "results.jsonl").read_text().splitlines()
    with workspace_scope(root, "adhesion-scraper"):
        for entry in (json.loads(line) for line in lines if line.strip()):
            phases = (entry["result"].get("contract_processing") or {}).get("phases", {})
            path = phases.get("extraction", {}).get("manifest_path")
            if not path or not Path(path).exists():
                continue
            manifest = json.loads(Path(path).read_text())
            if manifest["instruments"]:
                continue
            source = root / manifest["source"]["relative_path"]
            for first, last in generic_contracts(root, manifest["source"]):
                with pymupdf.open(source) as pdf, pymupdf.open() as part:
                    part.insert_pdf(pdf, from_page=first - 1, to_page=last - 1)
                    covered = sum(cover_stamp(page) for page in part)
                    name = f"{entry['position']:03d}_{entry['process_number']}"
                    part.save(folder / "contratos_genericos" / f"{name}_p{first}-{last}.pdf")
                state = "sello cubierto" if covered else "sin sello"
                status[("contratos_genericos", state)] += 1

    def count(way, *states):
        return sum(n for (w, s), n in status.items() if w == way and s in states)

    rows, totals = [], Counter()
    for way in ROUTES:
        ok = count(way, "completed")
        review = count(way, "needs_review")
        rule = count(way, "sello cubierto", "sin sello")
        total = sum(n for (w, _), n in status.items() if w == way)
        failed = total - ok - review - rule
        totals.update(total=total, ok=ok, review=review, rule=rule, failed=failed)
        rows.append(f"| `{way}/` | {total} | {ok} | {review} | {rule} | {failed} |")
    anonymized = totals["ok"] + totals["review"] + totals["rule"]
    summary = [
        f"# {args.report} anonimizado · lote {args.batch_id}",
        "",
        "| Contratos extraídos | Anonimizados | % |",
        "| ---: | ---: | ---: |",
        f"| {totals['total']} | {anonymized} | {100 * anonymized / totals['total']:.0f} % |",
        "",
        "Anonimizado = accepted + needs_review (pipeline) + contratos genéricos con el sello "
        "del tribunal cubierto. needs_review = los controles automáticos aún tienen dudas; "
        "no hay revisión humana.",
        "",
        "| Carpeta | Contratos | accepted | needs_review | genérico (regla) | error |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        *rows,
        f"| **Total** | **{totals['total']}** | **{totals['ok']}** | **{totals['review']}** "
        f"| **{totals['rule']}** | **{totals['failed']}** |",
    ]
    (folder / "resumen.md").write_text("\n".join(summary) + "\n")
    print(folder)


if __name__ == "__main__":
    main()
