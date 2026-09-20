from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_card_automation import Model
from test_card_automation import auto_source as auto_source

from datajud_scraper.card_automation import run_document
from datajud_scraper.card_publish import publish
from datajud_scraper.card_retention import purge_source, verified_card_release
from datajud_scraper.errors import StorageError
from datajud_scraper.runtime import Database
from datajud_scraper.scraper import ScraperService


@pytest.fixture
def automatic_result(auto_source, monkeypatch):
    root, connection = auto_source
    monkeypatch.setattr(
        "datajud_scraper.card_automation.classify_text",
        lambda _m, pages, _a: {
            p["page_number"]: {
                "page": p["page_number"],
                "state": "card_contract" if p["page_number"] == 2 else "noncontract",
                "confidence": 0.99,
                "kind": "adhesion",
                "start": True,
            }
            for p in pages
        },
    )
    monkeypatch.setattr(
        "datajud_scraper.card_automation.anonymize_page",
        lambda *_a, **_k: {
            "masks": [],
            "had_sensitive_data": False,
            "status": "automatic_checks_passed",
            "checks": {},
        },
    )
    result = run_document(root, "doc", Model())
    run = dict(
        connection.execute(
            "SELECT * FROM card_automation_runs WHERE run_id=?", (result["run_id"],)
        ).fetchone()
    )
    # Synthetic gate fixture only. No real document or review is approved here.
    report = {
        "passed": True,
        "manually_validated_documents": 100,
        "configuration_sha256": run["configuration_sha256"],
    }
    monkeypatch.setattr("datajud_scraper.card_publish.metrics", lambda _: report)
    monkeypatch.setattr("datajud_scraper.card_retention.metrics", lambda _: report)
    private = root / "card-work/doc/inference-cache"
    private.mkdir(parents=True)
    (private / "raw.json").write_text(json.dumps({"unredacted": "synthetic private data"}))
    validation = root / "card-validation/doc"
    validation.mkdir(parents=True)
    (validation / "source.png").write_bytes(b"synthetic source render")
    return root, connection, result, run


def test_retention_keeps_only_verified_clean_outputs_and_resumes_safely(automatic_result):
    root, connection, result, run = automatic_result
    intent = purge_source(root, result["run_id"])
    assert not (root / "source.pdf").exists()
    assert not (root / "contract-work/doc").exists()
    assert not (root / "card-work/doc/inference-cache").exists()
    assert not (root / "card-validation/doc").exists()
    assert Path(run["manifest_path"]).exists()
    assert Path(result["contracts"][0]["output"]["path"]).exists()
    assert (root / intent["contracts"][0]["path"]).is_file()
    assert verified_card_release(connection, root, "doc") == intent
    assert purge_source(root, result["run_id"]) == intent
    assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def test_retention_rejects_a_changed_published_contract(automatic_result):
    root, connection, result, _ = automatic_result
    release = publish(root, result["run_id"])
    (root / release["contracts"][0]["path"]).write_bytes(b"changed output")
    with pytest.raises(ValueError, match="version distinta"):
        purge_source(root, result["run_id"])
    assert (root / "source.pdf").is_file()
    assert (
        connection.execute("SELECT source_disposition FROM documents").fetchone()[0] == "retained"
    )


def test_retention_recovers_a_crash_after_original_was_removed(automatic_result, monkeypatch):
    from datajud_scraper import card_retention

    root, connection, result, _ = automatic_result
    remove = card_retention.prune_sensitive_files

    def fail_after_unlink(root, intent):
        (root / intent["source_path"]).unlink()
        raise OSError("simulated crash")

    monkeypatch.setattr(card_retention, "prune_sensitive_files", fail_after_unlink)
    with pytest.raises(OSError, match="simulated"):
        purge_source(root, result["run_id"])
    assert connection.execute("SELECT status FROM card_retention_events").fetchone()[0] == "purging"
    monkeypatch.setattr(card_retention, "prune_sensitive_files", remove)
    purge_source(root, result["run_id"])
    assert verified_card_release(connection, root, "doc")


def test_scraper_can_reuse_automatically_preserved_card_contracts(
    automatic_result, test_config, monkeypatch
):
    root, connection, result, _ = automatic_result
    purge_source(root, result["run_id"])
    with connection:
        connection.execute("UPDATE documents SET retrieved_at='2026-09-19T22:00:00+00:00'")
        connection.execute("UPDATE cases SET is_secret=0")
    service = ScraperService(replace(test_config, storage_root=root))
    monkeypatch.setattr(service, "_new_client", lambda: pytest.fail("must not redownload"))
    database = Database(root / "state/scraper.sqlite3")
    try:
        preserved = service.scrape_record(None, database, "case", "test-run")
        assert preserved.status == "contracts_preserved"
        assert preserved.contract_count == 1
        preserved.contract_paths[0].write_bytes(b"tampered retained contract")
        with pytest.raises(StorageError, match="reparar o completar"):
            service.scrape_record(None, database, "case", "test-run")
    finally:
        database.close()
