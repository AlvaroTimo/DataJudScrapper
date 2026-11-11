"""Move PDFs whose text is identical to another PDF of the same subfolder into OUT/duplicated/<sub>/.

usage: uv run --with pymupdf python scripts/limpio/dedup.py OUT
Idempotent: considers PDFs already in duplicated/ too, keeps the lowest name of each group in OUT/<sub>/
and rewrites duplicated/<sub>/duplicados.md.
"""

import collections
import glob
import hashlib
import os
import re
import shutil
import sys

import pymupdf

out = sys.argv[1]
for sub in ["buscar_texto", "contratos_genericos", "indices", "primera_pagina"]:
    dup_dir = f"{out}/duplicated/{sub}"
    os.makedirs(dup_dir, exist_ok=True)
    for p in glob.glob(f"{dup_dir}/*.pdf"):  # re-rendered copy of a known duplicate: drop it
        if os.path.exists(f"{out}/{sub}/{os.path.basename(p)}"):
            os.remove(f"{out}/{sub}/{os.path.basename(p)}")
    groups = collections.defaultdict(list)
    for p in glob.glob(f"{out}/{sub}/*.pdf") + glob.glob(f"{dup_dir}/*.pdf"):
        text = re.sub(r"\s+", " ", "".join(pg.get_text() for pg in pymupdf.open(p)))
        groups[hashlib.md5(text.encode()).hexdigest()].append(p)
    rows = []
    for paths in groups.values():
        paths.sort(key=os.path.basename)
        keep = paths[0]
        if os.path.dirname(keep) != f"{out}/{sub}":
            shutil.move(keep, f"{out}/{sub}/{os.path.basename(keep)}")
        for p in paths[1:]:
            if os.path.dirname(p) != dup_dir:
                shutil.move(p, f"{dup_dir}/{os.path.basename(p)}")
            rows.append((os.path.basename(p), os.path.basename(keep)))
    lines = [f"# Duplicates of {os.path.basename(out)}/{sub}", "",
             "Each PDF here has exactly the same text as the PDF kept in the main folder "
             "(same form, same visible text after anonymization).", "",
             f"| Duplicate (in this folder) | Same text as (kept in ../../{sub}) |", "|---|---|"]
    lines += [f"| {d} | {k} |" for d, k in sorted(rows)]
    open(f"{dup_dir}/duplicados.md", "w").write("\n".join(lines) + "\n")
    print(sub, "unique", len(glob.glob(f"{out}/{sub}/*.pdf")), "duplicates", len(rows))
