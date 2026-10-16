"""Simple extraction-only output of a batch, comparable across runs.

- data/output/100-pdfs/                  the input expedientes, shared by every report
- data/output/reportN/well-extracted/    every contract the pipeline extracted, split by
                                         how it was found: indices/ (the annex title in the
                                         index), primera_pagina/ (the annex first page),
                                         buscar_texto/ (keyword search in other annexes),
                                         contratos_genericos/ (text rule, no model)
- data/output/reportN/poorly-extracted/  expedientes with no contract extracted, split by
                                         why: no_esta_en_el_pdf/ (no page mentions a term),
                                         documentos_parecidos/ (pages titled like a term that
                                         are not one), solo_mencionado/ (only cited in the
                                         text), falla_descarga/ (the PDF was not downloaded)
                                         In expedientes without a term, a whole annex that
                                         is the bank's generic card contract also counts.

Real contracts come from data/evaluacion/referencia_contratos.json. Anonymization is ignored.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

import pymupdf

from datajud_scraper.adhesion.boundaries import heading_lines, instrument_evidence
from datajud_scraper.adhesion.common import workspace_scope
from datajud_scraper.adhesion.inventory import body_text, heading_text, open_inventory

SCOPE = {
    "instrument_scope_or_boundary_uncertainty",
    "scope_evidence_conflict",
    "possible_unincluded_continuation",
    "out_of_scope_instrument",
}


ROUTES = ("indices", "primera_pagina", "buscar_texto", "contratos_genericos")
MISSING = ("no_esta_en_el_pdf", "documentos_parecidos", "solo_mencionado", "falla_descarga")
MENTION = re.compile(r"(?:termo|proposta|contrato) de ades[a4]o")
CARD = re.compile(r"cart[a4]o")
GENERIC = re.compile(r"^contrato do cartao(?: de credito)?\b")


def generic_contracts(root: Path, source):
    """Annexes opening with the bank's generic card contract (Nubank, Itaucard, PAN)."""
    found = []
    with open_inventory(root, source) as pages:
        for annex in pages.attachments:
            first, last = annex["start_page"], annex["end_page"]
            if last - first >= 40:
                continue
            if any(
                GENERIC.search(line)
                for number in range(first, min(first + 4, last + 1))
                for line in heading_lines(pages[number - 1])[:3]
            ):
                found.append((first, last))
    return found


def why_missing(root: Path, source):
    """Why no contract came out: never mentioned, only cited, or look-alike documents."""
    mentioned = False
    with open_inventory(root, source) as pages:
        for page in pages:
            if instrument_evidence(page):
                return "documentos_parecidos"
            text = heading_text(page) + " " + body_text(page)
            mentioned = mentioned or bool(MENTION.search(text) and CARD.search(text))
    return "solo_mencionado" if mentioned else "no_esta_en_el_pdf"


def route(detection, pages):
    """How the pipeline reached a contract: its annex title, its first page or the text."""
    origins = detection.get("page_origins") or {}
    if not detection.get("index_valid") or any(
        origins.get(str(n), origins.get(n)) != "index" for n in pages
    ):
        return "buscar_texto"
    annex = next(
        (
            e
            for e in detection.get("routing_evidence") or []
            if e["start_page"] <= pages[0] <= e["end_page"]
        ),
        None,
    )
    return "indices" if annex and annex["score"] >= 4 else "primera_pagina"


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
    folders = (*(good / r for r in ROUTES), *(bad / m for m in MISSING))
    for path in (base, folder, inputs, good, bad, *folders):
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
    rows, misses, routes = [], [], []
    with workspace_scope(root, "adhesion-scraper"):
        for entry in entries:
            position, process = entry["position"], entry["process_number"]
            phases = (entry["result"].get("contract_processing") or {}).get("phases", {})
            path = phases.get("extraction", {}).get("manifest_path")
            manifest = json.loads(Path(path).read_text()) if path and Path(path).exists() else None
            name = f"{position:03d}_{process}"
            if manifest is None:
                (bad / "falla_descarga" / f"{name}.txt").write_text(
                    f"No se pudo descargar el PDF del PROJUDI ({entry['status']}); "
                    "se puede reintentar.\n"
                )
                misses.append(f"| {position} | falla_descarga | — | {entry['status']} |")
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
            for instrument in instruments:
                output = Path(instrument["output"]["path"])
                output = output if output.is_absolute() else root / output
                way = route(detection, instrument["pages"])
                routes.append(way)
                place(output, good / way / f"{name}_{span(instrument['pages'])}.pdf")
            generic = [] if instruments else generic_contracts(root, manifest["source"])
            for first, last in generic:
                routes.append("contratos_genericos")
                with pymupdf.open(source) as pdf, pymupdf.open() as part:
                    part.insert_pdf(pdf, from_page=first - 1, to_page=last - 1)
                    part.save(good / "contratos_genericos" / f"{name}_p{first}-{last}.pdf")
            found = len(instruments) + len(generic)
            extracted += bool(found)
            contracts += found
            if not found and (has_contract or not labelled):
                why = why_missing(root, manifest["source"])
                items = detection.get("unresolved", []) + detection.get("rejected", [])
                items = [i for i in items if i["reason"] in SCOPE] or items
                pages = sorted({n for i in items for n in i["pages"]})[:60]
                if pages:
                    with pymupdf.open(source) as pdf, pymupdf.open() as part:
                        for first, last in runs(pages):
                            part.insert_pdf(pdf, from_page=first - 1, to_page=last - 1)
                        part.save(bad / why / f"{name}.pdf")
                else:
                    (bad / why / f"{name}_sin-paginas-candidatas.txt").write_text(
                        f"No se encontró ninguna página candidata. Expediente completo: "
                        f"../../../{args.inputs}/{name}.pdf\n"
                    )
                reasons = ", ".join(sorted({i["reason"] for i in items})) or manifest["status"]
                spans = ", ".join(f"{a}-{b}" if a != b else str(a) for a, b in runs(pages))
                misses.append(f"| {position} | {why} | {spans or '—'} | {reasons} |")
            state = (
                "extraído"
                if instruments
                else "contrato genérico"
                if generic
                else "perdido"
                if has_contract
                else "sin contrato"
                if labelled
                else "no extraído"
            )
            rows.append(f"| {position} | {state} | {found} | {manifest['status']} |")

    table = [
        "| Input (expedientes) | Contrato extraído | % de extracción |",
        "| ---: | ---: | ---: |",
        f"| {total} | {extracted} ({contracts} contratos) | {100 * extracted / total:.0f} % |",
        "",
        "% = expedientes con contrato extraído / input. Incluye el contrato genérico del "
        "banco (`contratos_genericos/`) en expedientes sin termo de adhesión.",
        "",
        "Por método: " + " · ".join(f"`{r}/` {routes.count(r)}" for r in ROUTES),
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
        "que estén mal), separados por cómo se encontraron: `indices/` (título del anexo), "
        "`primera_pagina/` (1ª página del anexo), `buscar_texto/` (palabras clave), "
        "`contratos_genericos/` (contrato general del banco, regla de texto sin IA).",
        "- `poorly-extracted/`: por cada expediente no extraído, las páginas donde el "
        "pipeline vio algo (máx. 60), separados por motivo: `no_esta_en_el_pdf/` (ninguna "
        "página menciona el termo), `documentos_parecidos/` (páginas con título de adhesión "
        "que no son el termo), `solo_mencionado/` (solo citado en el texto), "
        "`falla_descarga/`.",
        "",
        "## No extraídos",
        "",
        "Por categoría: "
        + " · ".join(f"`{m}/` {sum(f'| {m} |' in row for row in misses)}" for m in MISSING),
        "",
        "| # | Categoría | Páginas candidatas | Motivo del pipeline |",
        "| ---: | --- | --- | --- |",
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
