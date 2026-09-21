"""Honest denominators: abstentions and omissions never become correct negatives."""

from __future__ import annotations

from pathlib import Path

from ..pdf_validation import hash_file
from .common import digest, now, read_json, workspace, write_json
from .pipeline import state_connection, verify_manifest


def current_run(root, document_id, signature):
    with state_connection(root) as state:
        row = state.execute(
            "SELECT * FROM runs WHERE document_id=? AND configuration_sha256=? AND role='holdout'",
            (document_id, signature),
        ).fetchone()
    if row is None or not row["manifest_path"]:
        return None
    if hash_file(Path(row["manifest_path"])) != row["manifest_sha256"]:
        raise ValueError("run manifest changed")
    return verify_manifest(row["manifest_path"])


def record_output_review(root, assessment):
    work = workspace(root)
    frozen = read_json(work / "frozen-configuration.json")
    run = current_run(root, assessment["document_id"], frozen["configuration_sha256"])
    if run is None or run["run_id"] != assessment["run_id"]:
        raise ValueError("review must refer to frozen holdout run")
    if assessment.get("method") != "ai_assisted_visual" or not assessment.get("reviewer"):
        raise ValueError("explicit AI visual provenance required")
    expected = {i["contract_id"]: i for i in run["instruments"]}
    if set(expected) != {i["contract_id"] for i in assessment["instruments"]}:
        raise ValueError("review every output, including quarantine")
    for item in assessment["instruments"]:
        if type(item.get("uncertain", False)) is not bool:
            raise ValueError("invalid review uncertainty")
        output = expected[item["contract_id"]]["output"]
        if item["output_sha256"] != output["sha256"]:
            raise ValueError("reviewed output changed")
        if item["observed_pages"] != list(range(1, output["page_count"] + 1)):
            raise ValueError("all output pages require visual review")
        for key in ("residual_pii_pages", "damaged_content_pages", "foreign_content_pages"):
            if any(
                type(n) is not int or n not in item["observed_pages"] for n in item.get(key, [])
            ):
                raise ValueError("invalid output assessment page")
    path = work / "runs" / run["document_id"] / run["run_id"]
    manifest_file = path / ("error.json" if run["status"] == "error" else "manifest.json")
    result = {
        **assessment,
        "configuration_sha256": frozen["configuration_sha256"],
        "manifest_sha256": hash_file(manifest_file),
        "created_at": now(),
        "human_review": False,
    }
    write_json(work / "reviews" / f"{run['document_id']}.json", result)
    return result


def score_document(gold, run, review):
    reference = gold["instruments"]
    predicted = run["instruments"]
    assessments = {i["contract_id"]: i for i in review["instruments"]}
    matched, good, delivered_good = set(), 0, 0
    false, boundaries, residual_pages, damaged_pages = 0, 0, 0, 0
    delivered = sum(i["status"] == "completed" for i in predicted)
    for item in predicted:
        quality = assessments[item["contract_id"]]
        residual_pages += len(quality["residual_pii_pages"])
        damaged_pages += len(quality["damaged_content_pages"])
        target = next(
            (
                n
                for n, g in enumerate(reference)
                if item["pages"] in g.get("occurrences", [g["pages"]]) and n not in matched
            ),
            None,
        )
        if target is None:
            if any(
                set(item["pages"]) & set(occurrence)
                for g in reference
                for occurrence in g.get("occurrences", [g["pages"]])
            ):
                boundaries += 1
            else:
                false += 1
            continue
        matched.add(target)
        useful = (
            not quality["residual_pii_pages"]
            and not quality["damaged_content_pages"]
            and not quality.get("foreign_content_pages")
            and not quality.get("uncertain", False)
            and item["status"] == "completed"
        )
        good += useful
        delivered_good += useful
    return {
        "gold": len(reference),
        "produced": len(predicted),
        "delivered": delivered,
        "exact_extractions": len(matched),
        "useful": good,
        "delivered_good": delivered_good,
        "missed_or_incomplete": len(reference) - len(matched),
        "false_outputs": false,
        "wrong_boundaries": boundaries,
        "residual_pii_pages": residual_pages,
        "damaged_content_pages": damaged_pages,
        "quarantined": len(predicted) - delivered,
        "clean_outputs": sum(
            not a["residual_pii_pages"] and not a.get("uncertain", False)
            for a in assessments.values()
        ),
        "preserved_outputs": sum(
            not a["damaged_content_pages"] and not a.get("uncertain", False)
            for a in assessments.values()
        ),
        "uncertain_outputs": sum(a.get("uncertain", False) for a in assessments.values()),
        "foreign_content_pages": sum(
            len(a.get("foreign_content_pages", [])) for a in assessments.values()
        ),
        "status": run["status"],
        "source_uncertain": bool(gold.get("uncertain_pages")),
    }


def evaluate(root):
    work = workspace(root)
    selection = read_json(work / "holdout.json")
    frozen = read_json(work / "frozen-configuration.json")
    if (
        frozen["selection_sha256"] != selection["selection_sha256"]
        or digest(selection["documents"]) != selection["selection_sha256"]
        or len(selection["documents"]) != 25
    ):
        raise ValueError("holdout selection changed")
    documents, pending = [], []
    for source in selection["documents"]:
        identifier = source["document_id"]
        gold_path = work / "reference" / identifier / "gold.json"
        review_path = work / "reviews" / f"{identifier}.json"
        run = current_run(root, identifier, frozen["configuration_sha256"])
        if not gold_path.exists() or not review_path.exists() or run is None:
            pending.append(identifier)
            continue
        gold, review = read_json(gold_path), read_json(review_path)
        if (
            gold["source_sha256"] != source["sha256"]
            or not gold["complete"]
            or gold["configuration_sha256"] != frozen["configuration_sha256"]
            or review["run_id"] != run["run_id"]
            or review["configuration_sha256"] != frozen["configuration_sha256"]
        ):
            raise ValueError("evaluation evidence belongs to another source/version")
        if hash_file(gold_path.with_name("packet.json")) != gold["packet_sha256"]:
            raise ValueError("source reference evidence changed")
        manifest_file = (
            work
            / "runs"
            / identifier
            / run["run_id"]
            / ("error.json" if run["status"] == "error" else "manifest.json")
        )
        if hash_file(manifest_file) != review["manifest_sha256"]:
            raise ValueError("reviewed extraction manifest changed")
        documents.append({"document_id": identifier, **score_document(gold, run, review)})
    totals = {
        key: sum(row[key] for row in documents)
        for key in (
            "gold",
            "produced",
            "delivered",
            "exact_extractions",
            "useful",
            "delivered_good",
            "missed_or_incomplete",
            "false_outputs",
            "wrong_boundaries",
            "residual_pii_pages",
            "damaged_content_pages",
            "quarantined",
            "clean_outputs",
            "preserved_outputs",
            "uncertain_outputs",
            "foreign_content_pages",
        )
    }
    coverage = totals["useful"] / totals["gold"] if totals["gold"] else None
    reliability = totals["delivered_good"] / totals["delivered"] if totals["delivered"] else None
    passed = (
        not pending
        and not any(row["source_uncertain"] for row in documents)
        and coverage is not None
        and coverage >= 0.8
        and reliability is not None
        and reliability >= 0.8
    )
    result = {
        "created_at": now(),
        "configuration_sha256": frozen["configuration_sha256"],
        "selection_sha256": selection["selection_sha256"],
        "required_documents": 25,
        "reviewed_documents": len(documents),
        "pending": pending,
        "totals": totals,
        "useful_coverage": coverage,
        "delivered_reliability": reliability,
        "extraction_recall": totals["exact_extractions"] / totals["gold"]
        if totals["gold"]
        else None,
        "extraction_precision": totals["exact_extractions"] / totals["produced"]
        if totals["produced"]
        else None,
        "cleaning_rate": totals["clean_outputs"] / totals["produced"]
        if totals["produced"]
        else None,
        "preservation_rate": totals["preserved_outputs"] / totals["produced"]
        if totals["produced"]
        else None,
        "positive_processes": sum(row["gold"] > 0 for row in documents),
        "negative_processes": sum(row["gold"] == 0 for row in documents),
        "false_positive_processes": sum(
            row["gold"] == 0 and row["produced"] > 0 for row in documents
        ),
        "processes_needing_review": sum(row["status"] == "needs_review" for row in documents),
        "error_processes": sum(row["status"] == "error" for row in documents),
        "inconclusive_source_processes": sum(row["source_uncertain"] for row in documents),
        "passed": passed,
        "documents": documents,
        "human_review": False,
        "note": "25 uniformly sampled cases; report positive denominators. "
        "AI-assisted visual review, not a human privacy certification. "
        "Zero positives is inconclusive. No failed cases were replaced.",
    }
    write_json(work / "evaluation.json", result)
    write_report(work, result)
    return result


def write_report(work, result):
    def percentage(value):
        return "inconcluso" if value is None else f"{value:.1%}"

    totals = result["totals"]
    conclusion = "cumple ambos objetivos" if result["passed"] else "no habilita el lote completo"
    lines = [
        "# Piloto independiente de termos de adesão",
        "",
        f"Resultado: **{conclusion}**.",
        f"Procesos revisados: {result['reviewed_documents']}/25. "
        f"Pendientes: {len(result['pending'])}. Positivos: {result['positive_processes']}.",
        "",
        f"- Cobertura útil: {percentage(result['useful_coverage'])} "
        f"({totals['useful']}/{totals['gold']} termos reales).",
        f"- Fiabilidad de entregados: {percentage(result['delivered_reliability'])} "
        f"({totals['delivered_good']}/{totals['delivered']} salidas aceptadas).",
        f"- Recuperación de extracción: {percentage(result['extraction_recall'])}; "
        f"precisión: {percentage(result['extraction_precision'])}.",
        f"- Limpieza: {percentage(result['cleaning_rate'])}; "
        f"conservación: {percentage(result['preservation_rate'])} "
        "sobre todas las salidas producidas, incluida cuarentena.",
        f"- Salidas en cuarentena: {totals['quarantined']}; páginas con residuos: "
        f"{totals['residual_pii_pages']}; páginas con daño: {totals['damaged_content_pages']}.",
        f"- Procesos retenidos por la automatización: {result['processes_needing_review']}; "
        f"errores de ejecución: {result['error_processes']}.",
        f"- Referencias originales inconclusas: {result['inconclusive_source_processes']}; "
        f"salidas con dudas visuales sin resolver: {totals['uncertain_outputs']}.",
        "",
        "Muestra uniforme sin reemplazo, semilla 20260920, congelada antes del desarrollo. "
        "Los negativos no aumentan la cobertura y no se sustituyeron casos difíciles. "
        "Pocos positivos limitan la evidencia; cero positivos es inconcluso.",
        "",
        "Referencia visual asistida por IA, no revisión humana. Todas las páginas originales "
        "se presentan al modelo de referencia antes de las predicciones; los casos señalados "
        "y todas las salidas requieren adjudicación. Los modelos pueden compartir errores.",
        "",
        f"Configuración: `{result['configuration_sha256']}`.",
        f"Muestra: `{result['selection_sha256']}`.",
        "",
        "| Proceso (ID interno) | Termos reales | Extracciones exactas | Útiles | "
        "Entregados | Cuarentena | Evidencia |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in result["documents"]:
        identifier = row["document_id"]
        lines.append(
            f"| {identifier} | {row['gold']} | {row['exact_extractions']} | {row['useful']} | "
            f"{row['delivered']} | {row['quarantined']} | "
            f"[Fuente](reference/{identifier}/gold.json), [salidas](reviews/{identifier}.json) |"
        )
    if result["pending"]:
        lines.extend(["", "Pendientes: " + ", ".join(result["pending"]) + "."])
    path = work / "report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf8")
    path.chmod(0o600)
