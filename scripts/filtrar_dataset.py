"""Write a subset of the dataset keeping only expedientes filed up to a given year.

Older expedientes usually already contain the bank's defence, which is where the adhesion
contract is attached; recent ones often hold only the initial claim. Lines are copied
verbatim and the metadata counts that scrape-dataset validates are recomputed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hasta-anio", type=int, required=True)
    parser.add_argument("--dataset", type=Path, default=Path("external/dataset/dataset.jsonl"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/datasets"))
    args = parser.parse_args()
    lines = [
        line
        for line in args.dataset.read_text(encoding="utf-8-sig").splitlines()
        if json.loads(line)["process_year"] <= args.hasta_anio
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
    secret = sum(r.get("is_secret") is True for r in records)
    metadata["segredo_justica"] = {
        "publico": sum(r.get("is_secret") is False for r in records),
        "secreto": secret,
        "desconocido": len(records) - secret - sum(r.get("is_secret") is False for r in records),
    }
    metadata["subconjunto"] = {
        "origen": str(args.dataset),
        "criterio": f"process_year <= {args.hasta_anio}",
        "motivo": "expedientes con mayor probabilidad de contener el contrato del banco",
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"dataset_hasta{args.hasta_anio}"
    (args.output_dir / f"{stem}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (args.output_dir / f"{stem}.metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{args.output_dir / stem}.jsonl: {len(records)} expedientes")


if __name__ == "__main__":
    main()
