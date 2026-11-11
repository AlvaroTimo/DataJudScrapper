"""Copy downloaded PDFs into the sample folder as NNN_<process>.pdf.

usage: python3 scripts/copiar_descargas.py POSITIONS.json STORAGE_ROOT [DEST]
"""

import glob
import json
import os
import shutil
import sys

pos = json.load(open(sys.argv[1]))
root = sys.argv[2]
dst = sys.argv[3] if len(sys.argv) > 3 else "data/dataset-contracts"
n, missing = 0, []
for proc, p in pos.items():
    hits = glob.glob(f"{root}/**/*{proc}*.pdf", recursive=True)
    if hits:
        shutil.copy2(max(hits, key=os.path.getsize), f"{dst}/{p:03d}_{proc}.pdf")
        n += 1
    else:
        missing.append(proc)
print(f"copiados {n}, faltan {len(missing)}")
for m in missing:
    print("  falta", m)
print("total en la carpeta:", len(os.listdir(dst)))
