"""Transcribe one scanned document with a single stateless Claude Code call (no tools, images inline).

usage: uv run --with reportlab --with pymupdf python scripts/limpio/call.py PDF OUTDIR [--mode strips|halves] [--dpi 250]
Needs the `claude` CLI signed in (`claude auth login`). Writes OUTDIR/<NAME>.txt (markup),
<NAME>.notes.md, <NAME>.usage.json and <NAME>.raw.txt.
"""

import argparse
import base64
import io
import json
import os
import subprocess
import sys
import time

import pymupdf
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
BRIEF = open(os.path.join(HERE, "brief.md"), encoding="utf-8").read()


def page_images(page, mode, dpi):
    """4 overlapping strips (scans) or 2 overlapping halves (clean digital prints)."""
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    im = Image.frombytes("L", (pix.w, pix.h), pix.samples)
    k = 4 if mode == "strips" else 2
    step, ov = im.height // k, int(dpi * 0.3)
    return [im.crop((0, max(0, i * step - ov), im.width, min(im.height, (i + 1) * step + ov))) for i in range(k)]


def b64(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("outdir")
    ap.add_argument("--mode", default="strips", choices=["strips", "halves"])
    ap.add_argument("--dpi", type=int, default=250)
    ap.add_argument("--model", default="claude-opus-5-5")
    a = ap.parse_args()
    name = os.path.basename(a.pdf)[:-4]
    os.makedirs(a.outdir, exist_ok=True)
    doc = pymupdf.open(a.pdf)
    piece = "4 horizontal strips" if a.mode == "strips" else "top half, then bottom half"
    content = [{"type": "text", "text": f"Document NAME: {name} — {len(doc)} page(s). Transcribe it following the brief."}]
    for i, page in enumerate(doc):
        content.append({"type": "text", "text": f"Page {i + 1} — {piece}, top→bottom (they overlap by a few lines)"})
        for im in page_images(page, a.mode, a.dpi):
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64(im)}})

    msg = {"type": "user", "message": {"role": "user", "content": content}}
    cmd = ["claude", "-p", "--model", a.model, "--tools", "", "--strict-mcp-config",
           "--system-prompt", BRIEF, "--input-format", "stream-json", "--output-format", "stream-json",
           "--verbose", "--no-session-persistence", "--setting-sources", ""]
    t0 = time.time()
    res = subprocess.run(cmd, input=json.dumps(msg) + "\n", capture_output=True, text=True, cwd=HERE, timeout=3600)
    events = [json.loads(l) for l in res.stdout.splitlines() if l.strip().startswith("{")]
    final = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if not final or final.get("is_error"):
        sys.exit(f"{name}: call failed: rc={res.returncode} {res.stderr[-300:]} {final.get('result') if final else ''}")
    text = final.get("result", "")
    open(f"{a.outdir}/{name}.raw.txt", "w", encoding="utf-8").write(text)
    markup, _, notes = text.partition("=== NOTES ===")
    markup = "\n".join(l for l in markup.strip().splitlines() if not l.startswith("```"))
    open(f"{a.outdir}/{name}.txt", "w", encoding="utf-8").write(markup.strip() + "\n")
    open(f"{a.outdir}/{name}.notes.md", "w", encoding="utf-8").write(notes.strip() + "\n")
    u = final.get("usage") or {}
    json.dump({"name": name, "pages": len(doc), "mode": a.mode, "dpi": a.dpi, "seconds": round(time.time() - t0, 1),
               "usage": u}, open(f"{a.outdir}/{name}.usage.json", "w"), indent=1)
    print(f"{name}: {len(doc)}p in={u.get('input_tokens')} cw={u.get('cache_creation_input_tokens')} "
          f"cr={u.get('cache_read_input_tokens')} out={u.get('output_tokens')} {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
