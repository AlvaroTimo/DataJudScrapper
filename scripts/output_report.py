"""Simple extraction-only output of a batch, comparable across runs.

- data/output/100-pdfs/                  the input expedientes, shared by every report
- data/output/reportN/well-extracted/    every contract the pipeline extracted
                                         (review and move the wrong ones)
- data/output/reportN/poorly-extracted/  expedientes with a real contract that was not
                                         extracted: the pages where it is
- data/output/reportN/well-extracted/extra-terms/
                                         outside the adhesion scope and the percentage: in
                                         expedientes without a term, a whole annex that is
                                         the bank's generic card contract or a card report.
                                         Deleting the folder undoes it.

Real contracts come from data/evaluacion/referencia_contratos.json. Anonymization is ignored.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

import pymupdf

from datajud_scraper.adhesion.boundaries import heading_lines
from datajud_scraper.adhesion.common import workspace_scope
from datajud_scraper.adhesion.inventory import open_inventory

SCOPE = {
    "instrument_scope_or_boundary_uncertainty",
    "scope_evidence_conflict",
    "possible_unincluded_continuation",
    "out_of_scope_instrument",
}


EXTRA = {
    "contrato-generico": re.compile(r"^contrato do cartao(?: de credito)?\b"),
    "relatorio-sistema": re.compile(r"^relatorio de cartao de credito\b"),
}


def extra_terms(root: Path, source):
    """Annexes opening with the bank's generic card contract or a card system report."""
    found = []
    with open_inventory(root, source) as pages:
        for annex in pages.attachments:
            first, last = annex["start_page"], annex["end_page"]
            if last - first >= 40:
                continue
            for number in range(first, min(first + 4, last + 1)):
                lines = heading_lines(pages[number - 1])[:3]
                kind = next((k for k, rule in EXTRA.items() if any(map(rule.search, lines))), None)
                if kind:
                    found.append((first, last, kind))
                    break
    return found


def place(source: Path, target: Path):
    shutil.copy2(source, target)


def span(pages):
    return f"p{pages[0]}-{pages[-1]}" if len(pages) > 1 else f"p{pages[0]}"


def runs(pages):
    """Contiguous page ranges, so a PDF part can be built with a few inserts."""
    result = []
    for number in pages:
        if result and number == result[-1][1] + 1:
            result[-1][1] = number
        else:
            result.append([number, number])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_id")
    parser.add_argument("--storage-root", type=Path, default=Path("data"))
    parser.add_argument("--inputs", default="100-pdfs", help="shared folder of input PDFs")
    parser.add_argument("--report", help="rewrite this existing report folder (e.g. report3)")
    args = parser.parse_args()
    root = args.storage_root.resolve()
    reference = root / "evaluacion" / "referencia_contratos.json"
    labels = json.loads(reference.read_text()) if reference.exists() else {}
    truth = labels.get("expedientes", {})
    base = root / "output"
    taken = [int(p.name[6:]) for p in base.glob("report*") if p.name[6:].isdigit()]
    folder = base / (args.report or f"report{max(taken, default=0) + 1}")
    inputs = base / args.inputs
    good, bad = folder / "well-extracted", folder / "poorly-extracted"
    extra = good / "extra-terms"
    for path in (base, folder, inputs, good, bad, extra):
        path.mkdir(mode=0o700, exist_ok=True)

    lines = (root / "reports" / args.batch_id / "results.jsonl").read_text().splitlines()
    entries = sorted(
        (json.loads(line) for line in lines if line.strip()), key=lambda e: e["position"]
    )
    batch = json.loads((root / "reports" / args.batch_id / "summary.json").read_text())
    total = len(entries)
    # Recovery is measured against the reference only when it labels the whole sample.
    labelled = all(e["process_number"] in truth for e in entries)
    real = extracted = contracts = 0
    rows, misses, extras = [], [], []
    with workspace_scope(root, "adhesion-scraper"):
        for entry in entries:
            position, process = entry["position"], entry["process_number"]
            phases = (entry["result"].get("contract_processing") or {}).get("phases", {})
            path = phases.get("extraction", {}).get("manifest_path")
            manifest = json.loads(Path(path).read_text()) if path and Path(path).exists() else None
            name = f"{position:03d}_{process}"
            if manifest is None:
                rows.append(f"| {position} | sin extracción | 0 | {entry['status']} |")
                continue
            detection = manifest.get("detection") or json.loads(
                (Path(path).parent / "detection.json").read_text()
            )
            source = root / manifest["source"]["relative_path"]
            if not (inputs / f"{name}.pdf").exists():
                place(source, inputs / f"{name}.pdf")
            instruments = manifest["instruments"]
            has_contract = truth.get(process) if labelled else None
            real += bool(has_contract)
            extracted += bool(instruments)
            contracts += len(instruments)
            for instrument in instruments:
                output = Path(instrument["output"]["path"])
                output = output if output.is_absolute() else root / output
                place(output, good / f"{name}_{span(instrument['pages'])}.pdf")
            annexes = [] if instruments else extra_terms(root, manifest["source"])
            for first, last, kind in annexes:
                with pymupdf.open(source) as pdf, pymupdf.open() as part:
                    part.insert_pdf(pdf, from_page=first - 1, to_page=last - 1)
                    part.save(extra / f"{name}_p{first}-{last}_{kind}.pdf")
                extras.append((position, kind))
            if not instruments and (has_contract or not labelled):
                items = detection.get("unresolved", []) + detection.get("rejected", [])
                items = [i for i in items if i["reason"] in SCOPE] or items
                pages = sorted({n for i in items for n in i["pages"]})[:60]
                if pages:
                    with pymupdf.open(source) as pdf, pymupdf.open() as part:
                        for first, last in runs(pages):
                            part.insert_pdf(pdf, from_page=first - 1, to_page=last - 1)
                        part.save(bad / f"{name}.pdf")
                else:
                    (bad / f"{name}_sin-paginas-candidatas.txt").write_text(
                        f"No se encontró ninguna página candidata. Expediente completo: "
                        f"../../{args.inputs}/{name}.pdf\n"
                    )
                reasons = ", ".join(sorted({i["reason"] for i in items})) or manifest["status"]
                spans = ", ".join(f"{a}-{b}" if a != b else str(a) for a, b in runs(pages))
                misses.append(f"| {position} | {spans or '—'} | {reasons} |")
            state = (
                "extraído"
                if instruments
                else "extra-terms"
                if annexes
                else "perdido"
                if has_contract
                else "sin contrato"
                if labelled
                else "no extraído"
            )
            rows.append(f"| {position} | {state} | {len(instruments)} | {manifest['status']} |")

    plus = extracted + len({p for p, _ in extras})
    table = [
        "| Input (expedientes) | Contrato extraído | % | Contrato extraído + extra-terms | % |",
        "| ---: | ---: | ---: | ---: | ---: |",
        f"| {total} | {extracted} ({contracts} contratos) | {100 * extracted / total:.0f} % "
        f"| {plus} ({contracts + len(extras)} documentos) | {100 * plus / total:.0f} % |",
        "",
        "% = expedientes con contrato extraído / input. Extra-terms (fuera del alcance de "
        "adhesión): contrato genérico del banco "
        f"{sum(k == 'contrato-generico' for _, k in extras)}, relatorio del sistema "
        f"{sum(k == 'relatorio-sistema' for _, k in extras)}, en `well-extracted/extra-terms/`.",
    ]
    summary = [
        f"# {folder.name} · lote {args.batch_id}",
        "",
        f"Muestra: {batch.get('selected')} expedientes · semilla {batch.get('seed')} · "
        f"dataset {batch.get('dataset_id', '')[:12]} · entrada en `../{args.inputs}/`",
        "",
        *table,
        "",
        f"- `../{args.inputs}/`: los expedientes de entrada (compartidos por esta muestra).",
        "- `well-extracted/`: contratos extraídos (decisión automática; mover aquí abajo los "
        "que estén mal).",
        "- `well-extracted/extra-terms/`: fuera del alcance de adhesión; borrar la carpeta "
        "lo deshace.",
        "- `poorly-extracted/`: por cada expediente no extraído, las páginas donde el "
        "pipeline vio algo (máx. 60).",
        "",
        "## No extraídos",
        "",
        "| # | Páginas candidatas | Motivo |",
        "| ---: | --- | --- |",
        *misses,
        "",
        "## Por expediente",
        "",
        "| # | Resultado | Contratos | Estado del pipeline |",
        "| ---: | --- | ---: | --- |",
        *rows,
    ]
    (folder / "resumen.md").write_text("\n".join(summary) + "\n")
    print(folder)


if __name__ == "__main__":
    main()
