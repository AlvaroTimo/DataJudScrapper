from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import respx
from conftest import numbered_record, record_data

from datajud_scraper.batch import BatchService
from datajud_scraper.dataset import load_dataset, select_records
from datajud_scraper.errors import InvalidInputError
from datajud_scraper.runtime import Database


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("process_number", "0003714-34.2025.8.05.0274"),
        ("process_number_digits", "11111111111111111111"),
        ("process_year", 2024),
        ("projudi_internal_id", "wrong"),
        ("source_url", "http://example.com/"),
        ("url_download", "https://projudi.tjba.jus.br/projudi/acoes/DownloadArquivo?arquivo=1"),
        ("distribution_at", "2025-04-03"),
        ("data_ajuizamento", "2025-02-30"),
        ("arquivos", {"links_detectados": -1}),
        ("datajud_subjects", None),
        ("is_secret", 1),
    ],
)
def test_entire_input_validated_before_storage_or_network(
    dataset_factory,
    test_config,
    field,
    value,
):
    records = [record_data(), numbered_record(2)]
    records[1][field] = value
    inputs = dataset_factory(records)
    with (
        respx.mock(assert_all_called=False) as router,
        pytest.raises(InvalidInputError, match="linea 2"),
    ):
        BatchService(test_config).start(*inputs, limit=1)
    assert len(router.calls) == 0 and not test_config.storage_root.exists()


@pytest.mark.parametrize(
    "bad_line", ["{}", "[]", "null", "{", "", '{"a": 1, "a": 2}', '{"x": NaN}']
)
def test_malformed_json_reports_line(dataset_factory, bad_line):
    path, meta = dataset_factory()
    with path.open("a") as file:
        file.write(bad_line + "\n")
    with pytest.raises(InvalidInputError, match="linea 2"):
        load_dataset(path, meta)


def test_duplicate_identity_and_bad_metadata_rejected(dataset_factory):
    inputs = dataset_factory([record_data(), record_data()])
    with pytest.raises(InvalidInputError, match="linea 2.*duplicado"):
        load_dataset(*inputs)
    inputs = dataset_factory(metadata_extra={"total_registros": 2})
    with pytest.raises(InvalidInputError, match="total_registros"):
        load_dataset(*inputs)
    inputs = dataset_factory(metadata_extra={"cobertura_campos": {"subject": 0}})
    with pytest.raises(InvalidInputError, match="cobertura"):
        load_dataset(*inputs)


def test_import_preserves_all_original_metadata_and_does_not_verify(dataset_factory, tmp_path):
    inputs = dataset_factory([record_data(subject=None, extra={"a": [None, "á", 9]})])
    dataset = load_dataset(*inputs)
    original = [p.read_bytes() for p in inputs]
    db = Database(tmp_path / "catalog.sqlite3")
    try:
        BatchService._import(db, dataset)
        BatchService._import(db, load_dataset(*inputs))
        assert db.connection.execute("SELECT COUNT(*) FROM dataset_records").fetchone()[0] == 1
        row = db.connection.execute("SELECT raw_json,line_number FROM dataset_records").fetchone()
        assert row[0] == original[0].decode().rstrip("\n") and row[1] == 1
        assert (
            db.connection.execute("SELECT metadata_json FROM datasets").fetchone()[0]
            == original[1].decode()
        )
        observed = db.connection.execute(
            "SELECT last_checked_at,source_url,codigo_hash,projudi_internal_id,is_secret FROM cases"
        ).fetchone()
        assert tuple(observed) == (None, None, None, None, None)
        assert db.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert [p.read_bytes() for p in inputs] == original
    finally:
        db.close()


def test_deterministic_selection_and_limit(dataset_factory):
    records = [numbered_record(i) for i in range(1, 30)]
    loaded = load_dataset(*dataset_factory(records))
    first = select_records(loaded.records, 15, "diverse", 20260908)
    assert first == select_records(list(reversed(loaded.records)), 15, "diverse", 20260908)
    assert len(first) == len({r.cnj for r in first}) == 15
    assert len(select_records(loaded.records, None, "diverse", 0)) == 29
    assert select_records(loaded.records, 2, "first", 0) == loaded.records[:2]


def test_real_dataset_diversity_and_fingerprints():
    root = Path(__file__).resolve().parents[1] / "external" / "dataset"
    if not root.exists():
        pytest.skip("dataset local no distribuido con el codigo")
    dataset = load_dataset(root / "dataset.jsonl", root / "dataset.metadata.json")
    assert len(dataset.records) == 10713
    assert dataset.sha256 == "a5a222cc80c4db390d41366b819a9a3046e631a6b6f2138c7b3bfcb217898df0"
    assert (
        dataset.metadata_sha256
        == "f153f7dd07005f80c884c84a1a528c104df4d9067f16ce8cdc94a1c882e23053"
    )
    chosen = select_records(dataset.records, 15, "diverse", 20260908)
    assert len({r.data["orgao_julgador"] for r in chosen}) == 15
    assert len({r.data["classe"] for r in chosen}) == 6
    assert len({r.data["process_year"] for r in chosen}) == 5
    assert chosen[0].data["subject"] is None and chosen[1].data["datajud_subjects"] == []
    assert [bool(r.data["avisos"]) for r in chosen[2:7]] == [True, False, False, True, True]
    assert all(r.data["arquivos"]["links_detectados"] == 0 for r in chosen[2:5])
    assert [r.data["process_year"] for r in chosen[7:14]] == [2026] * 4 + [2025] * 2 + [2024]
    assert chosen[14].data["process_year"] < 2024
    assert hashlib.sha256((root / "dataset.jsonl").read_bytes()).hexdigest() == dataset.sha256
