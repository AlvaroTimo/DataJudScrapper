"""Drafts and manual before/after review, bound to the exact reviewed pixels.

No renderer or detector can approve a contract. Every output page needs an explicit
visual decision before the document becomes eligible for release and source removal.
"""

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
)
from .contract_redaction import pixel_box, render_contract_page, write_cleaned_contract
from .contract_review import parse_pages, save_view, source_record, verify_view
from .pdf_validation import hash_file
from .runtime import utc_now


def contract_record(connection, root: Path, contract_id: str):
    record = connection.execute(
        "SELECT * FROM contracts WHERE contract_id=?", (contract_id,)
    ).fetchone()
    if record is None or record["extraction_status"] != "approved":
        raise ValueError("contrato desconocido o sin extraccion aprobada")
    source, path = source_record(connection, root, record["document_id"])
    if source["contract_review_status"] != "approved":
        raise ValueError("la revision de la fuente ya no esta aprobada")
    return record, source, path


def manual_decision(decision: dict) -> None:
    if decision.get("method") != "manual_visual" or not decision.get("reviewer", "").strip():
        raise ValueError("se requiere una decision visual manual identificada")


def checked_view(connection, view_id, record, source, scope, number, pixels_sha=None):
    view = verify_view(
        connection,
        view_id,
        document_id=record["document_id"],
        scope=scope,
        page_number=number,
        source_sha=source["sha256"],
    )
    if view["contract_id"] != record["contract_id"]:
        raise ValueError("la imagen pertenece a otro contrato")
    if pixels_sha is not None and view["pixels_sha256"] != pixels_sha:
        raise ValueError("la imagen corresponde a otra revision o resolucion")
    return view


def render_original_views(connection, root, contract_id, pages, *, dpi=240):
    import pymupdf

    if not 150 <= dpi <= 600:
        raise ValueError("resolucion fuera de rango")
    record, source, path = contract_record(connection, root, contract_id)
    regions = json.loads(record["source_regions_json"])
    views = []
    with pymupdf.open(path) as pdf:
        for number in pages:
            if type(number) is not int or not 1 <= number <= len(regions):
                raise ValueError("pagina de contrato fuera de rango")
            image = render_contract_page(pdf, regions[number - 1], dpi=dpi)
            views.append(
                save_view(
                    connection,
                    root,
                    record["document_id"],
                    "contract_original",
                    number,
                    image,
                    source["sha256"],
                    contract_id,
                )
            )
    return views


def create_draft(connection, root: Path, plan: dict) -> dict:
    """Use masks from viewed original regions, leaving the result pending review."""
    import pymupdf

    manual_decision(plan)
    if type(plan.get("had_sensitive_data")) is not bool:
        raise ValueError("debe indicarse si el contrato contenia datos sensibles")
    record, source, path = contract_record(connection, root, plan["contract_id"])
    regions = json.loads(record["source_regions_json"])
    pages = plan["pages"]
    expected = set(range(1, len(regions) + 1))
    if len(pages) != len(expected) or {p["page"] for p in pages} != expected:
        raise ValueError("el plan debe revisar cada pagina del contrato una sola vez")
    dpi = plan.get("dpi", 240)
    if type(dpi) is not int or not 150 <= dpi <= 600:
        raise ValueError("resolucion fuera de rango")
    masks, checked = {}, []
    with pymupdf.open(path) as pdf:
        for entry in pages:
            number = entry["page"]
            if type(number) is not int or not entry.get("rationale", "").strip():
                raise ValueError("cada pagina necesita numero y justificacion manual")
            original = render_contract_page(pdf, regions[number - 1], dpi=dpi)
            view = checked_view(
                connection,
                entry["original_view_id"],
                record,
                source,
                "contract_original",
                number,
                hashlib.sha256(original.tobytes()).hexdigest(),
            )
            masks[number] = []
            for mask in entry.get("masks", []):
                if mask.get("confirmed") is not True or not re.fullmatch(
                    r"[a-z_]{3,50}", mask.get("category", "")
                ):
                    raise ValueError("mascara sin confirmacion o categoria valida")
                pixel_box(mask["rect"], original.width, original.height)
                masks[number].append(
                    {"rect": mask["rect"], "category": mask["category"], "confirmed": True}
                )
            checked.append((entry, view))
    if not plan["had_sensitive_data"] and any(masks.values()):
        raise ValueError("un contrato con mascaras no puede declararse sin datos sensibles")
    output = (
        root
        / "contract-work"
        / record["document_id"]
        / "drafts"
        / (f"{record['contract_id']}--{uuid.uuid4()}.pdf")
    )
    evidence = write_cleaned_contract(path, output, regions, masks, dpi=dpi)
    evidence["declared_had_sensitive_data"] = plan["had_sensitive_data"]
    evidence["mask_reviewer"] = plan["reviewer"]
    evidence["source_sha256"] = source["sha256"]
    try:
        save_draft(connection, root, plan, record, source, checked, masks, output, evidence)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return evidence


@catalog_write
def save_draft(connection, root, plan, record, source, checked, masks, output, evidence):
    """Commit the rendered draft only if its inputs and approvals remain current.

    Rendering must not hold SQLite's single writer lock for minutes while the
    downloader and OCR workers need to persist their progress.
    """
    current, current_source, _ = contract_record(connection, root, plan["contract_id"])
    if dict(current) != dict(record) or current_source["sha256"] != source["sha256"]:
        raise ValueError("el contrato o sus revisiones cambiaron durante la limpieza")
    for entry, view in checked:
        checked_view(
            connection,
            entry["original_view_id"],
            current,
            current_source,
            "contract_original",
            entry["page"],
            view["pixels_sha256"],
        )
    now = utc_now().isoformat()
    with connection:
        connection.execute(
            "DELETE FROM contract_output_reviews WHERE contract_id=?", (record["contract_id"],)
        )
        connection.execute(
            "DELETE FROM contract_redactions WHERE contract_id=?", (record["contract_id"],)
        )
        for number, page_masks in masks.items():
            for mask in page_masks:
                connection.execute(
                    "INSERT INTO contract_redactions VALUES (?,?,?,?,?,1,?,?)",
                    (
                        str(uuid.uuid4()),
                        record["contract_id"],
                        number,
                        json.dumps(mask["rect"]),
                        mask["category"],
                        plan["reviewer"],
                        now,
                    ),
                )
        connection.execute(
            "UPDATE contracts SET cleaned_path=?,cleaned_sha256=?,render_evidence_json=?,"
            "had_sensitive_data=NULL,redaction_status='pending_review',updated_at=? "
            "WHERE contract_id=?",
            (
                str(output.resolve()),
                evidence["sha256"],
                json.dumps(evidence),
                now,
                record["contract_id"],
            ),
        )
        refresh_sensitive_state(connection, record["document_id"])
        audit(
            connection,
            "cleaned_draft_created",
            document_id=record["document_id"],
            contract_id=record["contract_id"],
            sha256=evidence["sha256"],
            reviewer=plan["reviewer"],
            original_views=[view["view_id"] for _, view in checked],
        )


def verified_cleaned_path(root: Path, record) -> tuple[Path, dict]:
    if not record["cleaned_path"] or not record["cleaned_sha256"]:
        raise ValueError("no existe borrador limpio")
    path = Path(record["cleaned_path"]).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("salida ausente o fuera del almacenamiento")
    if hash_file(path) != record["cleaned_sha256"]:
        raise ValueError("el PDF limpio cambio desde su generacion")
    evidence = json.loads(record["render_evidence_json"] or "{}")
    if evidence.get("sha256") != record["cleaned_sha256"]:
        raise ValueError("evidencia de limpieza incoherente")
    return path, evidence


def render_output_views(connection, root: Path, contract_id: str, pages: list[int]):
    """Read the saved PDF's lossless page image and the corresponding source pixels."""
    import pymupdf
    from PIL import Image

    record, source, source_path = contract_record(connection, root, contract_id)
    cleaned_path, evidence = verified_cleaned_path(root, record)
    regions = json.loads(record["source_regions_json"])
    views = []
    with pymupdf.open(source_path) as original, pymupdf.open(cleaned_path) as cleaned:
        for number in pages:
            if type(number) is not int or not 1 <= number <= len(regions):
                raise ValueError("pagina de contrato fuera de rango")
            page_evidence = evidence["pages"][number - 1]
            before = render_contract_page(original, regions[number - 1], dpi=evidence["dpi"])
            images = cleaned[number - 1].get_images()
            if len(images) != 1:
                raise ValueError("pagina limpia con imagenes inesperadas")
            pixels = pymupdf.Pixmap(cleaned, images[0][0])
            after = Image.frombytes("RGB", (pixels.width, pixels.height), pixels.samples)
            pair = {"page_number": number}
            for scope, image, key in (
                ("contract_original", before, "original_pixels_sha256"),
                ("contract_cleaned", after, "cleaned_pixels_sha256"),
            ):
                if hashlib.sha256(image.tobytes()).hexdigest() != page_evidence[key]:
                    raise ValueError("los pixeles no corresponden a la evidencia guardada")
                pair[scope] = save_view(
                    connection,
                    root,
                    record["document_id"],
                    scope,
                    number,
                    image,
                    source["sha256"],
                    contract_id,
                )
            views.append(pair)
    return views


@catalog_write
def record_output_reviews(connection, root: Path, review: dict) -> None:
    manual_decision(review)
    record, source, _ = contract_record(connection, root, review["contract_id"])
    _, evidence = verified_cleaned_path(root, record)
    current = {p["page_number"]: p for p in evidence["pages"]}
    checked = []
    for entry in review["pages"]:
        number = entry["page"]
        if type(number) is not int or number not in current:
            raise ValueError("pagina de contrato fuera de rango")
        if (
            any(
                type(entry.get(k)) is not bool
                for k in ("personal_data_removed", "contract_content_preserved")
            )
            or not entry.get("rationale", "").strip()
        ):
            raise ValueError("se requieren ambas decisiones visuales y su justificacion")
        page = current[number]
        for scope, key, expected in (
            ("contract_original", "original_view_id", "original_pixels_sha256"),
            ("contract_cleaned", "cleaned_view_id", "cleaned_pixels_sha256"),
        ):
            checked_view(connection, entry[key], record, source, scope, number, page[expected])
        checked.append((entry, page))
    with connection:
        for entry, page in checked:
            connection.execute(
                """INSERT INTO contract_output_reviews VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(contract_id,page_number) DO UPDATE SET
                original_render_sha256=excluded.original_render_sha256,
                cleaned_render_sha256=excluded.cleaned_render_sha256,
                masks_sha256=excluded.masks_sha256,
                personal_data_removed=excluded.personal_data_removed,
                contract_content_preserved=excluded.contract_content_preserved,
                reviewer=excluded.reviewer,reviewed_at=excluded.reviewed_at,
                rationale=excluded.rationale""",
                (
                    record["contract_id"],
                    entry["page"],
                    page["original_pixels_sha256"],
                    page["cleaned_pixels_sha256"],
                    page["masks_sha256"],
                    int(entry["personal_data_removed"]),
                    int(entry["contract_content_preserved"]),
                    review["reviewer"],
                    utc_now().isoformat(),
                    entry["rationale"],
                ),
            )
        connection.execute(
            "UPDATE contracts SET redaction_status='pending_review',had_sensitive_data=NULL "
            "WHERE contract_id=?",
            (record["contract_id"],),
        )
        refresh_sensitive_state(connection, record["document_id"])
        audit(
            connection,
            "output_pages_manually_reviewed",
            document_id=record["document_id"],
            contract_id=record["contract_id"],
            pages=[p["page"] for p, _ in checked],
            reviewer=review["reviewer"],
        )


def refresh_sensitive_state(connection, document_id):
    source = connection.execute(
        "SELECT * FROM documents WHERE document_id=?", (document_id,)
    ).fetchone()
    contracts = connection.execute(
        "SELECT had_sensitive_data,redaction_status FROM contracts WHERE document_id=?",
        (document_id,),
    ).fetchall()
    known = source["contract_review_status"] == "approved" and all(
        c["redaction_status"] == "approved" and c["had_sensitive_data"] is not None
        for c in contracts
    )
    value = int(any(c["had_sensitive_data"] for c in contracts)) if known else None
    connection.execute(
        "UPDATE documents SET contracts_had_sensitive_data=? WHERE document_id=?",
        (value, document_id),
    )
    refresh_case_contract_state(connection, source["case_id"])


@catalog_write
def approve_redaction(connection, root: Path, decision: dict) -> None:
    manual_decision(decision)
    record, _, _ = contract_record(connection, root, decision["contract_id"])
    _, evidence = verified_cleaned_path(root, record)
    reviews = connection.execute(
        "SELECT * FROM contract_output_reviews WHERE contract_id=?", (record["contract_id"],)
    ).fetchall()
    expected = {p["page_number"]: p for p in evidence["pages"]}
    if {r["page_number"] for r in reviews} != set(expected):
        raise ValueError("faltan paginas limpias por revisar manualmente")
    for review in reviews:
        page = expected[review["page_number"]]
        if not review["personal_data_removed"] or not review["contract_content_preserved"]:
            raise ValueError("hay una revision visual rechazada")
        if any(
            review[left] != page[right]
            for left, right in (
                ("original_render_sha256", "original_pixels_sha256"),
                ("cleaned_render_sha256", "cleaned_pixels_sha256"),
                ("masks_sha256", "masks_sha256"),
            )
        ):
            raise ValueError("las revisiones corresponden a otra version")
    if type(evidence.get("declared_had_sensitive_data")) is not bool:
        raise ValueError("falta la decision sobre datos sensibles")
    with connection:
        connection.execute(
            "UPDATE contracts SET had_sensitive_data=?,redaction_status='approved',updated_at=? "
            "WHERE contract_id=?",
            (
                int(evidence["declared_had_sensitive_data"]),
                utc_now().isoformat(),
                record["contract_id"],
            ),
        )
        refresh_sensitive_state(connection, record["document_id"])
        audit(
            connection,
            "redaction_manually_approved",
            document_id=record["document_id"],
            contract_id=record["contract_id"],
            reviewer=decision["reviewer"],
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Borradores y revision visual de contratos.")
    parser.add_argument("--storage-root", type=Path, default=Path("data"))
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("render-original", "render-output"):
        command = sub.add_parser(name)
        command.add_argument("contract_id")
        command.add_argument("pages", help="paginas del contrato: 1-5,9")
        if name == "render-original":
            command.add_argument("--dpi", type=int, default=240)
    for name in ("create-draft", "record-output-review", "approve-redaction"):
        sub.add_parser(name).add_argument("decisions", type=Path)
    args = parser.parse_args(argv)
    root = args.storage_root.resolve()
    with connect_catalog(root / "state/scraper.sqlite3") as connection:
        initialize_contract_schema(connection)
        if args.command == "render-original":
            result = render_original_views(
                connection, root, args.contract_id, parse_pages(args.pages), dpi=args.dpi
            )
        elif args.command == "render-output":
            result = render_output_views(
                connection, root, args.contract_id, parse_pages(args.pages)
            )
        else:
            decision = json.loads(args.decisions.read_text())
            action = {
                "create-draft": create_draft,
                "record-output-review": record_output_reviews,
                "approve-redaction": approve_redaction,
            }[args.command]
            result = action(connection, root, decision) or {"status": "recorded"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
