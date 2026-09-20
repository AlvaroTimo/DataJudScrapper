from __future__ import annotations

from pathlib import Path

import pytest
from test_contract_inventory import catalog as catalog
from test_contract_inventory import source_pdf as source_pdf

from datajud_scraper.contract_catalog import require_release_reviews
from datajud_scraper.contract_inventory import (
    DEFAULT_TESSDATA,
    inventory_document,
    save_inventory,
)
from datajud_scraper.contract_review import (
    approve_extraction,
    record_source_reviews,
    render_source_views,
)
from datajud_scraper.pdf_validation import hash_file


@pytest.fixture
def reviewed_source(catalog, source_pdf, tmp_path):
    if not (DEFAULT_TESSDATA / "por.traineddata").exists():
        pytest.skip("requiere modelos OCR locales")
    result = inventory_document(
        {
            "source_path": str(source_pdf),
            "folder": str(tmp_path / "inventory"),
            "document_id": "doc",
            "source_sha256": hash_file(source_pdf),
            "page_count": 4,
            "tessdata": str(DEFAULT_TESSDATA),
        }
    )
    save_inventory(catalog, result)
    views = render_source_views(catalog, tmp_path, "doc", [1, 2, 3, 4])
    review = {
        "method": "manual_visual",
        "reviewer": "synthetic_test_fixture",
        "document_id": "doc",
        "pages": [
            {
                "page": view["page_number"],
                "view_id": view["view_id"],
                "decision": "contract" if view["page_number"] in (2, 3) else "noncontract",
                "rationale": "Known synthetic test content",
            }
            for view in views
        ],
    }
    return catalog, tmp_path, review, views


def test_multiple_contracts_require_every_source_page_reviewed(reviewed_source):
    connection, root, review, _ = reviewed_source
    record_source_reviews(connection, root, {**review, "pages": review["pages"][:3]})
    plan = {
        "method": "manual_visual",
        "reviewer": "synthetic_test_fixture",
        "document_id": "doc",
        "contracts": [
            {"kind": "credit_card", "regions": [{"page": 2}]},
            {"kind": "loan", "regions": [{"page": 3}]},
        ],
    }
    with pytest.raises(ValueError, match="revisar manualmente"):
        approve_extraction(connection, root, plan)
    record_source_reviews(connection, root, review)
    identifiers = approve_extraction(connection, root, plan)
    assert len(set(identifiers)) == 2
    row = connection.execute("SELECT has_contract,contract_count FROM documents").fetchone()
    assert tuple(row) == (1, 2)
    assert connection.execute("SELECT contract_count FROM cases").fetchone()[0] == 2
    with pytest.raises(ValueError, match="limpieza aprobadas"):
        require_release_reviews(connection, "doc")


def test_extraction_cannot_omit_positive_pages_or_add_noncontract_pages(reviewed_source):
    connection, root, review, _ = reviewed_source
    record_source_reviews(connection, root, review)
    base = {"method": "manual_visual", "reviewer": "synthetic_test_fixture", "document_id": "doc"}
    with pytest.raises(ValueError, match="fuera de la extraccion"):
        approve_extraction(
            connection,
            root,
            {**base, "contracts": [{"kind": "credit_card", "regions": [{"page": 2}]}]},
        )
    with pytest.raises(ValueError, match="no aprobada"):
        approve_extraction(
            connection,
            root,
            {
                **base,
                "contracts": [{"kind": "loan", "regions": [{"page": 1}, {"page": 2}, {"page": 3}]}],
            },
        )


def test_changed_review_image_and_automatic_approval_rejected(reviewed_source):
    connection, root, review, views = reviewed_source
    with pytest.raises(ValueError, match="manual identificada"):
        record_source_reviews(connection, root, {**review, "method": "automatic"})
    Path(views[0]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="imagen revisada falta o cambio"):
        record_source_reviews(connection, root, review)
    assert connection.execute("SELECT COUNT(*) FROM contract_page_reviews").fetchone()[0] == 0


def test_invalid_rotation_cannot_be_approved_or_count_same_region_twice(reviewed_source):
    connection, root, review, _ = reviewed_source
    record_source_reviews(connection, root, review)
    plan = {
        "method": "manual_visual",
        "reviewer": "synthetic_test_fixture",
        "document_id": "doc",
        "contracts": [{"kind": "loan", "regions": [{"page": 2, "rotation": 45}, {"page": 3}]}],
    }
    with pytest.raises(ValueError, match="rotacion"):
        approve_extraction(connection, root, plan)
    plan["contracts"][0]["regions"][0]["rotation"] = 180
    plan["contracts"].append({"kind": "loan", "regions": [{"page": 2, "rotation": 0}]})
    with pytest.raises(ValueError, match="misma region"):
        approve_extraction(connection, root, plan)
    assert connection.execute("SELECT COUNT(*) FROM contracts").fetchone()[0] == 0


def test_review_of_older_version_does_not_approve_newer_unreviewed_source(reviewed_source):
    connection, root, review, _ = reviewed_source
    with connection:
        connection.execute("UPDATE documents SET retrieved_at='2025-01-01T00:00:00Z'")
        connection.execute(
            "INSERT INTO documents(document_id,case_id,retrieved_at,relative_path,sha256,"
            "size_bytes,mime_type,page_count) VALUES "
            "('newdoc','case','2026-01-01T00:00:00Z','new.pdf','different',100,'application/pdf',1)"
        )
    record_source_reviews(connection, root, review)
    approve_extraction(
        connection,
        root,
        {
            "method": "manual_visual",
            "reviewer": "synthetic_test_fixture",
            "document_id": "doc",
            "contracts": [{"kind": "loan", "regions": [{"page": 2}, {"page": 3}]}],
        },
    )
    case = connection.execute("SELECT has_contract,contract_count FROM cases").fetchone()
    assert tuple(case) == (None, None)
    old = connection.execute(
        "SELECT contract_count FROM documents WHERE document_id='doc'"
    ).fetchone()
    assert old[0] == 1
