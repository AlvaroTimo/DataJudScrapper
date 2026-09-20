from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from test_contract_inventory import catalog as catalog
from test_contract_inventory import source_pdf as source_pdf
from test_contract_review import reviewed_source as reviewed_source

from datajud_scraper.contract_catalog import require_release_reviews
from datajud_scraper.contract_outputs import (
    approve_redaction,
    create_draft,
    record_output_reviews,
    render_original_views,
    render_output_views,
)
from datajud_scraper.contract_review import approve_extraction, record_source_reviews

MANUAL = {"method": "manual_visual", "reviewer": "synthetic_test_fixture"}


@pytest.fixture
def extracted(reviewed_source):
    connection, root, review, _ = reviewed_source
    record_source_reviews(connection, root, review)
    ids = approve_extraction(
        connection,
        root,
        {
            **MANUAL,
            "document_id": "doc",
            "contracts": [
                {"kind": "credit_card", "regions": [{"page": 2}]},
                {"kind": "loan", "regions": [{"page": 3}]},
            ],
        },
    )
    return connection, root, ids, review


def draft_plan(connection, root, identifier, sensitive=False):
    view = render_original_views(connection, root, identifier, [1], dpi=150)[0]
    return {
        **MANUAL,
        "contract_id": identifier,
        "dpi": 150,
        "had_sensitive_data": sensitive,
        "pages": [
            {
                "page": 1,
                "original_view_id": view["view_id"],
                "rationale": "Known synthetic page; the upper right box is blank.",
                "masks": [
                    {
                        "rect": [0.9, 0.01, 0.95, 0.02],
                        "category": "personal_identity",
                        "confirmed": True,
                    }
                ]
                if sensitive
                else [],
            }
        ],
    }


def output_review(connection, root, identifier):
    pair = render_output_views(connection, root, identifier, [1])[0]
    return {
        **MANUAL,
        "contract_id": identifier,
        "pages": [
            {
                "page": 1,
                "original_view_id": pair["contract_original"]["view_id"],
                "cleaned_view_id": pair["contract_cleaned"]["view_id"],
                "personal_data_removed": True,
                "contract_content_preserved": True,
                "rationale": "Known synthetic fixture and exact expected terms preserved.",
            }
        ],
    }


def test_multiple_contract_sensitive_flags_require_all_manual_output_reviews(extracted):
    connection, root, ids, _ = extracted
    for number, identifier in enumerate(ids):
        evidence = create_draft(
            connection, root, draft_plan(connection, root, identifier, number == 0)
        )
        assert evidence["manual_review_status"] == "pending"
        with pytest.raises(ValueError, match="faltan paginas"):
            approve_redaction(connection, root, {**MANUAL, "contract_id": identifier})
        review = output_review(connection, root, identifier)
        record_output_reviews(connection, root, review)
        approve_redaction(connection, root, {**MANUAL, "contract_id": identifier})
        flags = connection.execute("SELECT contracts_had_sensitive_data FROM documents").fetchone()[
            0
        ]
        assert flags == (None if number == 0 else 1)
    require_release_reviews(connection, "doc")
    assert connection.execute("SELECT contracts_had_sensitive_data FROM cases").fetchone()[0] == 1
    assert [
        r[0]
        for r in connection.execute("SELECT had_sensitive_data FROM contracts ORDER BY occurrence")
    ] == [1, 0]
    assert (root / "source.pdf").is_file()


def test_rejected_review_and_tampered_pdf_block_approval(extracted):
    connection, root, ids, _ = extracted
    evidence = create_draft(connection, root, draft_plan(connection, root, ids[0]))
    review = output_review(connection, root, ids[0])
    review["pages"][0]["contract_content_preserved"] = False
    record_output_reviews(connection, root, review)
    with pytest.raises(ValueError, match="rechazada"):
        approve_redaction(connection, root, {**MANUAL, "contract_id": ids[0]})
    Path(evidence["path"]).write_bytes(b"modified output")
    with pytest.raises(ValueError, match="cambio desde"):
        record_output_reviews(connection, root, review)


def test_changed_masks_invalidate_reviews_and_old_visual_evidence(extracted):
    connection, root, ids, _ = extracted
    plan = draft_plan(connection, root, ids[0])
    create_draft(connection, root, plan)
    old_review = output_review(connection, root, ids[0])
    record_output_reviews(connection, root, old_review)
    approve_redaction(connection, root, {**MANUAL, "contract_id": ids[0]})
    create_draft(connection, root, draft_plan(connection, root, ids[0], sensitive=True))
    assert connection.execute("SELECT COUNT(*) FROM contract_output_reviews").fetchone()[0] == 0
    # A box over white pixels can preserve identical pixels. Mask hashes still differ,
    # so old approvals cannot survive even when the image itself did not change.
    with pytest.raises(ValueError, match="faltan paginas"):
        approve_redaction(connection, root, {**MANUAL, "contract_id": ids[0]})
    plan = draft_plan(connection, root, ids[0], sensitive=True)
    plan["pages"][0]["masks"][0]["rect"] = [0, 0, 0.8, 0.2]
    create_draft(connection, root, plan)
    with pytest.raises(ValueError, match="otra revision"):
        record_output_reviews(connection, root, old_review)


def test_wrong_contract_view_and_unsupported_sensitive_claim_rejected(extracted):
    connection, root, ids, _ = extracted
    plan = draft_plan(connection, root, ids[0], sensitive=True)
    plan["had_sensitive_data"] = False
    with pytest.raises(ValueError, match="sin datos sensibles"):
        create_draft(connection, root, plan)
    plan["had_sensitive_data"] = True
    foreign = render_original_views(connection, root, ids[1], [1], dpi=150)[0]
    plan["pages"][0]["original_view_id"] = foreign["view_id"]
    with pytest.raises(ValueError, match="otro contrato"):
        create_draft(connection, root, plan)


def test_changed_source_decision_invalidates_output_approval(extracted):
    connection, root, ids, source_review = extracted
    create_draft(connection, root, draft_plan(connection, root, ids[0]))
    review = output_review(connection, root, ids[0])
    record_output_reviews(connection, root, review)
    approve_redaction(connection, root, {**MANUAL, "contract_id": ids[0]})
    record_source_reviews(connection, root, source_review)
    with pytest.raises(ValueError, match="sin extraccion aprobada"):
        approve_redaction(connection, root, {**MANUAL, "contract_id": ids[0]})


def test_draft_rendering_allows_other_catalog_writers(extracted, monkeypatch):
    from datajud_scraper import contract_outputs

    connection, root, ids, _ = extracted
    plan = draft_plan(connection, root, ids[0])
    original_write = contract_outputs.write_cleaned_contract
    database_path = connection.execute("PRAGMA database_list").fetchone()[2]

    def concurrent_write(*args, **kwargs):
        with sqlite3.connect(database_path, timeout=0) as other:
            other.execute("BEGIN IMMEDIATE")
            other.execute("UPDATE request_state SET last_request_started_at=123")
        return original_write(*args, **kwargs)

    monkeypatch.setattr(contract_outputs, "write_cleaned_contract", concurrent_write)
    evidence = create_draft(connection, root, plan)
    assert Path(evidence["path"]).is_file()
    assert (
        connection.execute("SELECT last_request_started_at FROM request_state").fetchone()[0] == 123
    )


def test_concurrent_review_change_rejects_and_removes_stale_draft(extracted, monkeypatch):
    from datajud_scraper import contract_outputs

    connection, root, ids, source_review = extracted
    plan = draft_plan(connection, root, ids[0])
    original_write = contract_outputs.write_cleaned_contract
    outputs = []

    def changed_approval(*args, **kwargs):
        evidence = original_write(*args, **kwargs)
        outputs.append(Path(evidence["path"]))
        record_source_reviews(connection, root, source_review)
        return evidence

    monkeypatch.setattr(contract_outputs, "write_cleaned_contract", changed_approval)
    with pytest.raises(ValueError, match="sin extraccion aprobada"):
        create_draft(connection, root, plan)
    assert outputs and all(not path.exists() for path in outputs)
    assert connection.execute("SELECT COUNT(*) FROM contract_redactions").fetchone()[0] == 0
