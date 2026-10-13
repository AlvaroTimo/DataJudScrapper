"""Build PDFs to check extraction and anonymization by eye, in two steps.

Step 1 puts every extracted contract of the batch in one PDF: its original pages with
context, then extracted|anonymized pairs. Step 2 renders, for a seeded sample of the cases
left in review, the doubtful pages with their neighbours, stamped with the role the model
gave them. revision_detallada.csv collects the verdicts; re-running keeps them and writes
the metrics to resumen_revision.md.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

import pymupdf
from sample_report import REASONS, answer

ROLES = {
    "A": "inicio",
    "B": "contrato de 1 página",
    "C": "continuación",
    "E": "final",
    "N": "otro documento",
    "F": "fragmento en escrito",
    "U": "dudosa",
}
CONTRACTS_PDF = "1_contratos_extraidos.pdf"
DOUBTS_DIR = "2_propuestas_dudosas"
COLUMNS = ["paso", "posicion", "proceso", "item", "tipo", "paginas", "detalle", "pdf_revision"]
MANUAL = [
    "rev_es_contrato",
    "rev_inicio_real",
    "rev_fin_real",
    "rev_datos_sensibles_visibles",
    "rev_contenido_contrato_borrado",
    "rev_notas",
]
BANNER = 26
CONTEXT, DOUBT, CONTRACT = (0.45, 0.45, 0.45), (0.75, 0.35, 0.0), (0.0, 0.4, 0.75)


def stamped(out, source, number, label, color):
    """Copy one page under a coloured banner; `number` is 1-based like the manifests."""
    rect = source[number - 1].rect
    page = out.new_page(width=rect.width, height=rect.height + BANNER)
    page.show_pdf_page(
        pymupdf.Rect(0, BANNER, rect.width, rect.height + BANNER), source, number - 1
    )
    page.draw_rect(pymupdf.Rect(0, 0, rect.width, BANNER), color=None, fill=color)
    page.insert_text((8, 18), label, fontsize=11, fontname="helv", color=(1, 1, 1))


def side_by_side(out, left, right, index, label):
    a, b = left[index].rect, right[index].rect
    page = out.new_page(width=a.width + b.width + 12, height=max(a.height, b.height) + BANNER)
    page.show_pdf_page(pymupdf.Rect(0, BANNER, a.width, BANNER + a.height), left, index)
    x = a.width + 12
    page.show_pdf_page(pymupdf.Rect(x, BANNER, x + b.width, BANNER + b.height), right, index)
    page.draw_rect(pymupdf.Rect(0, 0, page.rect.width, BANNER), color=None, fill=CONTRACT)
    page.insert_text((8, 18), label, fontsize=11, fontname="helv", color=(1, 1, 1))


def doubt_groups(detection):
    """Merge unresolved pages into contiguous groups, keeping each page's reasons."""
    reasons = {}
    for item in detection.get("unresolved", []):
        for number in item["pages"]:
            reasons.setdefault(number, []).append(item["reason"])
    groups = []
    for number in sorted(reasons):
        if groups and number == groups[-1][-1] + 1:
            groups[-1].append(number)
        else:
            groups.append([number])
    return groups, reasons


def role_names(extraction):
    roles = {int(k): v for k, v in extraction["detection"].get("page_roles", {}).items()}
    return roles, lambda number: ROLES.get(roles.get(number), "no candidata")


def add_contracts(out, toc, root, entry, extraction, anonymization):
    """Append the case's extracted contracts to the shared step-1 PDF."""
    _, role = role_names(extraction)
    anonymized = {i["contract_id"]: i for i in (anonymization or {}).get("instruments", [])}
    rows = []
    with pymupdf.open(root / extraction["source"]["relative_path"]) as source:
        for instrument in extraction["instruments"]:
            first, last = instrument["pages"][0], instrument["pages"][-1]
            item = instrument["contract_id"]
            done = anonymized.get(item)
            state = {"completed": "aceptado", "needs_review": "cuarentena"}.get(
                (done or {}).get("status"), "sin anonimizar"
            )
            title = f"Exp. {entry['position']} · contrato {item[-3:]} · p.{first}-{last}"
            toc.append([1, f"{title} ({state})", len(out) + 1])
            for number in range(max(1, first - 1), min(len(source), last + 1) + 1):
                inside = first <= number <= last
                label = f"Exp. {entry['position']} · ORIGINAL p.{number} · {role(number)} · " + (
                    "EXTRAÍDO" if inside else "contexto"
                )
                stamped(out, source, number, label, CONTRACT if inside else CONTEXT)
            if done:
                with (
                    pymupdf.open(instrument["output"]["path"]) as extracted,
                    pymupdf.open(done["output"]["path"]) as cleaned,
                ):
                    for index in range(min(len(extracted), len(cleaned))):
                        label = (
                            f"{title} · pág. {index + 1}: EXTRAÍDO (izq.) | "
                            f"ANONIMIZADO, {state} (der.)"
                        )
                        side_by_side(out, extracted, cleaned, index, label)
            rows.append(
                {
                    "paso": 1,
                    "posicion": entry["position"],
                    "proceso": entry["process_number"],
                    "item": item,
                    "tipo": "contrato_extraido",
                    "paginas": f"{first}-{last}",
                    "detalle": f"{instrument.get('family', '')} · {instrument.get('kind', '')} · "
                    f"anonimización {state}",
                    "pdf_revision": CONTRACTS_PDF,
                }
            )
    return rows


def doubts_case(root, folder, entry, extraction):
    """One step-2 PDF per case with its doubtful page groups and their neighbours."""
    roles, role = role_names(extraction)
    groups, reasons = doubt_groups(extraction["detection"])
    if not groups:
        return []
    name = f"{entry['position']:03d}-{entry['process_number']}.pdf"
    out, toc, rows = pymupdf.open(), [], []
    with pymupdf.open(root / extraction["source"]["relative_path"]) as source:
        for position, group in enumerate(groups, 1):
            first, last = group[0], group[-1]
            toc.append([1, f"Duda {position:02d} (p.{first}-{last})", len(out) + 1])
            for number in range(max(1, first - 1), min(len(source), last + 1) + 1):
                if number in reasons:
                    why = ", ".join(
                        sorted({REASONS.get(r, r).split(":")[0] for r in reasons[number]})
                    )
                    label = f"p.{number} · {role(number)} · DUDA: {why}"
                    stamped(out, source, number, label, DOUBT)
                else:
                    stamped(out, source, number, f"p.{number} · {role(number)} · contexto", CONTEXT)
            rows.append(
                {
                    "paso": 2,
                    "posicion": entry["position"],
                    "proceso": entry["process_number"],
                    "item": f"duda-{position:02d}",
                    "tipo": "propuesta_dudosa",
                    "paginas": f"{first}-{last}",
                    "detalle": "roles "
                    + " ".join(roles.get(n, "-") for n in group)
                    + " · "
                    + "; ".join(sorted({r for n in group for r in reasons[n]})),
                    "pdf_revision": f"{DOUBTS_DIR}/{name}",
                }
            )
    out.set_toc(toc)
    out.save(folder / name, garbage=3, deflate=True)
    (folder / name).chmod(0o600)
    out.close()
    return rows


def span(row):
    first, last = (int(v) for v in row["paginas"].split("-"))
    return first, last


def metrics(rows):
    def pct(part, whole):
        return f"{part}/{whole} ({100 * part / whole:.0f} %)" if whole else "0/0"

    reviewed = [r for r in rows if answer(r.get("rev_es_contrato")) is not None]
    contracts = [r for r in reviewed if r["tipo"] == "contrato_extraido"]
    proposals = [r for r in reviewed if r["tipo"] == "propuesta_dudosa"]
    lines = ["# Revisión detallada de extracción y anonimización", ""]
    lines.append(f"Ítems revisados: {len(reviewed)} de {len(rows)}.")

    def bounds(items):
        exact, missing, extra, unknown = 0, 0, 0, 0
        for r in items:
            try:
                real = int(r["rev_inicio_real"]), int(r["rev_fin_real"])
            except (KeyError, ValueError):
                unknown += 1
                continue
            first, last = span(r)
            exact += real == (first, last)
            missing += real[0] < first or real[1] > last
            extra += first < real[0] or last > real[1]
        return exact, missing, extra, unknown

    real = [r for r in contracts if answer(r["rev_es_contrato"])]
    exact, missing, extra, unknown = bounds(real)
    sensitive = [r for r in real if answer(r.get("rev_datos_sensibles_visibles"))]
    erased = [r for r in real if answer(r.get("rev_contenido_contrato_borrado"))]
    clean = [
        r
        for r in real
        if answer(r.get("rev_datos_sensibles_visibles")) is False
        and answer(r.get("rev_contenido_contrato_borrado")) is False
    ]
    lines += [
        "",
        "## Paso 1 · Contratos extraídos",
        "",
        f"- Son realmente un termo de adhesión: {pct(len(real), len(contracts))}",
        f"- Límites exactos: {pct(exact, len(real))} · faltan páginas: {missing} · "
        f"sobran páginas: {extra} · sin límites anotados: {unknown}",
        f"- Anonimización correcta (sin datos sensibles y sin borrar contrato): "
        f"{pct(len(clean), len(real))}",
        f"- Con datos sensibles visibles: {len(sensitive)} · "
        f"con contenido del contrato borrado: {len(erased)}",
    ]
    hits = [r for r in proposals if answer(r["rev_es_contrato"])]
    exact, missing, extra, unknown = bounds(hits)
    lines += [
        "",
        "## Paso 2 · Propuestas dudosas (el pipeline no las extrajo)",
        "",
        f"- Son un termo de adhesión: {pct(len(hits), len(proposals))} "
        "(contratos que el pipeline dejó sin extraer)",
        f"- Falsas alarmas: {len(proposals) - len(hits)}",
        f"- Aceptarlas tal cual acertaría los límites en: {pct(exact, len(hits))} · "
        f"faltan páginas: {missing} · sobran: {extra} · sin límites anotados: {unknown}",
    ]
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_id")
    parser.add_argument(
        "--documentos", type=int, default=5, help="casos en revisión para el paso 2 (5)"
    )
    parser.add_argument("--semilla", default="20261011")
    parser.add_argument("--storage-root", type=Path, default=Path("data"))
    args = parser.parse_args()
    root = args.storage_root.resolve()
    output = root / "reports" / args.batch_id / "evaluacion" / "revision_detallada"
    doubts = output / DOUBTS_DIR
    doubts.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Only this tool writes PDFs here; regenerate them instead of mixing old layouts.
    for stale in [*output.glob("*.pdf"), *doubts.glob("*.pdf")]:
        stale.unlink()

    cases = []
    for line in (root / "reports" / args.batch_id / "results.jsonl").read_text().splitlines():
        entry = json.loads(line)
        phases = (entry["result"].get("contract_processing") or {}).get("phases", {})
        if "extraction" not in phases:
            continue
        extraction = json.loads(Path(phases["extraction"]["manifest_path"]).read_text())
        anonymization = None
        if "anonymization" in phases:
            anonymization = json.loads(Path(phases["anonymization"]["manifest_path"]).read_text())
        cases.append((entry, extraction, anonymization))

    rows, toc, contracts = [], [], pymupdf.open()
    for entry, extraction, anonymization in cases:
        if extraction["instruments"]:
            rows += add_contracts(contracts, toc, root, entry, extraction, anonymization)
    if len(contracts):
        contracts.set_toc(toc)
        contracts.save(output / CONTRACTS_PDF, garbage=3, deflate=True)
        (output / CONTRACTS_PDF).chmod(0o600)
    contracts.close()

    sample = sorted(
        (c for c in cases if c[1]["status"] == "needs_review"),
        key=lambda c: hashlib.sha256(f"{args.semilla}:{c[0]['process_number']}".encode()).digest(),
    )[: args.documentos]
    for entry, extraction, _ in sorted(sample, key=lambda c: c[0]["position"]):
        rows += doubts_case(root, doubts, entry, extraction)

    sheet = output / "revision_detallada.csv"
    previous = {}
    if sheet.exists():
        with sheet.open(newline="", encoding="utf-8-sig") as stream:
            delimiter = ";" if ";" in stream.readline().split(",")[0] else ","
            stream.seek(0)
            previous = {
                (r["proceso"], r["item"]): r for r in csv.DictReader(stream, delimiter=delimiter)
            }
    for row in rows:
        old = previous.get((row["proceso"], row["item"]), {})
        row.update({column: old.get(column, "") for column in MANUAL})
    temporary = sheet.with_suffix(".part")
    with temporary.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, COLUMNS + MANUAL, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(sheet)
    (output / "resumen_revision.md").write_text("\n".join(metrics(rows)) + "\n")
    kinds = Counter(r["tipo"] for r in rows)
    print(f"Paso 1: {output / CONTRACTS_PDF} ({kinds['contrato_extraido']} contratos)")
    print(f"Paso 2: {doubts}/ ({kinds['propuesta_dudosa']} propuestas dudosas)")
    print(sheet)


if __name__ == "__main__":
    main()
