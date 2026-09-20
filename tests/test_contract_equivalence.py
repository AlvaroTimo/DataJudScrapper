from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from datajud_scraper.contract_catalog import (
    initialize_contract_schema,
    require_release_reviews,
)
from datajud_scraper.contract_equivalence import index_references, propose_matches
from datajud_scraper.contract_review import (
    approve_extraction,
    record_source_reviews,
    render_source_views,
    save_view,
)
from datajud_scraper.pdf_validation import hash_file
from datajud_scraper.runtime import Database


def manual_review(document_id, entry):
    return {
        "method": "manual_visual", "reviewer": "synthetic_fixture",
        "document_id": document_id, "pages": [entry],
    }


def negative_plan(document_id):
    return {
        "method": "manual_visual", "reviewer": "synthetic_fixture",
        "document_id": document_id, "contracts": [],
    }


@pytest.fixture
def equivalent_sources(tmp_path):
    database = Database(tmp_path / "catalog.sqlite3")
    connection = database.connection
    initialize_contract_schema(connection)
    for document_id in ("reference", "target"):
        path = tmp_path / f"{document_id}.pdf"
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((40, 50), "CERTIDAO\nIntimacao das partes.")
            page.insert_text((40, 810), f"Identificador de expediente: {document_id}")
            pdf.save(path)
        with connection:
            connection.execute(
                "INSERT INTO cases(case_id,process_number,process_number_digits,first_seen_at) "
                "VALUES (?,?,?,'now')", (document_id, document_id, document_id),
            )
            connection.execute(
                "INSERT INTO documents(document_id,case_id,retrieved_at,relative_path,sha256,"
                "size_bytes,mime_type,page_count) VALUES (?,?,'now',?,?,?,'application/pdf',1)",
                (document_id, document_id, path.name, hash_file(path), path.stat().st_size),
            )
            connection.execute(
                "INSERT INTO contract_sources(document_id,inventory_version,source_sha256,"
                "status,page_count,updated_at) VALUES (?,'synthetic',?,'inventoried',1,'now')",
                (document_id, hash_file(path)),
            )
            connection.execute(
                "INSERT INTO contract_pages VALUES (?,1,50,50,0,'native',NULL,'[]')",
                (document_id,),
            )
    reference_views = render_source_views(connection, tmp_path, "reference", [1])
    target_views = render_source_views(connection, tmp_path, "target", [1])
    record_source_reviews(connection, tmp_path, manual_review("reference", {
        "page": 1, "view_id": reference_views[0]["view_id"], "decision": "noncontract",
        "rationale": "Known synthetic certificate with no credit instrument.",
    }))
    approve_extraction(connection, tmp_path, negative_plan("reference"))
    assert index_references(connection)["indexed_pages"] == 1
    matches = propose_matches(connection, tmp_path, "target", target_views)["matches"]
    assert len(matches) == 1
    entry = {
        "page": 1, "view_id": target_views[0]["view_id"], "decision": "noncontract",
        "review_basis": "exact_body_match", "reference_id": matches[0]["reference_id"],
        "remainder_view_id": matches[0]["remainder_view"]["view_id"],
        "rationale": "Synthetic exact body and separately reviewed different footer.",
    }
    yield connection, tmp_path, entry, reference_views[0], target_views[0]
    database.close()


def test_exact_match_requires_manual_remainder_and_keeps_hash_provenance(equivalent_sources):
    connection, root, entry, reference_view, _ = equivalent_sources
    target = connection.execute("SELECT * FROM documents WHERE document_id='target'").fetchone()
    assert target["has_contract"] is None
    assert connection.execute(
        "SELECT COUNT(*) FROM contract_page_reviews WHERE document_id='target'"
    ).fetchone()[0] == 0
    record_source_reviews(connection, root, manual_review("target", entry))
    approve_extraction(connection, root, negative_plan("target"))
    evidence = connection.execute("SELECT * FROM contract_page_review_evidence").fetchone()
    assert evidence["basis"] == "exact_body_match"
    assert evidence["reference_id"] == entry["reference_id"]
    assert len(evidence["body_sha256"]) == 64
    # A reused review cannot become a new independent anchor.
    assert index_references(connection)["indexed_pages"] == 0
    # Approved disposal removes pixels, not the original recorded manual judgment.
    Path(reference_view["path"]).unlink()
    with connection:
        connection.execute(
            "UPDATE documents SET source_disposition='purged' WHERE document_id='reference'"
        )
    require_release_reviews(connection, "target")


@pytest.mark.parametrize("change", ["pixel", "dimensions"])
def test_same_words_or_near_identical_pixels_do_not_qualify(equivalent_sources, change):
    connection, root, entry, _, target_view = equivalent_sources
    with Image.open(target_view["path"]) as opened:
        modified = opened.convert("RGB")
    if change == "pixel":
        modified.putpixel((0, 0), (254, 255, 255))
    else:
        modified = modified.crop((0, 0, modified.width - 1, modified.height))
    source_sha = hash_file(root / "target.pdf")
    new_view = save_view(connection, root, "target", "source", 1, modified, source_sha)
    changed = {**entry, "view_id": new_view["view_id"]}
    assert propose_matches(connection, root, "target", [new_view])["matches"] == []
    with pytest.raises(ValueError, match="identico pixel"):
        record_source_reviews(connection, root, manual_review("target", changed))


def test_wrong_or_missing_remainder_cannot_approve(equivalent_sources):
    connection, root, entry, _, target_view = equivalent_sources
    no_remainder = {key: value for key, value in entry.items() if key != "remainder_view_id"}
    with pytest.raises(ValueError, match="falta la referencia"):
        record_source_reviews(connection, root, manual_review("target", no_remainder))
    with pytest.raises(ValueError, match="no corresponde"):
        record_source_reviews(connection, root, manual_review("target", {
            **entry, "remainder_view_id": target_view["view_id"],
        }))
    bogus = save_view(
        connection, root, "target", "source_remainder", 1,
        Image.new("RGB", (20, 20), "white"), hash_file(root / "target.pdf"),
    )
    with pytest.raises(ValueError, match="no cubre exactamente"):
        record_source_reviews(connection, root, manual_review("target", {
            **entry, "remainder_view_id": bogus["view_id"],
        }))


@pytest.mark.parametrize("field,value", [
    ("decision", "uncertain"), ("rendered_sha256", "changed"), ("reviewed_at", "later"),
])
def test_changed_reference_blocks_approval_and_release(equivalent_sources, field, value):
    connection, root, entry, _, _ = equivalent_sources
    record_source_reviews(connection, root, manual_review("target", entry))
    approve_extraction(connection, root, negative_plan("target"))
    with connection:
        connection.execute(
            f"UPDATE contract_page_reviews SET {field}=? WHERE document_id='reference'", (value,)
        )
    with pytest.raises(ValueError, match="referencia cambio"):
        require_release_reviews(connection, "target")
    with pytest.raises(ValueError, match="referencia cambio"):
        record_source_reviews(connection, root, manual_review("target", entry))


def test_positive_decisions_cannot_reuse_negative_reference(equivalent_sources):
    connection, root, entry, _, _ = equivalent_sources
    with pytest.raises(ValueError, match="solo paginas no contractuales"):
        record_source_reviews(connection, root, manual_review("target", {
            **entry, "decision": "contract",
        }))


def test_full_page_reinspection_removes_dependency(equivalent_sources):
    connection, root, entry, _, _ = equivalent_sources
    record_source_reviews(connection, root, manual_review("target", entry))
    full_page = {key: value for key, value in entry.items() if key not in (
        "review_basis", "reference_id", "remainder_view_id"
    )}
    record_source_reviews(connection, root, manual_review("target", full_page))
    count = connection.execute("SELECT COUNT(*) FROM contract_page_review_evidence").fetchone()[0]
    assert count == 0
    approve_extraction(connection, root, negative_plan("target"))
    assert index_references(connection)["indexed_pages"] == 1
