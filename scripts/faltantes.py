"""Split a stopped batch between computers: what is done, and a dataset with what is left.

Writes, next to the batch's dataset:
- faltantes_<batch>.jsonl (+ .metadata.json): the pending, running and failed cases, as
  original dataset lines, ready for scrape-dataset / scripts/run_completo.sh on another PC
- hechos_<batch>.csv: position, process number and status of every finished case
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

LEFT = {"pending", "running", "failed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_id")
    parser.add_argument(
        "--dataset", type=Path, default=Path("data/datasets/dataset_hasta2024.jsonl")
    )
    parser.add_argument("--storage-root", type=Path, default=Path("data"))
    args = parser.parse_args()
    results = args.storage_root / "reports" / args.batch_id / "results.jsonl"
    entries = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
    left = {e["process_number"] for e in entries if e["status"] in LEFT}
    done = sorted((e for e in entries if e["status"] not in LEFT), key=lambda e: e["position"])

    lines = [
        line
        for line in args.dataset.read_text(encoding="utf-8-sig").splitlines()
        if json.loads(line)["process_number"] in left
    ]
    records = [json.loads(line) for line in lines]
    metadata = json.loads(
        args.dataset.with_name(args.dataset.stem + ".metadata.json").read_text("utf-8-sig")
    )
    metadata["total_registros"] = len(records)
    metadata["cobertura_campos"] = {
        field: sum(r.get(field) is not None for r in records)
        for field in metadata.get("cobertura_campos", {})
    }
    public = sum(r.get("is_secret") is False for r in records)
    secret = sum(r.get("is_secret") is True for r in records)
    metadata["segredo_justica"] = {
        "publico": public,
        "secreto": secret,
        "desconocido": len(records) - public - secret,
    }
    metadata["subconjunto"] = {
        "origen": str(args.dataset),
        "criterio": f"casos pendientes o fallidos del lote {args.batch_id}",
    }
    tag = args.batch_id[:8]
    out = args.dataset.parent
    (out / f"faltantes_{tag}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / f"faltantes_{tag}.metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (out / f"hechos_{tag}.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["posicion", "proceso", "estado"])
        writer.writerows((e["position"], e["process_number"], e["status"]) for e in done)
    print(f"faltantes: {len(records)} -> {out / f'faltantes_{tag}.jsonl'}")
    print(f"hechos: {len(done)} -> {out / f'hechos_{tag}.csv'}")


if __name__ == "__main__":
    main()
