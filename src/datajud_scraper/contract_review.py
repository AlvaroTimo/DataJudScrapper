"""Operator-driven review records. Rendering is not itself a manual approval."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from pathlib import Path

from .contract_catalog import (
    audit,
    catalog_write,
    connect_catalog,
    initialize_contract_schema,
    refresh_case_contract_state,
    require_source_review,
)
from .contract_redaction import pixel_box, region_rotation, render_contract_page
from .pdf_validation import hash_file
from .runtime import utc_now


def source_record(connection, root: Path, document_id: str):
    source = connection.execute(
        "SELECT * FROM documents WHERE document_id=?", (document_id,)
    ).fetchone()
    if source is None or source["source_disposition"] != "retained":
        raise ValueError("fuente desconocida o ya descartada")
    path = (root / source["relative_path"]).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("archivo fuente ausente o fuera del almacenamiento")
    if hash_file(path) != source["sha256"]:
        raise ValueError("la fuente cambio desde su importacion")
    return source, path


@catalog_write
def save_view(
    connection, root, document_id, scope, page_number, image, source_sha, contract_id=None
):
    state = connection.execute(
        "SELECT source_disposition,sha256 FROM documents WHERE document_id=?", (document_id,)
    ).fetchone()
    if state is None or state["source_disposition"] != "retained" or state["sha256"] != source_sha:
        raise ValueError("la fuente ya no permite crear vistas sin limpiar")
    view_id = str(uuid.uuid4())
    folder = root / "contract-work" / document_id / "views"
    folder.mkdir(mode=0o750, parents=True, exist_ok=True)
    path = folder / f"{view_id}.png"
    image.save(path)
    path.chmod(0o640)
    image_sha = hash_file(path)
    pixels_sha = hashlib.sha256(image.tobytes()).hexdigest()
    with connection:
        connection.execute(
            "INSERT INTO contract_view_artifacts VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                view_id,
                document_id,
                contract_id,
                scope,
                page_number,
                source_sha,
                str(path.resolve()),
                image_sha,
                pixels_sha,
                utc_now().isoformat(),
            ),
        )
    return {
        "view_id": view_id,
        "page_number": page_number,
        "path": str(path.resolve()),
        "image_sha256": image_sha,
        "pixels_sha256": pixels_sha,
        "scope": scope,
    }


def render_source_views(connection, root: Path, document_id: str, pages: list[int], dpi=140):
    import pymupdf

    record, path = source_record(connection, root, document_id)
    views = []
    with pymupdf.open(path) as source:
        for page in pages:
            image = render_contract_page(source, {"page": page}, dpi=dpi)
            views.append(
                save_view(connection, root, document_id, "source", page, image, record["sha256"])
            )
    return views


def verify_view(connection, view_id, *, document_id, scope, page_number, source_sha):
    view = connection.execute(
        "SELECT * FROM contract_view_artifacts WHERE view_id=?", (view_id,)
    ).fetchone()
    if (
        view is None
        or view["document_id"] != document_id
        or view["scope"] != scope
        or view["page_number"] != page_number
        or view["source_sha256"] != source_sha
    ):
        raise ValueError("la evidencia visual no corresponde a la pagina o revision")
    path = Path(view["image_path"])
    if not path.is_file() or hash_file(path) != view["image_sha256"]:
        raise ValueError("la imagen revisada falta o cambio")
    return view


@catalog_write
def record_source_reviews(connection, root: Path, review: dict) -> None:
    from .contract_equivalence import verify_review_basis

    if review.get("method") != "manual_visual" or not review.get("reviewer"):
        raise ValueError("se requiere revision visual manual identificada")
    document_id = review["document_id"]
    source, _ = source_record(connection, root, document_id)
    checked = []
    for entry in review["pages"]:
        if entry["decision"] not in ("contract", "noncontract", "mixed", "uncertain"):
            raise ValueError("decision de pagina desconocida")
        if not entry.get("rationale", "").strip():
            raise ValueError("cada decision necesita justificacion")
        view = verify_view(
            connection,
            entry["view_id"],
            document_id=document_id,
            scope="source",
            page_number=entry["page"],
            source_sha=source["sha256"],
        )
        checked.append((entry, view, verify_review_basis(connection, entry, view)))
    with connection:
        for entry, view, evidence in checked:
            connection.execute(
                """INSERT INTO contract_page_reviews VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(document_id,page_number) DO UPDATE SET
                decision=excluded.decision,reviewer=excluded.reviewer,
                reviewed_at=excluded.reviewed_at,source_sha256=excluded.source_sha256,
                rendered_sha256=excluded.rendered_sha256,rationale=excluded.rationale""",
                (
                    document_id,
                    entry["page"],
                    entry["decision"],
                    review["reviewer"],
                    utc_now().isoformat(),
                    source["sha256"],
                    view["image_sha256"],
                    entry["rationale"],
                ),
            )
            connection.execute(
                "DELETE FROM contract_page_review_evidence WHERE document_id=? AND page_number=?",
                (document_id, entry["page"]),
            )
            if evidence:
                connection.execute(
                    "INSERT INTO contract_page_review_evidence VALUES "
                    "(?,?,'exact_body_match',?,?,?,?,?,?)",
                    (
                        document_id, entry["page"], evidence["reference_id"],
                        evidence["source_view_id"], evidence["remainder_view_id"],
                        evidence["body_sha256"], evidence["remainder_pixels_sha256"],
                        utc_now().isoformat(),
                    ),
                )
        # Any revised source decision invalidates downstream approvals.
        connection.execute(
            "UPDATE documents SET has_contract=NULL,contract_count=NULL,"
            "contracts_had_sensitive_data=NULL,contract_review_status='unreviewed' "
            "WHERE document_id=?",
            (document_id,),
        )
        refresh_case_contract_state(connection, source["case_id"])
        connection.execute(
            "UPDATE contracts SET extraction_status='candidate',redaction_status='unreviewed',"
            "had_sensitive_data=NULL WHERE document_id=?",
            (document_id,),
        )
        connection.execute(
            "DELETE FROM contract_output_reviews WHERE contract_id IN "
            "(SELECT contract_id FROM contracts WHERE document_id=?)",
            (document_id,),
        )
        audit(
            connection,
            "source_pages_manually_reviewed",
            document_id=document_id,
            pages=[entry["page"] for entry, _, _ in checked],
            review_bases={
                str(entry["page"]): entry.get("review_basis", "full_page")
                for entry, _, _ in checked
            },
            reviewer=review["reviewer"],
        )


@catalog_write
def approve_extraction(connection, root: Path, plan: dict) -> list[str]:
    if plan.get("method") != "manual_visual" or not plan.get("reviewer"):
        raise ValueError("se requiere una decision manual de extraccion")
    document_id = plan["document_id"]
    source, _ = source_record(connection, root, document_id)
    require_source_review(connection, document_id)
    positive_pages = {
        row[0]
        for row in connection.execute(
            "SELECT page_number FROM contract_page_reviews WHERE document_id=? "
            "AND decision IN ('contract','mixed')",
            (document_id,),
        )
    }
    contracts, covered, seen_regions = [], set(), set()
    for occurrence, contract in enumerate(plan["contracts"], 1):
        if not re.fullmatch(r"[a-z_]{3,50}", contract["kind"]):
            raise ValueError("tipo de contrato invalido; use una etiqueta sin datos personales")
        regions = contract["regions"]
        if not regions:
            raise ValueError("un contrato debe contener paginas")
        for region in regions:
            if type(region["page"]) is not int or region["page"] not in positive_pages:
                raise ValueError("se intenta extraer una pagina no aprobada como contrato")
            normalized_rect = region.get("rect", [0, 0, 1, 1])
            pixel_box(normalized_rect, 10000, 10000)
            region_rotation(region)
            identity = (region["page"], tuple(normalized_rect))
            if identity in seen_regions:
                raise ValueError("la misma region fuente se conto como dos contratos")
            seen_regions.add(identity)
            covered.add(region["page"])
        identifier = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{document_id}:{occurrence}:{json.dumps(regions, sort_keys=True)}",
            )
        )
        contracts.append((identifier, occurrence, contract))
    if covered != positive_pages:
        raise ValueError("quedan paginas contractuales revisadas fuera de la extraccion")
    existing = {
        row[0]
        for row in connection.execute(
            "SELECT contract_id FROM contracts WHERE document_id=?", (document_id,)
        )
    }
    if existing and existing != {item[0] for item in contracts}:
        raise ValueError("hay otra extraccion registrada; se requiere una revision explicita")
    now = utc_now().isoformat()
    with connection:
        for identifier, occurrence, contract in contracts:
            connection.execute(
                """INSERT INTO contracts(contract_id,document_id,occurrence,source_pages_json,
                source_regions_json,kind,extraction_status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,'approved',?,?) ON CONFLICT(contract_id) DO UPDATE SET
                extraction_status='approved',updated_at=excluded.updated_at""",
                (
                    identifier,
                    document_id,
                    occurrence,
                    json.dumps([region["page"] for region in contract["regions"]]),
                    json.dumps(contract["regions"]),
                    contract["kind"],
                    now,
                    now,
                ),
            )
        connection.execute(
            "UPDATE documents SET has_contract=?,contract_count=?,"
            "contract_review_status='approved',contracts_had_sensitive_data=CASE WHEN ?=0 "
            "THEN 0 ELSE contracts_had_sensitive_data END "
            "WHERE document_id=?",
            (int(bool(contracts)), len(contracts), len(contracts), document_id),
        )
        refresh_case_contract_state(connection, source["case_id"])
        audit(
            connection,
            "extraction_manually_approved",
            document_id=document_id,
            contract_count=len(contracts),
            reviewer=plan["reviewer"],
        )
    return [item[0] for item in contracts]


def parse_pages(value: str) -> list[int]:
    pages = set()
    for part in value.split(","):
        bounds = part.split("-")
        if len(bounds) == 1:
            pages.add(int(bounds[0]))
        elif len(bounds) == 2:
            start, end = map(int, bounds)
            if start < 1 or end < start:
                raise ValueError("rango de paginas invalido")
            pages.update(range(start, end + 1))
        else:
            raise ValueError("rango de paginas invalido")
    return sorted(pages)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Evidencia y decisiones manuales del piloto.")
    parser.add_argument("--storage-root", type=Path, default=Path("data"))
    sub = parser.add_subparsers(dest="command", required=True)
    render = sub.add_parser("render-source")
    render.add_argument("document_id")
    render.add_argument("pages", help="paginas fisicas: 1-5,9,12")
    render.add_argument("--dpi", type=int, default=140)
    for name in ("record-source-review", "approve-extraction"):
        sub.add_parser(name).add_argument("decisions", type=Path)
    args = parser.parse_args(argv)
    root = args.storage_root.resolve()
    with connect_catalog(root / "state/scraper.sqlite3") as connection:
        initialize_contract_schema(connection)
        if args.command == "render-source":
            result = render_source_views(
                connection, root, args.document_id, parse_pages(args.pages), args.dpi
            )
        else:
            decisions = json.loads(args.decisions.read_text())
            if args.command == "record-source-review":
                record_source_reviews(connection, root, decisions)
                result = {"recorded_pages": len(decisions["pages"])}
            else:
                result = {"contract_ids": approve_extraction(connection, root, decisions)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
