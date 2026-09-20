"""Exact pixel reuse for previously reviewed noncontract pages.

Only the top 94% can reuse a full-page manual decision. The remaining rows need
a separate visual review. Similar text, perceptual hashes and OCR never qualify.
Fingerprints contain no image/text copies and survive approved source disposal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path

from PIL import Image

from .contract_catalog import (
    audit,
    catalog_write,
    connect_catalog,
    initialize_contract_schema,
)
from .pdf_validation import hash_file
from .runtime import utc_now


def image_parts(path):
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    # Integer arithmetic gives an unambiguous partition with no missing row.
    split = image.height * 94 // 100
    body = image.crop((0, 0, image.width, split))
    remainder = image.crop((0, split, image.width, image.height))
    return {
        "width": image.width,
        "height": image.height,
        "split_row": split,
        "body_sha256": hashlib.sha256(body.tobytes()).hexdigest(),
    }, remainder


def require_reference(connection, reference_id):
    reference = connection.execute(
        "SELECT * FROM contract_reference_bodies WHERE reference_id=?", (reference_id,)
    ).fetchone()
    if reference is None:
        raise ValueError("referencia visual desconocida")
    current = connection.execute(
        "SELECT r.*,d.sha256,d.contract_review_status FROM contract_page_reviews r "
        "JOIN documents d USING(document_id) WHERE r.document_id=? AND r.page_number=?",
        (reference["document_id"], reference["page_number"]),
    ).fetchone()
    reused = connection.execute(
        "SELECT 1 FROM contract_page_review_evidence WHERE document_id=? AND page_number=?",
        (reference["document_id"], reference["page_number"]),
    ).fetchone()
    if (
        current is None
        or current["decision"] != "noncontract"
        or current["contract_review_status"] != "approved"
        or current["sha256"] != reference["source_sha256"]
        or any(
            current[key] != reference[key]
            for key in ("source_sha256", "rendered_sha256", "reviewed_at")
        )
        or reused
    ):
        raise ValueError("la revision manual de referencia cambio o no es independiente")
    return reference


@catalog_write
def save_references(connection, references):
    now = utc_now().isoformat()
    for reference in references:
        connection.execute(
            "INSERT OR IGNORE INTO contract_reference_bodies VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                reference["reference_id"],
                reference["document_id"],
                reference["page_number"],
                reference["source_sha256"],
                reference["rendered_sha256"],
                reference["reviewed_at"],
                reference["width"],
                reference["height"],
                reference["split_row"],
                reference["body_sha256"],
                now,
            ),
        )
        require_reference(connection, reference["reference_id"])
    if references:
        audit(connection, "independent_noncontract_bodies_indexed", count=len(references))


def index_references(connection):
    """Hash verified renders outside the write transaction; recheck decisions at save."""
    rows = connection.execute(
        "SELECT r.*,v.image_path FROM contract_page_reviews r "
        "JOIN documents d USING(document_id) JOIN contract_view_artifacts v ON "
        "v.document_id=r.document_id AND v.page_number=r.page_number "
        "AND v.source_sha256=r.source_sha256 AND v.image_sha256=r.rendered_sha256 "
        "AND v.scope='source' WHERE r.decision='noncontract' "
        "AND d.contract_review_status='approved' AND d.sha256=r.source_sha256 "
        "AND NOT EXISTS (SELECT 1 FROM contract_page_review_evidence e "
        "WHERE e.document_id=r.document_id AND e.page_number=r.page_number) "
        "AND NOT EXISTS (SELECT 1 FROM contract_reference_bodies b "
        "WHERE b.document_id=r.document_id AND b.page_number=r.page_number "
        "AND b.rendered_sha256=r.rendered_sha256 AND b.reviewed_at=r.reviewed_at)"
    ).fetchall()
    pending, seen, indexed, missing = [], set(), 0, 0
    for row in rows:
        identity = (row["document_id"], row["page_number"], row["reviewed_at"])
        if identity in seen:
            continue
        seen.add(identity)
        path = Path(row["image_path"])
        if not path.is_file():
            missing += 1
            continue
        if hash_file(path) != row["rendered_sha256"]:
            raise ValueError("la imagen de referencia cambio")
        parts, _ = image_parts(path)
        reference = {
            key: row[key]
            for key in (
                "document_id", "page_number", "source_sha256", "rendered_sha256", "reviewed_at"
            )
        }
        reference.update(parts)
        reference["reference_id"] = str(
            uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(reference, sort_keys=True))
        )
        pending.append(reference)
        if len(pending) == 50:
            save_references(connection, pending)
            indexed += len(pending)
            pending = []
    save_references(connection, pending)
    return {"indexed_pages": indexed + len(pending), "unavailable_renders": missing}


def propose_matches(connection, root, document_id, views):
    """Return suggestions and unreviewed remainder images; never record decisions."""
    from .contract_review import save_view, source_record, verify_view

    source, _ = source_record(connection, root, document_id)
    matches = []
    for supplied in views:
        view = verify_view(
            connection, supplied["view_id"], document_id=document_id,
            scope="source", page_number=supplied["page_number"], source_sha=source["sha256"],
        )
        parts, remainder = image_parts(view["image_path"])
        candidates = connection.execute(
            "SELECT reference_id FROM contract_reference_bodies WHERE body_sha256=? "
            "AND width=? AND height=? AND split_row=? AND document_id!=?",
            (
                parts["body_sha256"], parts["width"], parts["height"],
                parts["split_row"], document_id,
            ),
        ).fetchall()
        for candidate in candidates:
            try:
                reference = require_reference(connection, candidate[0])
            except ValueError:
                continue
            remainder_view = save_view(
                connection, root, document_id, "source_remainder", view["page_number"],
                remainder, source["sha256"],
            )
            matches.append({
                "page": view["page_number"], "view_id": view["view_id"],
                "review_basis": "exact_body_match",
                "reference_id": reference["reference_id"],
                "reference_document_id": reference["document_id"],
                "reference_page": reference["page_number"],
                "body_sha256": parts["body_sha256"],
                "remainder_view": remainder_view,
            })
            break
    return {"document_id": document_id, "matches": matches, "requires_manual_review": True}


def verify_review_basis(connection, entry, view):
    from .contract_review import verify_view

    basis = entry.get("review_basis", "full_page")
    if basis == "full_page":
        if entry.get("reference_id") or entry.get("remainder_view_id"):
            raise ValueError("especifique la base de revision con referencia")
        return None
    if basis != "exact_body_match" or entry["decision"] != "noncontract":
        raise ValueError("solo paginas no contractuales permiten reutilizar una revision")
    if not entry.get("reference_id") or not entry.get("remainder_view_id"):
        raise ValueError("falta la referencia o la franja revisada")
    reference = require_reference(connection, entry["reference_id"])
    if reference["document_id"] == view["document_id"]:
        raise ValueError("la referencia debe pertenecer a otro expediente aprobado")
    parts, expected_remainder = image_parts(view["image_path"])
    if any(reference[key] != value for key, value in parts.items()):
        raise ValueError("el cuerpo de pagina no es identico pixel por pixel")
    remainder = verify_view(
        connection, entry["remainder_view_id"], document_id=view["document_id"],
        scope="source_remainder", page_number=view["page_number"],
        source_sha=view["source_sha256"],
    )
    with Image.open(remainder["image_path"]) as opened:
        actual_remainder = opened.convert("RGB")
    expected_sha = hashlib.sha256(expected_remainder.tobytes()).hexdigest()
    if (
        actual_remainder.size != expected_remainder.size
        or hashlib.sha256(actual_remainder.tobytes()).hexdigest() != expected_sha
        or remainder["pixels_sha256"] != expected_sha
    ):
        raise ValueError("la franja revisada no cubre exactamente el resto de la pagina")
    return {
        "reference_id": reference["reference_id"],
        "source_view_id": view["view_id"],
        "remainder_view_id": remainder["view_id"],
        "body_sha256": parts["body_sha256"],
        "remainder_pixels_sha256": expected_sha,
    }


def require_current_references(connection, document_id):
    evidence = connection.execute(
        "SELECT e.*,r.decision,r.source_sha256,r.rendered_sha256,v.image_sha256, "
        "v.source_sha256 AS view_source_sha256 FROM contract_page_review_evidence e "
        "JOIN contract_page_reviews r USING(document_id,page_number) "
        "JOIN contract_view_artifacts v ON v.view_id=e.source_view_id WHERE e.document_id=?",
        (document_id,),
    ).fetchall()
    for row in evidence:
        reference = require_reference(connection, row["reference_id"])
        if (
            row["decision"] != "noncontract"
            or row["source_sha256"] != row["view_source_sha256"]
            or row["rendered_sha256"] != row["image_sha256"]
            or row["body_sha256"] != reference["body_sha256"]
        ):
            raise ValueError("la evidencia de equivalencia ya no corresponde a la revision")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Equivalencia exacta de paginas ya revisadas.")
    parser.add_argument("--storage-root", type=Path, default=Path("data"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("index")
    match = sub.add_parser("match")
    match.add_argument("document_id")
    match.add_argument("views_json", type=Path)
    args = parser.parse_args(argv)
    root = args.storage_root.resolve()
    with connect_catalog(root / "state/scraper.sqlite3") as connection:
        initialize_contract_schema(connection)
        if args.command == "index":
            result = index_references(connection)
        else:
            result = propose_matches(
                connection, root, args.document_id, json.loads(args.views_json.read_text())
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
