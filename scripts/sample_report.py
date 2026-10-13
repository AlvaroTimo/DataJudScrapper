from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

WORKSPACE = "adhesion-scraper"
AUTO_COLUMNS = [
    "posicion",
    "proceso",
    "estado_descarga",
    "paginas_expediente",
    "estado_extraccion",
    "indice_valido",
    "pendientes_extraccion",
    "error_extraccion",
    "contratos_extraidos",
    "contrato_id",
    "familia",
    "tipo",
    "paginas_contrato",
    "estado_anonimizacion",
    "problemas_automaticos",
    "paginas_con_reparacion",
    "max_pasadas_por_pagina",
    "seg_total",
    "seg_extraccion",
    "seg_anonimizacion",
    "pdf_original",
    "pdf_extraido",
    "pdf_anonimizado",
]
MANUAL_COLUMNS = [
    "rev_contrato_real",
    "rev_limites_ok",
    "rev_contratos_omitidos",
    "rev_motivo_extraccion",
    "rev_anonimizacion_ok",
    "rev_motivo_anonimizacion",
    "rev_notas",
]
YES = {"si", "sí", "s", "yes", "y", "1", "x", "ok"}
NO = {"no", "n", "0"}


def seconds(manifest):
    if not manifest or not manifest.get("finished_at"):
        return None
    start = datetime.fromisoformat(manifest["started_at"])
    return round((datetime.fromisoformat(manifest["finished_at"]) - start).total_seconds(), 1)


def moment(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def phase_manifest(folder: Path, phase, runs):
    """The phase manifest of this batch item, not of another batch over the same PDF.

    Prefer the exact path recorded in results.jsonl (also covers verified reuse);
    otherwise take the latest error/in-progress snapshot started during the item's runs.
    """
    if phase and Path(phase["manifest_path"]).is_file():
        return json.loads(Path(phase["manifest_path"]).read_text())
    if not runs:
        return None
    since = min(moment(r["started_at"]) for r in runs)
    ends = [moment(r["finished_at"]) for r in runs]
    until = None if None in ends else max(ends)
    found = []
    for name in ("manifest.json", "error.json", "progress.json"):
        found += [(p.parent, p) for p in folder.rglob(name)]
    best = {}
    for parent, path in found:
        best.setdefault(parent, path)  # manifest > error > progress for the same generation
    manifests = [
        m
        for m in (json.loads(p.read_text()) for p in best.values())
        if since <= moment(m["started_at"]) and (until is None or moment(m["started_at"]) <= until)
    ]
    return max(manifests, key=lambda m: moment(m["started_at"]), default=None)


def model_label(work: Path, signature):
    path = work / "extraction-configurations" / f"{signature}.json"
    if not signature or not path.is_file():
        return None
    config = json.loads(path.read_text())
    context = (config.get("inference") or {}).get("num_ctx")
    return f"{config.get('model')} · contexto {context} tokens" if context else config.get("model")


def page_problems(decision):
    checks = decision.get("checks", {})
    last = (checks.get("audits") or [{}])[-1]
    problems = []
    if last.get("pii_remaining"):
        problems.append("datos_personales_residuales")
    if last.get("content_preserved") is False:
        problems.append("contenido_no_conservado")
    if last.get("uncertain"):
        problems.append("incierto")
    if last.get("source_identifier_residuals"):
        problems.append("identificadores_residuales")
    if checks.get("protected_region_intersections"):
        problems.append("mascara_sobre_contenido_protegido")
    return problems


def count(value):
    try:
        return int(str(value or "0").strip())
    except ValueError:
        return 0


def answer(value):
    value = (value or "").strip().lower()
    return True if value in YES else False if value in NO else None


def stats(values):
    values = [v for v in values if v is not None]
    if not values:
        return "—"
    return (
        f"media {statistics.mean(values):.0f} s · mediana {statistics.median(values):.0f} s · "
        f"máx {max(values):.0f} s · total {sum(values) / 60:.1f} min (n={len(values)})"
    )


def build_rows(root: Path, batch_id: str):
    results = root / "reports" / batch_id / "results.jsonl"
    entries = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
    db = sqlite3.connect(f"file:{root / 'state' / 'scraper.sqlite3'}?mode=ro", uri=True)
    work = root / WORKSPACE
    rows, documents = [], []
    for entry in entries:
        result = entry["result"]
        document = db.execute(
            """SELECT d.document_id, d.relative_path, d.page_count FROM documents d
            JOIN cases c ON c.case_id = d.case_id WHERE c.process_number = ?
            ORDER BY d.retrieved_at DESC LIMIT 1""",
            (entry["process_number"],),
        ).fetchone()
        extraction = anonymization = None
        if document:
            phases = (result.get("contract_processing") or {}).get("phases", {})
            runs = entry.get("runs", [])
            extraction = phase_manifest(
                work / "extractions" / document[0], phases.get("extraction"), runs
            )
            anonymization = phase_manifest(
                work / "runs" / document[0], phases.get("anonymization"), runs
            )
        download = result.get("error_code") or result.get("status") or entry["status"]
        base = {
            "posicion": entry["position"],
            "proceso": entry["process_number"],
            "estado_descarga": (result.get("contract_processing") or {}).get(
                "download_status", download
            ),
            "paginas_expediente": result.get("page_count") or (document[2] if document else ""),
            "seg_total": None
            if entry["status"] in ("pending", "running")
            else entry.get("elapsed_seconds"),
            "pdf_original": str(root / document[1]) if document else "",
        }
        if extraction:
            detection = extraction.get("detection") or {}
            base.update(
                estado_extraccion=extraction["status"],
                indice_valido=detection.get("index_valid", ""),
                pendientes_extraccion="; ".join(
                    f"{u['reason']} p{u['pages'][0]}-{u['pages'][-1]}"
                    for u in detection.get("unresolved", [])
                ),
                error_extraccion=extraction.get("error_type", ""),
                contratos_extraidos=len(extraction["instruments"]),
                seg_extraccion=seconds(extraction),
                # Not exported to the sheet: inputs for the pipeline report.
                motivos=sorted({u["reason"] for u in detection.get("unresolved", [])}),
                modelo=model_label(work, extraction.get("configuration_sha256")),
            )
        if anonymization:
            base["seg_anonimizacion"] = seconds(anonymization)
        anonymized = {i["contract_id"]: i for i in (anonymization or {}).get("instruments", [])}
        documents.append(base)
        instruments = (extraction or {}).get("instruments", [])
        if not instruments:
            rows.append(base)
            continue
        for instrument in instruments:
            row = {
                **base,
                "contrato_id": instrument["contract_id"],
                "familia": instrument.get("family", ""),
                "tipo": instrument.get("kind", ""),
                "paginas_contrato": f"{instrument['pages'][0]}-{instrument['pages'][-1]}"
                f" ({len(instrument['pages'])})",
                "pdf_extraido": instrument["output"]["path"],
            }
            done = anonymized.get(instrument["contract_id"])
            if done:
                decisions = done.get("privacy", [])
                repairs = [d.get("checks", {}).get("repair_count", 0) for d in decisions]
                problems = Counter(p for d in decisions for p in page_problems(d))
                row.update(
                    estado_anonimizacion=done["status"],
                    problemas_automaticos="; ".join(f"{k} x{v}" for k, v in problems.items()),
                    paginas_con_reparacion=sum(1 for r in repairs if r),
                    max_pasadas_por_pagina=1 + max(repairs, default=0),
                    pdf_anonimizado=done["output"]["path"],
                )
            elif anonymization and anonymization["status"] == "error":
                row["estado_anonimizacion"] = f"error ({anonymization.get('error_type', '')})"
            rows.append(row)
    return rows, documents


def merge_manual(rows, path: Path):
    if not path.exists():
        return
    with path.open(newline="", encoding="utf-8-sig") as stream:
        # Spreadsheets in es/pt locales may save the sheet back with semicolons.
        delimiter = ";" if ";" in stream.readline().split(",")[0] else ","
        stream.seek(0)
        previous = {
            (r["proceso"], r.get("contrato_id", "")): r
            for r in csv.DictReader(stream, delimiter=delimiter)
        }
    for row in rows:
        old = previous.get((row["proceso"], row.get("contrato_id", "")), {})
        for column in MANUAL_COLUMNS:
            row[column] = old.get(column, "")


REASONS = {
    "visual_uncertainty": "páginas dudosas: el modelo no decide si son contrato",
    "region_uncertainty": "recorte no resuelto: el contrato comparte página con otro contenido",
    "orphan_continuation": "continuación de contrato sin página inicial",
    "incomplete_instrument": "contrato sin final confirmado",
    "instrument_scope_or_boundary_uncertainty": "límites del contrato inciertos",
    "scope_evidence_conflict": "título de adhesión que el modelo no confirma",
    "possible_unincluded_continuation": "posible continuación no incluida",
}
REFERENCE = Path("evaluacion") / "referencia_contratos.json"


def reference(root: Path):
    """Which expedientes really contain an adhesion contract, if someone labelled them."""
    path = root / REFERENCE
    if not path.exists():
        return None, None
    data = json.loads(path.read_text())
    return data["expedientes"], data.get("fuente", "")


def pipeline_report(rows, documents, root: Path):
    def pct(part, whole):
        return f"{100 * part / whole:.0f} %" if whole else "—"

    contracts = [r for r in rows if r.get("contrato_id")]
    by_document = {
        d["proceso"]: [c for c in contracts if c["proceso"] == d["proceso"]] for d in documents
    }
    pages = sum(count(d.get("paginas_expediente")) for d in documents)
    models = sorted({d["modelo"] for d in documents if d.get("modelo")})
    total = len(documents)

    def group(document):
        if not document.get("pdf_original"):
            return "descarga"
        status = document.get("estado_extraccion")
        if status in (None, "running"):
            return "sin_procesar"
        if status == "error":
            return "error"
        if by_document[document["proceso"]]:
            return "extraido"
        return "limpio" if status == "no_target" else "dudas"

    groups = {d["proceso"]: group(d) for d in documents}
    tally = Counter(groups.values())
    accepted = [c for c in contracts if c.get("estado_anonimizacion") == "completed"]
    pending = [c for c in contracts if not c.get("estado_anonimizacion")]
    quarantine = len(contracts) - len(accepted) - len(pending)

    lines = ["## Reporte del pipeline", ""]
    if models:
        lines += [f"Modelo: {', '.join(models)}", ""]
    lines += [
        f"**Entrada: {total} expedientes ({pages:,} páginas).**".replace(",", "."),
        "",
        "### Etapa 1 · Extracción: ¿el expediente trae un contrato de adhesión?",
        "",
        "| Resultado | Expedientes | % |",
        "| --- | ---: | ---: |",
    ]
    labels = {
        "extraido": f"Se extrajo contrato ({len(contracts)} contratos en total)",
        "limpio": "Sin contrato, cerrado limpio (`no_target`)",
        "dudas": "Sin contrato extraído, con páginas dudosas para revisar",
        "error": "Error técnico en la extracción",
        "descarga": "Falló la descarga",
        "sin_procesar": "Sin procesar todavía",
    }
    for key, label in labels.items():
        if tally[key]:
            lines.append(f"| {label} | {tally[key]} | {pct(tally[key], total)} |")
    lines += [f"| **Total** | **{total}** | 100 % |", ""]
    doubts = Counter(
        reason for d in documents if groups[d["proceso"]] == "dudas" for reason in d["motivos"]
    )
    if doubts:
        lines += ["Dudas de los expedientes sin contrato extraído (pueden tener varias):", ""]
        lines += [f"- {REASONS.get(k, k)}: {v}" for k, v in doubts.most_common()]
        lines.append("")
    lines += [
        f"### Etapa 2 · Anonimización de los {len(contracts)} contratos extraídos",
        "",
        "| Resultado | Contratos | % |",
        "| --- | ---: | ---: |",
        f"| Aceptado por los controles automáticos | {len(accepted)} | "
        f"{pct(len(accepted), len(contracts))} |",
        f"| En cuarentena (revisión humana) | {quarantine} | {pct(quarantine, len(contracts))} |",
    ]
    if pending:
        lines.append(f"| Sin anonimizar | {len(pending)} | {pct(len(pending), len(contracts))} |")
    lines.append("")

    truth, source = reference(root)
    labelled = [d for d in documents if truth and d["proceso"] in truth]
    if labelled:
        real = [d for d in labelled if truth[d["proceso"]]]
        found = [d for d in real if by_document[d["proceso"]]]
        lost = [d for d in real if not by_document[d["proceso"]]]
        false = [d for d in labelled if not truth[d["proceso"]] and by_document[d["proceso"]]]
        right = len(found) + len(labelled) - len(real) - len(false)
        lines += [
            "### Calidad de la extracción contra la referencia",
            "",
            f"Referencia: {source} ({len(labelled)}/{total} expedientes etiquetados).",
            "",
            "| | Expedientes | % |",
            "| --- | ---: | ---: |",
            f"| Con contrato real | {len(real)} | {pct(len(real), len(labelled))} |",
            f"| ↳ contrato extraído (**recuperación**) | **{len(found)}** | "
            f"**{pct(len(found), len(real))}** |",
            f"| ↳ contrato perdido | {len(lost)} | {pct(len(lost), len(real))} |",
            f"| Sin contrato real | {len(labelled) - len(real)} | "
            f"{pct(len(labelled) - len(real), len(labelled))} |",
            f"| ↳ con un “contrato” extraído (falso positivo) | {len(false)} | "
            f"{pct(len(false), len(labelled) - len(real))} |",
            f"| **Decisión correcta (hay / no hay contrato)** | **{right}** | "
            f"**{pct(right, len(labelled))}** |",
            "",
        ]
        if lost:
            lines.append(
                "Perdidos (#): " + ", ".join(str(d["posicion"]) for d in lost) + ". "
                "Falsos positivos (#): " + (", ".join(str(d["posicion"]) for d in false) or "—")
            )
            lines.append("")
    else:
        lines += [
            f"Sin referencia de contratos reales (`data/{REFERENCE}`): no se puede medir la "
            "recuperación ni los falsos positivos.",
            "",
        ]
    lines += [
        "| # | Proceso | Extracción | Contratos extraídos | Aceptados / cuarentena | Dudas |",
        "| ---: | --- | --- | ---: | --- | --- |",
    ]
    for document in documents:
        mine = by_document[document["proceso"]]
        ok = sum(c.get("estado_anonimizacion") == "completed" for c in mine)
        detail = "; ".join(REASONS.get(r, r) for r in document.get("motivos") or []) or "—"
        lines.append(
            f"| {document['posicion']} | {document['proceso']} | "
            f"{labels[groups[document['proceso']]].split(' (')[0]} | {len(mine)} | "
            f"{f'{ok} / {len(mine) - ok}' if mine else '—'} | {detail} |"
        )
    lines += [
        "",
        "Aceptado significa superar los controles automáticos, no la revisión humana.",
    ]
    return lines


def automatic_summary(rows, documents):
    lines = ["## Resultados automáticos del pipeline", ""]
    lines.append(f"- Expedientes en la muestra: **{len(documents)}**")
    downloads = Counter(d["estado_descarga"] for d in documents)
    lines.append("- Descarga: " + ", ".join(f"{k} {v}" for k, v in downloads.most_common()))
    extraction = Counter(d.get("estado_extraccion", "no_procesado") for d in documents)
    lines.append(
        "- Extracción por expediente: " + ", ".join(f"{k} {v}" for k, v in extraction.most_common())
    )
    index = Counter(
        str(d["indice_valido"]) for d in documents if d.get("indice_valido") not in (None, "")
    )
    if index:
        lines.append(
            "- Índice PDF válido: " + ", ".join(f"{k} {v}" for k, v in index.most_common())
        )
    contracts = [r for r in rows if r.get("contrato_id")]
    lines.append(f"- Contratos extraídos: **{len(contracts)}**")
    anonymization = Counter(r.get("estado_anonimizacion", "no_procesado") for r in contracts)
    if anonymization:
        lines.append(
            "- Anonimización por contrato (completed = aceptado, needs_review = cuarentena): "
            + ", ".join(f"{k} {v}" for k, v in anonymization.most_common())
        )
    problems = Counter()
    for row in contracts:
        for item in filter(None, (row.get("problemas_automaticos") or "").split("; ")):
            name, pages = item.rsplit(" x", 1)
            problems[name] += int(pages)
    if problems:
        lines.append(
            "- Problemas detectados por los controles (páginas): "
            + ", ".join(f"{k} {v}" for k, v in problems.most_common())
        )
    passes = Counter(
        r.get("max_pasadas_por_pagina") for r in contracts if r.get("estado_anonimizacion")
    )
    if passes:
        lines.append(
            "- Pasadas de anonimización (máx. por página, por contrato): "
            + ", ".join(f"{k} pasada(s) {v}" for k, v in sorted(passes.items()))
        )
    lines += [
        "",
        "### Tiempos por expediente",
        "",
        "- Total (descarga + esperas + procesamiento): "
        + stats(d.get("seg_total") for d in documents),
        f"- Extracción: {stats(d.get('seg_extraccion') for d in documents)}",
        "- Anonimización (solo expedientes con contratos): "
        + stats(d.get("seg_anonimizacion") for d in documents if d.get("contratos_extraidos")),
        "",
        "Los estados automáticos no equivalen a una revisión: un contrato `completed` puede",
        "tener datos personales residuales o contenido borrado.",
    ]
    return lines


def manual_summary(rows):
    reviewed = [r for r in rows if any(r.get(c) for c in MANUAL_COLUMNS)]
    if not reviewed:
        return ["## Revisión manual", "", "Pendiente: rellenar las columnas `rev_*`."]
    contracts = [r for r in rows if r.get("contrato_id")]
    real = [r for r in contracts if answer(r.get("rev_contrato_real")) is True]
    false_positive = [r for r in contracts if answer(r.get("rev_contrato_real")) is False]
    omitted = sum(count(r.get("rev_contratos_omitidos")) for r in rows)
    correct = [r for r in real if answer(r.get("rev_limites_ok")) is True]
    wrong_bounds = [r for r in real if answer(r.get("rev_limites_ok")) is False]
    clean = [r for r in correct if answer(r.get("rev_anonimizacion_ok")) is True]
    dirty = [r for r in correct if answer(r.get("rev_anonimizacion_ok")) is False]
    pending = [r for r in contracts if answer(r.get("rev_contrato_real")) is None]
    total_real = len(real) + omitted

    def pct(part, whole):
        return f"{part}/{whole} ({100 * part / whole:.0f} %)" if whole else f"{part}/0"

    def reasons(items, column):
        counts = Counter((r.get(column) or "sin motivo").strip() for r in items)
        return [f"  - {k}: {v}" for k, v in counts.most_common()]

    with_contract = {
        r["proceso"]
        for r in rows
        if answer(r.get("rev_contrato_real")) is True or count(r.get("rev_contratos_omitidos"))
    }
    lines = [
        "## Revisión manual",
        "",
        f"- Expedientes con al menos un termo de adhesión real: {len(with_contract)}",
        f"- Contratos reales: **{total_real}** ({len(real)} detectados + {omitted} omitidos)",
        f"- Extraídos correctamente: **{pct(len(correct), total_real)}**",
        f"- Falsos positivos (extraído, pero no es termo): {len(false_positive)}",
        f"- Contratos sin revisar: {len(pending)}",
        "",
        "### Fallos de extracción",
        "",
        f"- Omitidos: {omitted}",
        f"- Límites incorrectos: {len(wrong_bounds)}",
        "- Motivos:",
        *reasons(
            wrong_bounds
            + false_positive
            + [r for r in rows if count(r.get("rev_contratos_omitidos"))],
            "rev_motivo_extraccion",
        ),
        "",
        "### Anonimización (sobre los extraídos correctamente)",
        "",
        f"- Anonimizados correctamente: **{pct(len(clean), len(correct))}**",
        f"- Con problemas: {len(dirty)}",
        "- Motivos:",
        *reasons(dirty, "rev_motivo_anonimizacion"),
    ]
    return lines


def batch_model(root: Path, batch_id: str):
    for line in (root / "reports" / batch_id / "results.jsonl").read_text().splitlines():
        phase = (json.loads(line)["result"].get("contract_processing") or {}).get("phases", {})
        path = Path(phase.get("extraction", {}).get("manifest_path", ""))
        if path.is_file():
            signature = json.loads(path.read_text()).get("configuration_sha256")
            return model_label(root / WORKSPACE, signature)
    return None


def register(root: Path, batch_id: str) -> Path:
    """Stable sequential names next to the scraper's UUID folders: reportes/reporte_N.

    Numbers follow batch creation and are never reused, so a deleted batch leaves a gap;
    reportes/ultimo always points at the newest one.
    """
    folder = root / "reportes"
    folder.mkdir(exist_ok=True)
    index_path = folder / "indice.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    batches = sorted(
        (json.loads(p.read_text()) for p in (root / "reports").glob("*/summary.json")),
        key=lambda s: s["created_at"],
    )
    for summary in batches:
        index.setdefault(summary["batch_id"], max(index.values(), default=0) + 1)
    index_path.write_text(json.dumps(index, indent=2) + "\n")
    existing = {s["batch_id"]: s for s in batches}
    lines = [
        "# Reportes",
        "",
        "| Reporte | Creado (UTC) | Expedientes | Estado | Modelo | Lote |",
        "| --- | --- | ---: | --- | --- | --- |",
    ]
    for batch, number in sorted(index.items(), key=lambda item: item[1]):
        link = folder / f"reporte_{number}"
        summary = existing.get(batch)
        if summary is None:
            link.unlink(missing_ok=True)  # only the link: the batch folder is already gone
            lines.append(f"| reporte_{number} | — | — | lote borrado | — | `{batch}` |")
            continue
        if not link.is_symlink():
            link.symlink_to(Path("..") / "reports" / batch)
        created = summary["created_at"][:16].replace("T", " ")
        lines.append(
            f"| [reporte_{number}](reporte_{number}/evaluacion/resumen.md) | {created} | "
            f"{summary['selected']} | {summary['status']} | {batch_model(root, batch) or '—'} | "
            f"`{batch}` |"
        )
    if batches:
        latest = folder / "ultimo"
        latest.unlink(missing_ok=True)
        latest.symlink_to(f"reporte_{index[batches[-1]['batch_id']]}")
    (folder / "indice.md").write_text("\n".join(lines) + "\n")
    return folder / f"reporte_{index[batch_id]}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_id")
    parser.add_argument("--storage-root", type=Path, default=Path("data"))
    args = parser.parse_args()
    root = args.storage_root.resolve()
    output = root / "reports" / args.batch_id / "evaluacion"
    output.mkdir(exist_ok=True)
    rows, documents = build_rows(root, args.batch_id)
    sheet = output / "revision.csv"
    merge_manual(rows, sheet)
    temporary = sheet.with_suffix(".part")
    with temporary.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, AUTO_COLUMNS + MANUAL_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(sheet)
    lines = [f"# Evaluación del lote {args.batch_id}", ""]
    lines += pipeline_report(rows, documents, root) + [""]
    lines += automatic_summary(rows, documents) + [""] + manual_summary(rows)
    (output / "resumen.md").write_text("\n".join(lines) + "\n")
    print(sheet)
    print(output / "resumen.md")
    print(f"{register(root, args.batch_id)}  (último: {root / 'reportes' / 'ultimo'})")


if __name__ == "__main__":
    main()
