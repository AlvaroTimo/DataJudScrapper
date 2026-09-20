"""Measure automatic predictions against explicit manual validation evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .card_automation import initialize_automation_schema
from .card_model import canonical_hash, private_json
from .contract_catalog import connect_catalog
from .contract_redaction import pixel_box
from .pdf_validation import hash_file
from .runtime import utc_now


def verified_manifest(run):
    path = Path(run["manifest_path"])
    if hash_file(path) != run["manifest_sha256"]:
        raise ValueError("manifiesto automatico modificado")
    manifest = json.loads(path.read_text())
    if manifest["run_id"] != run["run_id"]:
        raise ValueError("el manifiesto pertenece a otra ejecucion")
    for contract in manifest["contracts"]:
        if hash_file(Path(contract["output"]["path"])) != contract["output"]["sha256"]:
            raise ValueError("PDF limpio de validacion modificado")
    return manifest


def validate_view_coverage(views, source_pages, manifest):
    expected = {("source", None, p) for p in range(1, source_pages + 1)}
    for contract in manifest["contracts"]:
        expected.update(
            ("cleaned", contract["contract_id"], p)
            for p in range(1, contract["output"]["page_count"] + 1)
        )
    observed = {(v["scope"], v["contract_id"], v["page"]) for v in views}
    if observed != expected or len(views) != len(expected):
        raise ValueError("faltan paginas fuente o limpias, o hay vistas duplicadas")


def validation_members(root):
    manifest = json.loads((root / "reports/card-automation/validation-set.json").read_text())
    if manifest["size"] != 100 or len(manifest["documents"]) != 100:
        raise ValueError("la validacion debe contener exactamente 100 expedientes")
    ids = {r["document_id"] for r in manifest["documents"]}
    if len(ids) != 100:
        raise ValueError("la muestra de validacion contiene duplicados")
    return manifest, ids


def render_validation(root, document_id, *, dpi=140):
    """Create a hash-bound review packet; rendering is not a manual approval."""
    import pymupdf

    _, ids = validation_members(root)
    if document_id not in ids:
        raise ValueError("fuente ajena a los 100 expedientes de validacion")
    with connect_catalog(root / "state/scraper.sqlite3") as connection:
        initialize_automation_schema(connection)
        source = connection.execute(
            "SELECT * FROM documents WHERE document_id=?",
            (document_id,),
        ).fetchone()
        run = connection.execute(
            "SELECT * FROM card_automation_runs WHERE document_id=? "
            "AND status IN ('automatic_checks_passed','needs_review') "
            "ORDER BY finished_at DESC LIMIT 1",
            (document_id,),
        ).fetchone()
    if not run:
        raise ValueError("no hay ejecucion automatica terminada")
    path = root / source["relative_path"]
    if hash_file(path) != run["source_sha256"]:
        raise ValueError("original de validacion modificado")
    manifest = verified_manifest(run)
    folder = root / "card-validation" / document_id / run["run_id"]
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    views = []
    tasks = [("source", None, path)] + [
        ("cleaned", c["contract_id"], Path(c["output"]["path"])) for c in manifest["contracts"]
    ]
    for scope, contract_id, pdf_path in tasks:
        pdf_hash = hash_file(pdf_path)
        with pymupdf.open(pdf_path) as pdf:
            for page in pdf:
                image = folder / f"{scope}-{contract_id or 'source'}-{page.number + 1:05d}.png"
                page.get_pixmap(dpi=dpi, alpha=False).save(image)
                image.chmod(0o600)
                views.append(
                    {
                        "scope": scope,
                        "contract_id": contract_id,
                        "page": page.number + 1,
                        "path": str(image),
                        "sha256": hash_file(image),
                        "pdf_sha256": pdf_hash,
                    }
                )
    packet = {
        "document_id": document_id,
        "run_id": run["run_id"],
        "source_sha256": run["source_sha256"],
        "manifest_sha256": run["manifest_sha256"],
        "dpi": dpi,
        "views": views,
        "manual_review": "pending",
    }
    private_json(folder / "packet.json", packet)
    return folder / "packet.json"


def record_validation(root: Path, review: dict):
    _, ids = validation_members(root)
    document_id = review["document_id"]
    if (
        document_id not in ids
        or review.get("method") != "manual_visual"
        or not review.get("reviewer")
    ):
        raise ValueError("revision manual invalida o fuera de la muestra")
    packet_path = Path(review["packet_path"])
    packet = json.loads(packet_path.read_text())
    if packet["document_id"] != document_id or packet["run_id"] != review["run_id"]:
        raise ValueError("las vistas pertenecen a otra ejecucion")
    observed = review["observed_views"]
    expected = {canonical_hash(v): v for v in packet["views"]}
    if set(observed) != set(expected) or len(observed) != len(expected):
        raise ValueError("faltan paginas fuente o limpias inspeccionadas manualmente")
    for value in expected.values():
        if hash_file(Path(value["path"])) != value["sha256"]:
            raise ValueError("vista de validacion modificada")
    assessment = review["assessment"]
    keys = (
        "missed_contracts",
        "false_contracts",
        "wrong_boundaries",
        "residual_pii_pages",
        "damaged_contract_pages",
    )
    if any(type(assessment.get(key)) is not int or assessment[key] < 0 for key in keys):
        raise ValueError("las metricas deben ser recuentos enteros no negativos")
    if type(assessment.get("complete")) is not bool or not assessment["complete"]:
        raise ValueError("la revision de este expediente no esta completa")
    gold = review["gold_contracts"]
    for contract in gold:
        if not contract.get("regions"):
            raise ValueError("contrato de referencia sin regiones")
        for region in contract["regions"]:
            pixel_box(region["rect"], 1000, 1000)
    with connect_catalog(root / "state/scraper.sqlite3") as connection:
        initialize_automation_schema(connection)
        run = connection.execute(
            "SELECT * FROM card_automation_runs WHERE run_id=? AND document_id=?",
            (review["run_id"], document_id),
        ).fetchone()
        if (
            not run
            or run["source_sha256"] != packet["source_sha256"]
            or run["manifest_sha256"] != packet["manifest_sha256"]
            or hash_file(Path(run["manifest_path"])) != packet["manifest_sha256"]
        ):
            raise ValueError("la ejecucion cambio desde la inspeccion")
        source_pages = sum(v["scope"] == "source" for v in expected.values())
        output_pages = sum(v["scope"] == "cleaned" for v in expected.values())
        source = connection.execute(
            "SELECT page_count,relative_path FROM documents WHERE document_id=?",
            (document_id,),
        ).fetchone()
        if hash_file(root / source["relative_path"]) != packet["source_sha256"]:
            raise ValueError("original de validacion modificado")
        manifest = verified_manifest(run)
        validate_view_coverage(packet["views"], source["page_count"], manifest)
        for contract in gold:
            if contract.get("kind") not in (
                "adhesion",
                "general_conditions",
                "card_operation",
                "card_excerpt",
            ):
                raise ValueError("tipo de contrato de referencia invalido")
            for region in contract["regions"]:
                if (
                    type(region.get("page")) is not int
                    or not 1 <= region["page"] <= source["page_count"]
                ):
                    raise ValueError("pagina de referencia fuera del expediente")
        if assessment["missed_contracts"] > len(gold):
            raise ValueError("omisiones superiores a los contratos de referencia")
        if assessment["false_contracts"] > len(manifest["contracts"]):
            raise ValueError("falsos positivos superiores a los resultados extraidos")
        if not any(assessment[k] for k in keys) and len(gold) != len(manifest["contracts"]):
            raise ValueError("los recuentos de contratos no justifican una validacion sin errores")
        with connection:
            connection.execute(
                "INSERT OR REPLACE INTO card_validation_reviews VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    document_id,
                    review["run_id"],
                    packet["source_sha256"],
                    packet["manifest_sha256"],
                    "manual_visual",
                    review["reviewer"],
                    utc_now().isoformat(),
                    source_pages,
                    output_pages,
                    json.dumps(gold),
                    json.dumps(assessment),
                    json.dumps(packet),
                ),
            )


def metrics(root: Path):
    selection, ids = validation_members(root)
    with connect_catalog(root / "state/scraper.sqlite3") as connection:
        initialize_automation_schema(connection)
        all_rows = connection.execute(
            "SELECT v.*,r.configuration_sha256,r.status,r.manifest_path "
            "FROM card_validation_reviews v "
            "JOIN card_automation_runs r USING(run_id) "
            "WHERE NOT EXISTS (SELECT 1 FROM card_automation_runs newer "
            "WHERE newer.document_id=r.document_id AND newer.started_at>r.started_at) "
            "ORDER BY reviewed_at"
        ).fetchall()
        latest = {r["document_id"]: r for r in all_rows if r["document_id"] in ids}
        counts = {}
        for status, count in connection.execute(
            "SELECT d.card_automation_status,COUNT(*) FROM documents d WHERE d.document_id IN ("
            + ",".join("?" for _ in ids)
            + ") GROUP BY d.card_automation_status",
            tuple(ids),
        ):
            counts[status] = count
    error_keys = (
        "missed_contracts",
        "false_contracts",
        "wrong_boundaries",
        "residual_pii_pages",
        "damaged_contract_pages",
    )
    totals = dict.fromkeys(error_keys, 0)
    output_count = 0
    for row in latest.values():
        manifest = verified_manifest(row)
        output_count += len(manifest["contracts"])
        assessment = json.loads(row["assessment_json"])
        for key in totals:
            totals[key] += assessment[key]
    gold_count = sum(len(json.loads(r["gold_contracts_json"])) for r in latest.values())
    configurations = {r["configuration_sha256"] for r in latest.values()}
    passed = (
        len(latest) == 100
        and len(configurations) == 1
        and not any(totals.values())
        and all(r["status"] == "automatic_checks_passed" for r in latest.values())
    )
    report = {
        "created_at": utc_now().isoformat(),
        "scope": selection["scope"],
        "selection_sha256": canonical_hash(selection),
        "required_documents": 100,
        "manually_validated_documents": len(latest),
        "automatic_status_counts": counts,
        "gold_contracts": gold_count,
        "predicted_contracts": output_count,
        "errors": totals,
        "recall": (gold_count - totals["missed_contracts"]) / gold_count if gold_count else None,
        "precision": (output_count - totals["false_contracts"]) / output_count
        if output_count
        else None,
        "passed": passed,
        "configuration_sha256": next(iter(configurations)) if len(configurations) == 1 else None,
        "note": "La revision manual de 100 casos mide el resultado; no prueba ausencia "
        "universal de errores. Los casos pendientes o fallidos no cuentan como negativos.",
    }
    private_json(root / "reports/card-automation/validation-report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-root", type=Path, default=Path("data"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("metrics")
    render = sub.add_parser("render")
    render.add_argument("document_id")
    record = sub.add_parser("record")
    record.add_argument("review", type=Path)
    args = parser.parse_args()
    root = args.storage_root.resolve()
    if args.command == "metrics":
        print(json.dumps(metrics(root), ensure_ascii=False, indent=2))
    elif args.command == "render":
        print(render_validation(root, args.document_id))
    else:
        record_validation(root, json.loads(args.review.read_text()))


if __name__ == "__main__":
    main()
