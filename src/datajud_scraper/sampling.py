"""Deterministic coverage sampling for the contract discovery pilot."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .dataset import DatasetRecord


def stratum(record: DatasetRecord) -> str:
    data = record.data
    return json.dumps(
        [
            data["process_year"],
            data["classe"],
            data["subject"],
            data["arquivos"]["links_detectados"] == 0,
            bool(data["avisos"]),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def stratified_records(
    records: list[DatasetRecord], limit: int, seed: int
) -> list[DatasetRecord]:
    """Cover joint strata, then allocate residual seats by population size.

    A minimum of one per stratum deliberately overrepresents rare profiles.
    Within strata, prefer courts not yet represented, then their sampling rate.
    The final order is hashed so an interrupted pilot is not rarity-ordered.
    """
    if limit < 1:
        raise ValueError("limit debe ser positivo")
    limit = min(limit, len(records))
    if not records:
        return []

    def rank(value: str) -> bytes:
        return hashlib.sha256(f"{seed}:{value}".encode()).digest()

    groups: dict[str, list[DatasetRecord]] = defaultdict(list)
    for record in records:
        groups[stratum(record)].append(record)
    keys = sorted(groups, key=lambda key: (len(groups[key]), rank(key)))
    if limit < len(keys):
        # Small pilots cannot cover every stratum: use a seeded uniform sample.
        return sorted(records, key=lambda r: rank(r.cnj))[:limit]

    remaining = limit - len(keys)
    capacity = len(records) - len(keys)
    quotas = {key: 1 for key in keys}
    if capacity:
        remainders = {}
        for key in keys:
            seats, remainder = divmod(remaining * (len(groups[key]) - 1), capacity)
            quotas[key] += seats
            remainders[key] = remainder
        for key in sorted(keys, key=lambda key: (-remainders[key], rank(key)))[
            : limit - sum(quotas.values())
        ]:
            quotas[key] += 1

    population = Counter(r.data["orgao_julgador"] for r in records)
    selected_courts: Counter = Counter()
    selected = []
    while len(selected) < limit:
        for key in keys:
            if not quotas[key]:
                continue
            record = min(
                groups[key],
                key=lambda r: (
                    selected_courts[r.data["orgao_julgador"]] > 0,
                    selected_courts[r.data["orgao_julgador"]]
                    / population[r.data["orgao_julgador"]],
                    rank(r.cnj),
                ),
            )
            groups[key].remove(record)
            quotas[key] -= 1
            selected.append(record)
            selected_courts[record.data["orgao_julgador"]] += 1
    return sorted(selected, key=lambda r: rank(f"order:{r.cnj}"))


def sampling_report(records: list[DatasetRecord], selected: list[DatasetRecord]) -> dict:
    dimensions = ("process_year", "classe", "subject", "orgao_julgador")
    report = {
        "population": len(records),
        "selected": len(selected),
        "population_strata": len({stratum(r) for r in records}),
        "selected_strata": len({stratum(r) for r in selected}),
        "dimensions": {},
        "design_note": (
            "Muestra de cobertura con sobrerrepresentacion de estratos raros; "
            "los porcentajes sin ponderar no estiman prevalencia poblacional."
        ),
    }
    for dimension in dimensions:
        total = Counter(r.data[dimension] for r in records)
        sample = Counter(r.data[dimension] for r in selected)
        report["dimensions"][dimension] = [
            {"value": value, "population": total[value], "selected": sample[value]}
            for value in sorted(total, key=lambda value: str(value))
        ]
    return report
