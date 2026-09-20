from pathlib import Path

from datajud_scraper.dataset import load_dataset, select_records
from datajud_scraper.sampling import sampling_report, stratum


def test_stratified_pilot_covers_real_population_and_is_reproducible():
    root = Path(__file__).resolve().parents[1] / "external" / "dataset"
    loaded = load_dataset(root / "dataset.jsonl", root / "dataset.metadata.json")
    chosen = select_records(loaded.records, 1000, "stratified", 20260919)
    assert len(chosen) == len({r.cnj for r in chosen}) == 1000
    assert {stratum(r) for r in chosen} == {stratum(r) for r in loaded.records}
    assert chosen == select_records(list(reversed(loaded.records)), 1000, "stratified", 20260919)
    report = sampling_report(loaded.records, chosen)
    for dimension in report["dimensions"].values():
        assert all(row["selected"] > 0 for row in dimension)
        assert sum(row["selected"] for row in dimension) == 1000


def test_stratified_limits_and_alternative_seed(dataset_factory):
    from conftest import numbered_record

    loaded = load_dataset(*dataset_factory([numbered_record(i) for i in range(1, 31)]))
    for limit in (1, 3, 15, 30, 100):
        sample = select_records(loaded.records, limit, "stratified", 5)
        assert len(sample) == len({r.cnj for r in sample}) == min(limit, 30)
    assert select_records(loaded.records, 15, "stratified", 5) != select_records(
        loaded.records, 15, "stratified", 6
    )
