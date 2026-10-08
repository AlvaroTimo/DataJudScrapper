from __future__ import annotations

import sqlite3

import pytest

from datajud_scraper.adhesion.common import read_json, workspace, workspace_scope, write_json


def test_workspace_scope_preserves_default_and_restores_after_failure(tmp_path):
    default = workspace(tmp_path)
    with pytest.raises(RuntimeError), workspace_scope(tmp_path, "adhesion-v2"):
        assert workspace(tmp_path) == tmp_path / "adhesion-v2"
        assert workspace(tmp_path / "other") == tmp_path / "other/adhesion-v1"
        raise RuntimeError("interrupted")
    assert workspace(tmp_path) == default
    with pytest.raises(ValueError), workspace_scope(tmp_path, "../adhesion-v2"):
        pass


def test_new_cohort_preserves_legacy_and_excludes_seen_samples(tmp_path, monkeypatch):
    from datajud_scraper.adhesion import prepare as preparation

    backup = tmp_path / "backups/historical"
    (backup / "state").mkdir(parents=True)
    (tmp_path / "pdfs").mkdir()
    with sqlite3.connect(backup / "state/scraper.sqlite3") as connection:
        connection.executescript(
            "CREATE TABLE documents(document_id,case_id,relative_path,sha256,page_count);"
            "CREATE TABLE dataset_records(record_id,case_id);"
            "CREATE TABLE batch_items(record_id,batch_id);"
        )
        for n in range(70):
            identifier = f"synthetic-{n:02d}"
            relative = f"pdfs/{identifier}.pdf"
            (tmp_path / relative).write_bytes(identifier.encode())
            connection.execute(
                "INSERT INTO documents VALUES(?,?,?,?,?)",
                (identifier, identifier, relative, identifier, 1),
            )
            connection.execute("INSERT INTO dataset_records VALUES(?,?)", (n, identifier))
            connection.execute("INSERT INTO batch_items VALUES(?,?)", (n, "batch"))
    legacy = tmp_path / "adhesion-v1"
    write_json(legacy / "preparation.json", {"batch_id": "batch", "seed": 1})
    write_json(legacy / "archive.json", {"path": str(backup), "status": "verified"})
    write_json(legacy / "holdout.json", {"documents": [{"document_id": "synthetic-00"}]})
    before = {p: p.read_bytes() for p in legacy.glob("*.json")}
    exposure = tmp_path / "seen.json"
    write_json(exposure, {"documents": [{"document_id": "synthetic-01"}]})

    def forbidden(*args, **kwargs):
        raise AssertionError("new cohort must not archive historical outputs again")

    monkeypatch.setattr(preparation, "archive", forbidden)
    with workspace_scope(tmp_path, "adhesion-index-v2"):
        result = preparation.prepare(tmp_path, "batch", seed=2, excluded_manifests=[exposure])
        chosen = read_json(workspace(tmp_path) / "holdout.json")["documents"]
        chosen += read_json(workspace(tmp_path) / "development.json")["documents"]
        assert not {r["document_id"] for r in chosen} & {"synthetic-00", "synthetic-01"}
        assert result["holdout_count"] == 25 and result["development_count"] == 30
        assert (
            preparation.prepare(tmp_path, "batch", seed=2, excluded_manifests=[exposure]) == result
        )
        write_json(exposure, {"documents": [{"document_id": "synthetic-02"}]})
        with pytest.raises(ValueError, match="already frozen"):
            preparation.prepare(tmp_path, "batch", seed=2, excluded_manifests=[exposure])
    assert all(p.read_bytes() == contents for p, contents in before.items())
