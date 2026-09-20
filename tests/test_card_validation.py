from __future__ import annotations

import json

import pytest

from datajud_scraper.card_publish import publish
from datajud_scraper.card_validation import (
    validate_view_coverage,
    validation_members,
    verified_manifest,
)
from datajud_scraper.pdf_validation import hash_file


def test_publication_is_blocked_before_full_validation(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "datajud_scraper.card_publish.metrics",
        lambda _: {"passed": False, "manually_validated_documents": 99},
    )
    original = tmp_path / "source.pdf"
    original.write_bytes(b"preserve until validation succeeds")
    with pytest.raises(ValueError, match="100 expedientes"):
        publish(tmp_path, "not-validated")
    assert original.exists()
    assert not (tmp_path / "card-contracts").exists()


def test_validation_requires_100_unique_documents(tmp_path):
    path = tmp_path / "reports/card-automation/validation-set.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"size": 100, "documents": [{"document_id": "same"}] * 100}))
    with pytest.raises(ValueError, match="duplicados"):
        validation_members(tmp_path)
    path.write_text(json.dumps({"size": 99, "documents": []}))
    with pytest.raises(ValueError, match="exactamente 100"):
        validation_members(tmp_path)


def test_validation_checks_page_identity_not_only_page_count():
    manifest = {"contracts": [{"contract_id": "contract", "output": {"page_count": 1}}]}
    views = [
        {"scope": "source", "contract_id": None, "page": 1},
        {"scope": "source", "contract_id": None, "page": 2},
        {"scope": "cleaned", "contract_id": "contract", "page": 1},
    ]
    validate_view_coverage(views, 2, manifest)
    with pytest.raises(ValueError, match="duplicadas"):
        validate_view_coverage([views[0], views[0], views[2]], 2, manifest)
    with pytest.raises(ValueError, match="paginas"):
        validate_view_coverage(views[:2], 2, manifest)


def test_validation_rejects_tampered_cleaned_pdf_even_with_unchanged_manifest(tmp_path):
    cleaned = tmp_path / "cleaned.pdf"
    cleaned.write_bytes(b"original output")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "run_id": "run",
                "contracts": [{"output": {"path": str(cleaned), "sha256": hash_file(cleaned)}}],
            }
        )
    )
    run = {"run_id": "run", "manifest_path": str(manifest), "manifest_sha256": hash_file(manifest)}
    assert verified_manifest(run)["run_id"] == "run"
    cleaned.write_bytes(b"changed output")
    with pytest.raises(ValueError, match="PDF limpio"):
        verified_manifest(run)
