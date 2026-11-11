"""Transcribe a share of each subfolder (pixel-identical scans transcribed once), then build PDFs.

usage: uv run --with reportlab --with pymupdf python scripts/limpio/run_batch.py SRC TRANS OUT [--half 1|2|all] [--workers 6]
  SRC   input folder with subfolders (buscar_texto, contratos_genericos, indices, primera_pagina)
  TRANS where markup is kept (resumable: documents with a .usage.json are skipped)
  OUT   output folder for PDFs (OUT/<sub>/<name>.pdf); duplicates are sorted out by dedup.py afterwards
If the plan's usage limit is reached, workers pause and retry every 10 minutes, then continue.
"""

import argparse
import collections
import concurrent.futures as cf
import glob
import hashlib
import os
import shutil
import subprocess
import sys
import threading
import time

import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
SUBS = ["buscar_texto", "contratos_genericos", "indices", "primera_pagina"]

ap = argparse.ArgumentParser()
ap.add_argument("src")
ap.add_argument("trans")
ap.add_argument("out")
ap.add_argument("--half", default="all", choices=["1", "2", "all"])
ap.add_argument("--workers", type=int, default=6)
ap.add_argument("--take", default="", help="sub=N,... : only the first N selected documents of each subfolder")
ap.add_argument("--pause", type=float, default=10)
a = ap.parse_args()


def log(msg):
    print(f"{time.strftime('%H:%M')} {msg}", flush=True)


# 1. select documents and group pixel-identical scans (first name in a group = the one transcribed)
jobs, copies = [], []  # jobs: (sub, rep); copies: (sub, dup, rep)
for sub in SUBS:
    files = sorted(glob.glob(f"{a.src}/{sub}/*.pdf"))
    k = (len(files) + 1) // 2
    sel = files if a.half == "all" else files[:k] if a.half == "1" else files[k:]
    take = dict(kv.split("=") for kv in a.take.split(",") if kv)
    if sub in take:
        sel = sel[:int(take[sub])]
    groups = collections.defaultdict(list)
    for f in files:  # group over the whole subfolder so reps are stable across halves
        sig = tuple(hashlib.md5(p.get_pixmap(dpi=40).samples).hexdigest() for p in pymupdf.open(f))
        groups[sig].append(os.path.basename(f)[:-4])
    rep_of = {n: g[0] for g in groups.values() for n in g}
    for f in sel:
        n = os.path.basename(f)[:-4]
        if rep_of[n] == n:
            jobs.append((sub, n))
        else:
            copies.append((sub, n, rep_of[n]))
            if (sub, rep_of[n]) not in jobs:
                jobs.append((sub, rep_of[n]))
todo = [(s, n) for s, n in jobs if not os.path.exists(f"{a.trans}/{s}/{n}.usage.json")]
log(f"{len(jobs) + len(copies)} documents ({len(copies)} identical copies), {len(todo)} to transcribe")

# 2. transcribe, pausing on usage limits
paused, probe = threading.Event(), threading.Lock()


def limited(out):
    return any(k in out.lower() for k in ("limit", "rate_limit", "overloaded", "429", "529"))


def call(sub, n):
    r = subprocess.run([sys.executable, os.path.join(HERE, "call.py"), f"{a.src}/{sub}/{n}.pdf",
                        f"{a.trans}/{sub}", "--mode", "strips", "--dpi", "250"], capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    return r.returncode, ("limit: " + out[-300:]) if r.returncode and limited(out) else out[-300:]


def one(job):
    while True:
        while paused.is_set():
            if probe.acquire(blocking=False):
                try:
                    time.sleep(a.pause * 60)
                    rc, out = call(*job)
                    if rc == 0 or not out.startswith("limit"):
                        paused.clear()
                        log("resumed")
                        return job, rc, out
                    log("still limited, waiting")
                finally:
                    probe.release()
            else:
                time.sleep(20)
        rc, out = call(*job)
        if rc and out.startswith("limit"):
            if not paused.is_set():
                log("usage limit reached — pausing")
            paused.set()
            continue
        return job, rc, out


fails = 0
with cf.ThreadPoolExecutor(a.workers) as ex:
    for job, rc, out in ex.map(one, todo):
        log(("OK   " if rc == 0 else "FAIL ") + out)
        fails += rc != 0

# 3. copies of identical scans reuse the transcription; render every selected document
for sub, dup, rep in copies:
    for ext in (".txt", ".notes.md"):
        if os.path.exists(f"{a.trans}/{sub}/{rep}{ext}"):
            shutil.copy(f"{a.trans}/{sub}/{rep}{ext}", f"{a.trans}/{sub}/{dup}{ext}")
sys.path.insert(0, HERE)
import render  # noqa: E402

built = bad = 0
for sub, n in jobs + [(s, d) for s, d, _ in copies]:
    src = f"{a.trans}/{sub}/{n}.txt"
    if not os.path.exists(src):
        continue
    os.makedirs(f"{a.out}/{sub}", exist_ok=True)
    try:
        render.build(src, f"{a.out}/{sub}/{n}.pdf")
        built += 1
    except Exception as e:  # noqa: BLE001
        bad += 1
        log(f"RENDER FAIL {sub}/{n}: {e}")
log(f"done: {fails} transcription failures, {built} PDFs built, {bad} render failures")
