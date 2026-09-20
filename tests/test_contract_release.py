from __future__ import annotations

import json
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
from test_contract_inventory import catalog as catalog
from test_contract_inventory import source_pdf as source_pdf
from test_contract_outputs import (
    MANUAL,
    draft_plan,
    output_review,
)
from test_contract_outputs import (
    extracted as extracted,
)
from test_contract_review import reviewed_source as reviewed_source

from datajud_scraper import contract_release
from datajud_scraper.contract_outputs import approve_redaction, create_draft, record_output_reviews
from datajud_scraper.contract_release import release_source, verified_release
from datajud_scraper.contract_review import approve_extraction, record_source_reviews
from datajud_scraper.errors import BusyError, StorageError
from datajud_scraper.runtime import Database, StorageLock
from datajud_scraper.scraper import ScraperService


@pytest.fixture
def approved(extracted):
    connection, root, ids, review = extracted
    for identifier in ids:
        create_draft(connection, root, draft_plan(connection, root, identifier))
        record_output_reviews(connection, root, output_review(connection, root, identifier))
        approve_redaction(connection, root, {**MANUAL, "contract_id": identifier})
    return connection, root, ids, review


def test_release_keeps_only_clean_pdfs_and_repeated_release_is_safe(approved):
    connection, root, ids, _ = approved
    manifest = release_source(connection, root, "doc")
    assert len(manifest["contracts"]) == len(ids)
    assert not (root / "source.pdf").exists()
    assert not (root / "contract-work/doc").exists()
    assert len(list((root / "contracts").rglob("*.pdf"))) == 2
    assert connection.execute("SELECT source_disposition FROM documents").fetchone()[0] == "purged"
    assert connection.execute("SELECT status FROM contract_releases").fetchone()[0] == "complete"
    assert release_source(connection, root, "doc") == manifest
    assert verified_release(connection, root, "doc") == manifest
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_no_review_no_deletion_and_scraper_lock_blocks_deletion(extracted):
    connection, root, _, _ = extracted
    with pytest.raises(ValueError, match="limpieza aprobadas"):
        release_source(connection, root, "doc")
    assert (root / "source.pdf").is_file()
    with StorageLock(root / "state/scraper.lock", 0), pytest.raises(BusyError):
        release_source(connection, root, "doc")
    assert (root / "source.pdf").is_file()


def test_publish_failure_keeps_original_and_drafts(approved, monkeypatch):
    connection, root, _, _ = approved
    original = (root / "source.pdf").read_bytes()
    real_copy = contract_release.publish_copy
    copied = []

    def fail_second(source, destination, sha):
        if copied:
            raise OSError("synthetic disk failure")
        real_copy(source, destination, sha)
        copied.append(destination)

    monkeypatch.setattr(contract_release, "publish_copy", fail_second)
    with pytest.raises(OSError, match="disk failure"):
        release_source(connection, root, "doc")
    assert (root / "source.pdf").read_bytes() == original
    assert (root / "contract-work/doc").exists()
    assert connection.execute("SELECT COUNT(*) FROM contract_releases").fetchone()[0] == 0
    monkeypatch.setattr(contract_release, "publish_copy", real_copy)
    release_source(connection, root, "doc")
    assert not (root / "source.pdf").exists()


def test_crash_after_unlink_resumes_without_losing_contracts(approved, monkeypatch):
    connection, root, _, _ = approved
    real_remove = contract_release.remove_source_files

    def crash_after_unlink(root, manifest):
        (root / manifest["source_path"]).unlink()
        raise OSError("synthetic crash after unlink")

    monkeypatch.setattr(contract_release, "remove_source_files", crash_after_unlink)
    with pytest.raises(OSError, match="after unlink"):
        release_source(connection, root, "doc")
    assert connection.execute("SELECT source_disposition FROM documents").fetchone()[0] == "purging"
    assert connection.execute("SELECT status FROM contract_releases").fetchone()[0] == "purging"
    assert len(list((root / "contracts").rglob("*.pdf"))) == 2
    monkeypatch.setattr(contract_release, "remove_source_files", real_remove)
    release_source(connection, root, "doc")
    assert verified_release(connection, root, "doc")
    assert not (root / "contract-work/doc").exists()


def test_changed_clean_output_blocks_purge_and_preservation_result(approved):
    connection, root, _, _ = approved
    clean = connection.execute("SELECT cleaned_path FROM contracts LIMIT 1").fetchone()[0]
    Path(clean).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="cambio desde"):
        release_source(connection, root, "doc")
    assert (root / "source.pdf").is_file()
    assert (root / "contract-work/doc").exists()


def test_verified_absence_can_be_released_without_creating_contract(reviewed_source):
    connection, root, review, _ = reviewed_source
    # Synthetic catalog-only negative fixture: all decisions are deliberately set
    # by the test, never generated for real documents by this routine.
    for page in review["pages"]:
        page["decision"] = "noncontract"
    record_source_reviews(connection, root, review)
    approve_extraction(connection, root, {**MANUAL, "document_id": "doc", "contracts": []})
    manifest = release_source(connection, root, "doc")
    assert manifest["contracts"] == []
    assert not (root / "source.pdf").exists()
    assert tuple(
        connection.execute(
            "SELECT has_contract,contract_count,contracts_had_sensitive_data FROM documents"
        ).fetchone()
    ) == (0, 0, 0)


def test_scraper_reuses_clean_contracts_without_redownloading_raw_source(
    approved, test_config, monkeypatch
):
    connection, root, _, _ = approved
    release_source(connection, root, "doc")
    with connection:
        connection.execute("UPDATE documents SET retrieved_at='2026-09-19T22:00:00+00:00'")
        connection.execute("UPDATE cases SET is_secret=0")
    config = replace(test_config, storage_root=root)
    service = ScraperService(config)
    monkeypatch.setattr(service, "_new_client", lambda: pytest.fail("must not contact portal"))
    with closing(Database(root / "catalog.sqlite3")) as database:
        result = service.scrape_record(None, database, "case", "test-run")
        assert result.status == "contracts_preserved"
        assert result.pdf_path is None and result.contract_count == 2
        assert all(path.is_file() for path in result.contract_paths)
        clean = result.contract_paths[0]
        clean.write_bytes(b"damaged retained contract")
        with pytest.raises(StorageError, match="reparar o completar"):
            service.scrape_record(None, database, "case", "test-run")
    assert not (root / "source.pdf").exists()


def test_manifest_cannot_silently_drop_a_contract(approved):
    connection, root, _, _ = approved
    release_source(connection, root, "doc")
    row = connection.execute("SELECT manifest_json FROM contract_releases").fetchone()
    manifest = json.loads(row[0])
    manifest["contracts"].pop()
    with connection:
        connection.execute("UPDATE contract_releases SET manifest_json=?", (json.dumps(manifest),))
    with pytest.raises(ValueError, match="exactamente"):
        verified_release(connection, root, "doc")
