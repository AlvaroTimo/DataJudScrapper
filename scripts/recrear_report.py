"""Rebuild an anonymized report (data/output_anonim/reportN) as clean, 1:1 text PDFs.

    recrear_report.py inventario  --report report5
    recrear_report.py indice      --report report5
    recrear_report.py candidatos  --report report5          # line-review batches
    recrear_report.py procesar    --report report5 [--solo NAME ...] [--lote 10] [--limite N]
    recrear_report.py resumen     --report report5

Output: data/output_anonim/recreados/<report>/<subfolder>/<same name>.pdf (only anonymized PDFs and
resumen.md). Everything that may contain personal data (line index, OCR caches, line review,
corrections) and the run state (estado.jsonl, qa/<name>.json) live in data/recreacion/<report>/.
Sources per document (inventario.json):
  A        report5 file found in a local run manifest: masks with categories, the original
           expediente and the unmasked 300 dpi raster are available
  C        only the anonymized raster
  G-orig   generic bank contract with its original expediente; G-noorig without it
Personal data is removed, never painted: it shows as blank space.
"""

from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

import cv2
import numpy as np
import pymupdf

import recrear_politica as P
import recrear_raster as RS
import recrear_vector as V
from PIL import Image

ROOT = Path("data/output_anonim")
ORIGINALS = Path("data/output/1000-pdfs-hasta2024-seed20261009")
pymupdf.TOOLS.mupdf_display_errors(False)


def out_dir(report):
    return ROOT / "recreados" / report


def work_dir(report):
    return Path("data/recreacion") / report


def load_inventory(report):
    return json.loads((work_dir(report) / "inventario.json").read_text())


# ------------------------------------------------------------------ inventory
def cmd_inventario(args):
    by_sha = {}
    for mp in glob.glob("data/adhesion-scraper/runs/*/*/manifest.json"):
        m = json.load(open(mp))
        for ins in m.get("instruments", []):
            sha = (ins.get("output") or {}).get("sha256")
            if sha:
                by_sha.setdefault(sha, []).append((mp, ins["contract_id"]))
    rows = []
    for f in sorted((ROOT / args.report).rglob("*.pdf")):
        sha = hashlib.sha256(f.read_bytes()).hexdigest()
        m = re.match(r"(.+)_p(\d+)(?:-(\d+))?$", f.stem)
        orig = ORIGINALS / f"{m.group(1)}.pdf"
        hits = by_sha.get(sha, [])
        if f.parent.name == "contratos_genericos":
            tier = "G-orig" if orig.exists() else "G-noorig"
        else:
            tier = "A" if hits and orig.exists() else "C"
        rows.append(dict(
            file=str(f), sub=f.parent.name, name=f.name, sha256=sha, pages=len(pymupdf.open(f)),
            first=int(m.group(2)), last=int(m.group(3) or m.group(2)),
            original=str(orig) if orig.exists() else None,
            manifest=hits[-1][0] if hits else None, contract_id=hits[-1][1] if hits else None, tier=tier,
        ))
    work_dir(args.report).mkdir(parents=True, exist_ok=True)
    (work_dir(args.report) / "inventario.json").write_text(json.dumps(rows, indent=1))
    print(collections.Counter(r["tier"] for r in rows))


# ------------------------------------------------------------------ per-document sources
class Doc:
    """Everything known about one report file."""

    def __init__(self, row):
        self.row = row
        self.name = row["name"]
        self.exp = Path(row["original"]).stem if row["original"] else row["name"].rsplit("_p", 1)[0]
        self.report_pdf = row["file"]
        self.instrument = None
        self.page_dirs = []
        if row["tier"] == "A":
            m = json.load(open(row["manifest"]))
            self.instrument = next(i for i in m["instruments"] if i["contract_id"] == row["contract_id"])
            base = os.path.join(os.path.dirname(row["manifest"]), row["contract_id"])
            self.page_dirs = sorted(glob.glob(os.path.join(base, "source-*")))

    def source_page(self, k):
        """Expediente page number of output page k (0-based)."""
        return self.row["first"] + k

    def word_source(self, k):
        if self.instrument is None:
            return None
        return self.instrument["privacy"][k]["checks"]["word_source"]

    def masks(self, k):
        """[(rect_norm, category, kind)] of the original anonymization on output page k."""
        if self.instrument is None:
            return []
        priv = self.instrument["privacy"][k]
        folder = self.page_dirs[k]
        regions = {}
        for stage, fname in ((None, "private-regions.json"), ("output_ocr_0", "private-output-regions-0.json"),
                             ("output_ocr_1", "private-output-regions-1.json")):
            path = os.path.join(folder, fname)
            if os.path.exists(path):
                data = json.load(open(path))
                for reg in data["regions"] if isinstance(data, dict) else data:
                    regions[(stage, reg["id"])] = reg["kind"]
        out = []
        for mk in priv["masks"]:
            kind = regions.get((mk.get("region_stage"), mk.get("region_id")), mk.get("rule", "identifier"))
            out.append((mk["rect"], mk["category"], kind))
        return out


# ------------------------------------------------------------------ line index
def index_path(report):
    return work_dir(report) / "indice_lineas.json"


def styled_lines(page):
    """[(key, text, runs, serif)] from the native text layer; runs = [[text, "B"|"R"]]."""
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            runs, serif = [], 0
            for sp in l["spans"]:
                if not sp["text"]:
                    continue
                style = "B" if (sp["flags"] & 16 or "bold" in sp["font"].lower()) else "R"
                serif += len(sp["text"]) * (1 if (sp["flags"] & 4 or re.search("times|serif|roman|georgia|garamond", sp["font"], re.I)) else -1)
                if runs and runs[-1][1] == style:
                    runs[-1][0] += sp["text"]
                else:
                    runs.append([sp["text"], style])
            text = "".join(r[0] for r in runs).strip()
            if text:
                out.append((P.key(text), re.sub(r"\s+", " ", text), runs, serif > 0))
    return out


def cmd_indice(args):
    """Normalized line -> distinct expedientes (+ one styled sample), from native contract pages."""
    index = collections.defaultdict(set)
    sample = {}
    images = collections.defaultdict(set)
    for row in load_inventory(args.report):
        if row["tier"] not in ("A", "G-orig"):
            continue
        doc = Doc(row)
        src = pymupdf.open(row["original"])
        for k in range(row["pages"]):
            if row["tier"] == "A" and doc.word_source(k) != "native":
                continue
            page = src[doc.source_page(k) - 1]
            if len(page.get_text().strip()) < 200:
                continue
            for text, _, _ in V.native_lines(page):
                index[P.key(text)].add(doc.exp)
            for key, text, runs, serif in styled_lines(page):
                index[key].add(doc.exp)
                sample.setdefault(key, {"texto": text, "runs": runs, "serif": serif})
            for info in page.get_images(full=True):
                h = V.image_hash(src, info[0])
                if h:
                    images[h].add(doc.exp)
    data = {k: {"n": len(v), **sample.get(k, {})} for k, v in index.items()}
    index_path(args.report).write_text(json.dumps(data, ensure_ascii=False))
    image_path(args.report).write_text(json.dumps({k: len(v) for k, v in images.items()}))
    print("lineas distintas:", len(data), "| en >=3 expedientes:", sum(v["n"] >= 3 for v in data.values()))
    print("imagenes distintas:", len(images), "| en >=3 expedientes:", sum(len(v) >= 3 for v in images.values()))


def image_path(report):
    return work_dir(report) / "indice_imagenes.json"


def load_image_index(report):
    p = image_path(report)
    return json.loads(p.read_text()) if p.exists() else {}


def load_index(report):
    p = index_path(report)
    return json.loads(p.read_text()) if p.exists() else {}


def review_path(report):
    return work_dir(report) / "revision_lineas.json"


def load_review(report):
    p = review_path(report)
    return json.loads(p.read_text()) if p.exists() else {}


# ------------------------------------------------------------------ coverage helpers
G = 1000
IMAGE_INDEX = {}
LEXICON = set()  # words of boilerplate lines: anything else read with low confidence may be handwriting


def mask_grid(masks):
    grid = np.zeros((G, G), np.float32)
    for rect, _cat, _kind in masks:
        x0, y0, x1, y1 = rect
        grid[int(y0 * G):int(np.ceil(y1 * G)), int(x0 * G):int(np.ceil(x1 * G))] = 1
    return grid


def covered(grid, rect_norm):
    x0, y0, x1, y1 = rect_norm
    a, b = int(y0 * G), max(int(y0 * G) + 1, int(np.ceil(y1 * G)))
    c, d = int(x0 * G), max(int(x0 * G) + 1, int(np.ceil(x1 * G)))
    sub = grid[max(0, a):min(G, b), max(0, c):min(G, d)]
    return float(sub.mean()) if sub.size else 0.0


# ------------------------------------------------------------------ vector pages
def native_page(doc, k):
    """Generic contracts: is this page native text (else it is processed as a scan)?"""
    if doc.row["tier"] == "G-orig":
        page = pymupdf.open(doc.row["original"])[doc.source_page(k) - 1]
    else:
        page = pymupdf.open(doc.report_pdf)[k]
    area = page.rect.get_area()
    cover = max([(r & page.rect).get_area() / area for x in page.get_images() for r in page.get_image_rects(x[0])] or [0])
    return len(page.get_text().strip()) > 200 and cover < 0.5


def vector_page(doc, k, index, review, out, record, page_corr=None):
    if doc.row["tier"] == "G-noorig":  # the report copy already lost the stamp: paint its black box white
        src_path, page_number = doc.report_pdf, k + 1
    elif doc.row["tier"] == "G-orig":
        src_path, page_number = doc.row["original"], doc.source_page(k)
    else:
        src_path, page_number = doc.row["original"], doc.source_page(k)
    masks = doc.masks(k)
    grid = mask_grid(masks)

    def decide(lines, page):
        W, H = page.rect.width, page.rect.height
        hidden, shown, restored_boxes = [], [], []
        for text, rect, words in lines:
            cov = [covered(grid, (r.x0 / W, r.y0 / H, r.x1 / W, r.y1 / H)) for _, r in words]
            key = P.key(text)
            n = index.get(key, {}).get("n", 0)
            visible, reason = P.decide_words([w for w, _ in words], cov, n, review.get(key, {}).get("veredicto"))
            record["lineas"][reason.split("+")[0]] += 1
            for (w, r), v in zip(words, visible):
                (shown if v else hidden).append((w, r) if v else r)
            if reason.startswith("restaurada"):
                restored_boxes.append(pymupdf.Rect(rect[0] * W, rect[1] * H, rect[2] * W, rect[3] * H))
        for ax0, ay0, ax1, ay1 in (page_corr or {}).get("ocultar_areas", []):  # visual review
            box = pymupdf.Rect(ax0 * W, ay0 * H, ax1 * W, ay1 * H)
            for _t, _rect, words in lines:
                for w, r in words:
                    if (r & box).get_area() > 0.3 * r.get_area() and r not in hidden:
                        hidden.append(r)
        areas = [(pymupdf.Rect(ax0 * W, ay0 * H, ax1 * W, ay1 * H), True)
                 for ax0, ay0, ax1, ay1 in (page_corr or {}).get("ocultar_areas", [])]
        for (x0, y0, x1, y1), _cat, kind in masks:
            r = pymupdf.Rect(x0 * W, y0 * H, x1 * W, y1 * H)
            text_part = sum((r & b).get_area() for b in restored_boxes)
            if kind in ("signature", "graphic", "measured_qr") or text_part < 0.8 * r.get_area():
                areas.append((r, kind in ("signature", "graphic")))
        return hidden, areas, [w for w, _ in shown]

    generic = doc.row["tier"].startswith("G")  # bank artwork only, no client data
    info = V.build(src_path, page_number, decide, out, IMAGE_INDEX,
                   whiten_black=doc.row["tier"] == "G-noorig", keep_images=generic)
    return info


# ------------------------------------------------------------------ documents
def process(doc, index, review, target, work, canon, corrections):
    out = pymupdf.open()
    record = {"name": doc.name, "tier": doc.row["tier"], "pages": [], "lineas": collections.Counter()}
    for k in range(doc.row["pages"]):
        t0 = time.time()
        if doc.row["tier"] == "A" and doc.word_source(k) == "native":
            info = vector_page(doc, k, index, review, out, record, corrections.get(doc.name, {}).get(str(k + 1)))
        elif doc.row["tier"].startswith("G") and native_page(doc, k):
            info = vector_page(doc, k, index, review, out, record, corrections.get(doc.name, {}).get(str(k + 1)))
        else:
            info = raster_page(doc, k, index, review, out, record, work, canon, corrections.get(doc.name, {}))
        info["seconds"] = round(time.time() - t0, 2)
        info.pop("shown_text", None)
        record["pages"].append(info)
    out.set_metadata({"title": doc.name, "producer": "recrear_report.py"})
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp.pdf")
    out.save(tmp, garbage=4, deflate=True)
    os.replace(tmp, target)
    record["lineas"] = dict(record["lineas"])
    return record


def page_image(pdf_path, k, dpi=300):
    page = pymupdf.open(pdf_path)[k]
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()


def cached_ocr(work, doc, k, img):
    path = work / "cache" / doc.name / f"p{k + 1}.ocr.json"
    if path.exists():
        return json.loads(path.read_text())
    lines = RS.ocr_lines(Image.fromarray(img))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lines, ensure_ascii=False))
    return lines


def raster_page(doc, k, index, review, out, record, work, canon, corrections):
    tier = doc.row["tier"]
    if tier == "A":
        src = page_image(doc.instrument["extracted_output"]["path"], k)  # unmasked 300 dpi raster
    elif tier == "G-orig":
        src = page_image(doc.row["original"], doc.source_page(k) - 1)  # scanned page of a generic contract
    else:
        src = page_image(doc.report_pdf, k)  # only the anonymized raster exists
    H, W = src.shape[:2]
    lines = cached_ocr(work, doc, k, src)
    src = RS.normalize(src)
    figures = RS.figure_regions(cv2.cvtColor(src, cv2.COLOR_RGB2GRAY))
    lines = RS.clean_lines(lines, cv2.cvtColor(src, cv2.COLOR_RGB2GRAY), figures)
    masks = doc.masks(k)
    grid = mask_grid(masks)

    decisions, hidden_boxes, restored_boxes = {}, [], []
    for ln in lines:
        words = [w["text"] for w in ln["words"]]
        cov = [covered(grid, (w["bbox"][0] / W, w["bbox"][1] / H, w["bbox"][2] / W, w["bbox"][3] / H))
               for w in ln["words"]]
        text = " ".join(words)
        snap = canon.snap(text)
        key = snap[0] if snap else P.key(text)
        n = index.get(key, {}).get("n", 0)
        verdict = review.get(key, {}).get("veredicto")
        visible, reason = P.decide_words(words, cov, n, verdict)
        if not snap:  # handwriting read as a word (signatures, filled-in names)
            for i, w in enumerate(ln["words"]):
                if w["conf"] < 80 and LEXICON and P.key(w["text"]) and \
                        any(t not in LEXICON for t in P.key(w["text"]).split()):
                    visible[i] = False
        record["lineas"][reason.split("+")[0]] += 1
        decisions[ln["id"]] = {"visible": visible, "reason": reason, "key": key}
        for w, v, box in zip(ln["words"], visible, RS.text_cover_boxes([ln])):
            if not v:
                hidden_boxes.append(box)
        if reason.startswith("restaurada"):
            restored_boxes.append(ln["bbox"])

    page_corr = corrections.get(str(k + 1), {})
    for lid in page_corr.get("ocultar", []):  # visual review: personal data on these lines
        if lid in decisions:
            decisions[lid]["visible"] = [False] * len(decisions[lid]["visible"])
            hidden_boxes += RS.text_cover_boxes([ln for ln in lines if ln["id"] == lid])
    for ax0, ay0, ax1, ay1 in page_corr.get("ocultar_areas", []):
        box = [int(ax0 * W), int(ay0 * H), int(np.ceil(ax1 * W)), int(np.ceil(ay1 * H))]
        hidden_boxes.append(box)
        for ln in lines:
            vis_flags = decisions[ln["id"]]["visible"]
            for i, w in enumerate(ln["words"]):
                if RS.inside(w["bbox"], [box], 0.3):
                    vis_flags[i] = False

    # what the reader must see: hidden words and non-restored mask areas become paper
    vis = src.copy()
    for x0, y0, x1, y1 in hidden_boxes:
        vis[max(0, y0):y1, max(0, x0):x1] = 255
    for (mx0, my0, mx1, my1), _cat, _kind in masks:
        box = [int(mx0 * W), int(my0 * H), int(np.ceil(mx1 * W)), int(np.ceil(my1 * H))]
        keep = vis[box[1]:box[3], box[0]:box[2]].copy()
        vis[box[1]:box[3], box[0]:box[2]] = 255
        for rx0, ry0, rx1, ry1 in restored_boxes:  # restored text lines stay
            a, b = max(box[0], rx0), max(box[1], ry0)
            c, d = min(box[2], rx1), min(box[3], ry1)
            if a < c and b < d:
                vis[b:d, a:c] = keep[b - box[1]:d - box[1], a - box[0]:c - box[0]]
    good = [w["bbox"] for ln in lines for w in ln["words"] if w["conf"] >= 80]
    photos = RS.photo_regions(cv2.cvtColor(vis, cv2.COLOR_RGB2GRAY), good)
    for x0, y0, x1, y1 in photos:
        vis[y0:y1, x0:x1] = 255
    vis_gray = cv2.cvtColor(vis, cv2.COLOR_RGB2GRAY)

    shown = [ln for ln in lines if any(decisions[ln["id"]]["visible"])]
    covers = RS.text_cover_boxes(lines) + RS.line_cover_boxes(shown)
    touching = [f for f in figures if any(RS.inside(f, [[p[0] - 24, p[1] - 24, p[2] + 24, p[3] + 24]], 0.01) for p in photos)]
    for x0, y0, x1, y1 in touching:  # dark parts of the same photograph
        vis[y0:y1, x0:x1] = 255
    photos += touching
    figures = [f for f in figures if f not in touching]
    text_h = float(np.median([ln["bbox"][3] - ln["bbox"][1] for ln in lines])) if lines else 40.0
    rects, crops, dropped = RS.graphics(vis_gray, vis, covers, figures, RS.line_cover_boxes(shown, 0), text_h)
    dropped += photos
    line_corr = {k_: v for k_, v in page_corr.items() if k_.isdigit()}
    f = 300 / 110  # review images are rendered at 110 dpi
    for box, text in page_corr.get("textos_px", []):
        bx = [v * f for v in box]
        best = max(lines, key=lambda ln: RS.overlap(ln["bbox"], bx), default=None)
        if best is not None and RS.overlap(best["bbox"], bx) > 0.3:
            line_corr[str(best["id"])] = {"texto": text}
            decisions[best["id"]]["visible"] = [True] * len(decisions[best["id"]]["visible"]) \
                if all(decisions[best["id"]]["visible"]) else decisions[best["id"]]["visible"]
    plan = RS.plan_text(lines, vis_gray, canon, decisions, line_corr)

    # quality gate: retypeset only when the text is trustworthy, else the clean image
    trial = pymupdf.open()
    scores = RS.line_scores(RS.to_gray(RS.render(trial, (W, H), rects, crops, plan)), vis_gray, plan)
    good = [ln for ln in plan if ln["source"] in ("plantilla", "revision")
            or (ln["conf"] >= 90 and scores[ln["id"]] >= 0.6)]
    words = [w for ln in lines for w in ln["words"]]
    mean_conf = float(np.mean([w["conf"] for w in words])) if words else 0.0
    share = len(good) / max(1, len(plan))
    mode = "retipeo" if plan and (share >= 0.85 or page_corr.get("modo") == "retipeo") and \
        page_corr.get("modo") != "imagen" else "imagen"
    # reference for the visual review: after the privacy masks, before dropping marks, so a
    # reviewer can tell a removed signature from removed print
    ref = work / "referencia" / doc.name / f"p{k + 1}.png"
    ref.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(vis).convert("L").resize((W // 2, H // 2), Image.Resampling.LANCZOS).save(ref, optimize=True)
    if mode == "retipeo":
        RS.render(out, (W, H), rects, crops, plan)
    else:
        keep = vis.copy()
        for x0, y0, x1, y1 in dropped:  # signatures, fingerprints, photos
            vis[max(0, y0):y1, max(0, x0):x1] = 255
        for x0, y0, x1, y1 in RS.line_cover_boxes(shown, 0):  # never erase visible text
            vis[max(0, y0):y1, max(0, x0):x1] = keep[max(0, y0):y1, max(0, x0):x1]
        RS.render_image(out, vis, plan)
    flagged = [ln["id"] for ln in plan if ln not in good]
    sources = collections.Counter(ln["source"] for ln in plan)
    return {"mode": mode, "lines": len(plan), "sources": dict(sources), "photos_dropped": len(dropped),
            "dropped_boxes": dropped,
            "crops": len(crops), "rects": len(rects), "flagged_lines": flagged if mode == "retipeo" else [],
            "good_share": round(share, 3), "ocr_conf": round(mean_conf, 1),
            "mean_line_score": round(float(np.mean(list(scores.values()))) if scores else 1.0, 3)}


# ------------------------------------------------------------------ line review
def covered_lines(doc, k, index, canon, work):
    """[(key, text, n, prev_text, next_text)] of lines the original anonymization covered."""
    masks = doc.masks(k)
    if not masks:
        return []
    grid = mask_grid(masks)
    if doc.word_source(k) == "native":
        page = pymupdf.open(doc.row["original"])[doc.source_page(k) - 1]
        W, H = page.rect.width, page.rect.height
        items = [(t, [covered(grid, (r.x0 / W, r.y0 / H, r.x1 / W, r.y1 / H)) for _, r in ws])
                 for t, _, ws in V.native_lines(page)]
        keys = [P.key(t) for t, _ in items]
    else:
        path = work / "cache" / doc.name / f"p{k + 1}.ocr.json"
        if not path.exists():
            return []
        W, H = 2480, 3509
        lines = json.loads(path.read_text())
        items, keys = [], []
        for ln in lines:
            t = " ".join(w["text"] for w in ln["words"])
            items.append((t, [covered(grid, (w["bbox"][0] / W, w["bbox"][1] / H, w["bbox"][2] / W, w["bbox"][3] / H))
                              for w in ln["words"]]))
            snap = canon.snap(t)
            keys.append(snap[0] if snap else P.key(t))
    out = []
    for i, ((t, cov), key) in enumerate(zip(items, keys)):
        if not cov or sum(cov) / len(cov) < 0.3 or not P.review_candidate(t):
            continue
        if any(name in P.STRONG for name in P.identifier_hits(t)):
            continue  # strong identifiers are never restored
        shown = index.get(key, {}).get("texto", t)
        out.append((key, shown, index.get(key, {}).get("n", 0),
                    items[i - 1][0] if i else "", items[i + 1][0] if i + 1 < len(items) else ""))
    return out


def cmd_candidatos(args):
    """Distinct covered lines for the line review, in batches for the agents."""
    index = load_index(args.report)
    canon = RS.Canon(index)
    review = load_review(args.report)
    work = work_dir(args.report)
    seen = {}
    for row in load_inventory(args.report):
        if row["tier"] != "A":
            continue
        doc = Doc(row)
        for k in range(row["pages"]):
            for key, text, n, prev, nxt in covered_lines(doc, k, index, canon, work):
                if key in review or key in seen:
                    continue
                seen[key] = {"clave": key, "texto": text, "expedientes": n, "antes": prev, "despues": nxt}
    folder = work / "revision"
    folder.mkdir(exist_ok=True)
    items = sorted(seen.values(), key=lambda e: (-e["expedientes"], e["clave"]))
    size = args.tam
    for b in range(0, len(items), size):
        batch = [{"id": i, **e} for i, e in enumerate(items[b:b + size])]
        (folder / f"lote-{b // size + 1:03d}.json").write_text(json.dumps(batch, ensure_ascii=False, indent=1))
    print(f"{len(items)} lineas distintas para revisar en {-(-len(items) // size)} lotes de {size}")
    print("por frecuencia:", collections.Counter(
        "n>=10" if e["expedientes"] >= 10 else "n3-9" if e["expedientes"] >= 3 else "n1-2" if e["expedientes"] else "ocr/unica"
        for e in items))


def cmd_aplicar_revision(args):
    """Merge agent verdicts (revision/veredicto-NNN.json) into revision_lineas.json."""
    work = work_dir(args.report)
    review = load_review(args.report)
    added = collections.Counter()
    for vpath in sorted((work / "revision").glob("veredicto-*.json")):
        lote = vpath.stem.split("-")[1]
        batch = {e["id"]: e for e in json.loads((work / "revision" / f"lote-{lote}.json").read_text())}
        for v in json.loads(vpath.read_text()):
            entry = batch.get(v["id"])
            if entry is None or v.get("veredicto") not in ("restaurar", "ocultar"):
                continue
            review[entry["clave"]] = {"veredicto": v["veredicto"], "origen": "agente", "lote": lote}
            added[v["veredicto"]] += 1
    review_path(args.report).write_text(json.dumps(review, ensure_ascii=False, indent=0))
    print("veredictos aplicados:", dict(added), "| total en revision_lineas.json:", len(review))


# ------------------------------------------------------------------ visual review
def cmd_exportar_visual(args):
    """Review packages for scanned pages: output image, anonymized reference, current lines."""
    work = work_dir(args.report)
    rows = {r["name"]: r for r in load_inventory(args.report)}
    pages = []
    for qa in sorted((work / "qa").glob("*.json")):
        rec = json.loads(qa.read_text())
        for k, info in enumerate(rec["pages"]):
            if info["mode"] in ("retipeo", "imagen") and rec["name"] in rows:
                pages.append((rec["name"], k, info))
    folder = work / "visual"
    folder.mkdir(exist_ok=True)
    size = args.tam
    for b in range(0, len(pages), size):
        lote = folder / f"lote-{b // size + 1:03d}"
        lote.mkdir(exist_ok=True)
        manifest = []
        for name, k, info in pages[b:b + size]:
            row = rows[name]
            out_pdf = out_dir(args.report) / row["sub"] / name
            page = pymupdf.open(out_pdf)[k]
            stem = f"{name[:-4]}_p{k + 1}"
            pix = page.get_pixmap(dpi=110)
            pix.save(lote / f"{stem}_salida.png")
            ref = work / "referencia" / name / f"p{k + 1}.png"
            if ref.exists():
                Image.open(ref).save(lote / f"{stem}_referencia.png")
            W, H = page.rect.width, page.rect.height
            lines = []
            for blk in page.get_text("dict")["blocks"]:
                for ln in blk.get("lines", []):
                    t = "".join(sp["text"] for sp in ln["spans"]).strip()
                    if t:
                        x0, y0, x1, y1 = ln["bbox"]
                        lines.append({"texto": t, "px": [round(x0 / W * pix.width), round(y0 / H * pix.height),
                                                         round(x1 / W * pix.width), round(y1 / H * pix.height)]})
            manifest.append({"doc": name, "pagina": k + 1, "modo": info["mode"], "salida": f"{stem}_salida.png",
                             "referencia": f"{stem}_referencia.png" if ref.exists() else None,
                             "tam_px": [pix.width, pix.height], "lineas": lines})
        (lote / "lote.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(f"{len(pages)} paginas escaneadas en {-(-len(pages) // size)} lotes de {size}:",
          dict(collections.Counter(i["mode"] for _, _, i in pages)))


def cmd_hojas(args):
    """Review sheets for the agents (work/hojas/):
    privacidad-NNN.png  6 scanned output pages per sheet, tiles labeled T<id> -> privacy check
    lineas-NNN.png      doubtful retypeset lines: reference crop over output crop, labeled L<id>
    with hojas.json mapping every label back to (doc, page[, line box])."""
    from PIL import ImageDraw

    work = work_dir(args.report)
    rows = {r["name"]: r for r in load_inventory(args.report)}
    folder = work / "hojas"
    folder.mkdir(exist_ok=True)
    tiles, line_items = [], []
    for qa in sorted((work / "qa").glob("*.json")):
        rec = json.loads(qa.read_text())
        if rec["name"] not in rows:
            continue
        for k, info in enumerate(rec["pages"]):
            if info["mode"] in ("retipeo", "imagen"):
                tiles.append((rec["name"], k))
            if info["mode"] == "retipeo":
                for lid in info.get("flagged_lines", []):
                    line_items.append((rec["name"], k, lid))
    mapping = {"tiles": {}, "lineas": {}}
    tw, th = 620, 877
    for b in range(0, len(tiles), 6):
        sheet = Image.new("RGB", (3 * tw + 40, 2 * th + 30), "white")
        d = ImageDraw.Draw(sheet)
        for j, (name, k) in enumerate(tiles[b:b + 6]):
            row = rows[name]
            page = pymupdf.open(out_dir(args.report) / row["sub"] / name)[k]
            pix = page.get_pixmap(dpi=80)
            im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            im.thumbnail((tw - 10, th - 30))
            X, Y = 10 + (j % 3) * (tw + 10), 10 + (j // 3) * (th + 10)
            tid = f"T{b + j:04d}"
            sheet.paste(im, (X, Y + 22))
            d.rectangle([X, Y + 22, X + im.width, Y + 22 + im.height], outline="blue", width=2)
            d.text((X + 2, Y + 2), tid, fill="red")
            mapping["tiles"][tid] = {"doc": name, "pagina": k + 1, "tam_px": [im.width, im.height]}
        sheet.save(folder / f"privacidad-{b // 6 + 1:03d}.png", optimize=True)
    # doubtful lines: needs the OCR cache and the saved reference of each page
    per = 14
    for b in range(0, len(line_items), per):
        pairs = []
        for name, k, lid in line_items[b:b + per]:
            lines = json.loads((work / "cache" / name / f"p{k + 1}.ocr.json").read_text())
            ln = next((l for l in lines if l["id"] == lid), None)
            ref = work / "referencia" / name / f"p{k + 1}.png"
            if ln is None or not ref.exists():
                continue
            x0, y0, x1, y1 = ln["bbox"]
            pad = 12
            box = (max(0, x0 - pad), max(0, y0 - pad), x1 + pad, y1 + pad)
            refim = Image.open(ref).convert("RGB")
            rc = refim.crop(tuple(v // 2 for v in box))  # references are saved at half resolution
            page = pymupdf.open(out_dir(args.report) / rows[name]["sub"] / name)[k]
            clip = pymupdf.Rect(*(v * 72 / 300 for v in box))
            pix = page.get_pixmap(dpi=150, clip=clip)
            oc = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            lab = f"L{b + len(pairs):05d}"
            mapping["lineas"][lab] = {"doc": name, "pagina": k + 1, "linea": lid}
            pairs.append((lab, rc, oc))
        if not pairs:
            continue
        width = min(1800, max(max(p[1].width, p[2].width) for p in pairs) + 20)
        height = sum(p[1].height + p[2].height + 40 for p in pairs) + 10
        sheet = Image.new("RGB", (width, height), "white")
        d = ImageDraw.Draw(sheet)
        y = 5
        for lab, rc, oc in pairs:
            d.text((5, y), f"{lab}  referencia (arriba) / salida (abajo)", fill="red")
            sheet.paste(rc, (10, y + 14))
            sheet.paste(oc, (10, y + 16 + rc.height))
            y += rc.height + oc.height + 40
            d.line([0, y - 8, width, y - 8], fill="gray")
        sheet.save(folder / f"lineas-{b // per + 1:03d}.png", optimize=True)
    (folder / "hojas.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=1))
    print(f"{len(tiles)} paginas en {-(-len(tiles) // 6)} hojas de privacidad; "
          f"{len(mapping['lineas'])} lineas dudosas en {-(-len(line_items) // per)} hojas")


def cmd_aplicar_visual(args):
    """Merge the sheet reviews into correcciones.json:
    hojas/privacidad-NNN.json  [{"tile": "T0001", "privacidad_ok": false,
                                 "problemas": [{"descripcion": "...", "caja_px": [x0, y0, x1, y1]}]}]
    hojas/lineas-NNN.json      [{"linea": "L00001", "texto": "...", "ilegible": false}]"""
    work = work_dir(args.report)
    folder = work / "hojas"
    mapping = json.loads((folder / "hojas.json").read_text())
    cpath = work / "correcciones.json"
    corrections = json.loads(cpath.read_text()) if cpath.exists() else {}
    stats = collections.Counter()
    for res in sorted(folder.glob("privacidad-*.json")):
        for item in json.loads(res.read_text()):
            t = mapping["tiles"].get(item.get("tile"))
            if t is None:
                continue
            stats["paginas_revisadas"] += 1
            if item.get("privacidad_ok", True):
                continue
            stats["paginas_con_problemas"] += 1
            pc = corrections.setdefault(t["doc"], {}).setdefault(str(t["pagina"]), {})
            W, H = t["tam_px"]
            for prob in item.get("problemas", []):
                x0, y0, x1, y1 = prob["caja_px"]
                area = [max(0, x0 / W), max(0, y0 / H), min(1, x1 / W), min(1, y1 / H)]
                if area not in pc.setdefault("ocultar_areas", []):
                    pc["ocultar_areas"].append(area)
                    stats["areas_ocultas"] += 1
    for res in sorted(folder.glob("lineas-*.json")):
        for item in json.loads(res.read_text()):
            m = mapping["lineas"].get(item.get("linea"))
            if m is None:
                continue
            pc = corrections.setdefault(m["doc"], {}).setdefault(str(m["pagina"]), {})
            if item.get("ilegible"):
                pc["modo"] = "imagen"
                stats["paginas_a_imagen"] += 1
            elif item.get("texto"):
                pc[str(m["linea"])] = {"texto": item["texto"]}
                stats["lineas_corregidas"] += 1
    cpath.write_text(json.dumps(corrections, ensure_ascii=False, indent=1))
    print(dict(stats))


def cmd_resumen(args):
    """resumen.md next to the rebuilt PDFs: counts only, no personal data."""
    work = work_dir(args.report)
    rows = load_inventory(args.report)
    state = {}
    for line in (work / "estado.jsonl").read_text().splitlines():
        d = json.loads(line)
        state[d["name"]] = d
    modes, by_sub, lines = collections.Counter(), collections.Counter(), collections.Counter()
    fails = [r["name"] for r in rows if state.get(r["name"], {}).get("status") != "ok"]
    for r in rows:
        q = work / "qa" / f"{r['name']}.json"
        if not q.exists():
            continue
        rec = json.loads(q.read_text())
        by_sub[r["sub"]] += 1
        for k, v in rec["lineas"].items():
            lines[k] += v
        for p in rec["pages"]:
            modes[p["mode"]] += 1
    review = load_review(args.report)
    corrections = json.loads((work / "correcciones.json").read_text()) if (work / "correcciones.json").exists() else {}
    hidden_areas = sum(len(pc.get("ocultar_areas", [])) for d in corrections.values() for pc in d.values())
    md = [f"# Recreación de {args.report}", "",
          f"Documentos: {len(rows)} ({', '.join(f'{k}: {v}' for k, v in sorted(by_sub.items()))}). "
          f"Fallidos: {len(fails)}.", "",
          "## Páginas por modo", "",
          "| Modo | Páginas | Qué es |", "|---|---|---|",
          f"| vector | {modes['vector']} | página original del expediente; datos personales eliminados con redacción real |",
          f"| retipeo | {modes['retipeo']} | escaneo legible reconstruido como texto (OCR verificado contra plantillas y revisión) |",
          f"| imagen | {modes['imagen']} | escaneo de baja calidad: la imagen sin datos personales + capa de texto invisible |",
          "", "## Líneas", "",
          f"- visibles como en el original: {lines['visible']}",
          f"- recuperadas (tapadas antes por error): {lines['restaurada']}",
          f"- siguen tapadas (dato personal o duda): {lines['tapada']}",
          f"- sello del tribunal eliminado: {lines['sello']}",
          f"- paginación visible: {lines['paginacion']}",
          f"- líneas revisadas por agentes: {sum(1 for v in review.values() if v.get('origen') == 'agente')} "
          f"({sum(1 for v in review.values() if v.get('origen') == 'agente' and v['veredicto'] == 'restaurar')} recuperables)",
          f"- áreas ocultadas en la revisión visual: {hidden_areas}",
          "", "Los datos personales aparecen como espacio en blanco. Solo «Página X de Y» queda visible del pie;",
          "sello del tribunal, nº de propuesta y datos del originador/operador se ocultan.", ""]
    if fails:
        md += ["## Fallidos", ""] + [f"- {n}" for n in fails]
    (out_dir(args.report) / "resumen.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


_STATE = {}


def _init_worker(report, provisional):
    """Per-process state (the default start method does not share globals)."""
    index = load_index(report)
    IMAGE_INDEX.update(load_image_index(report))
    review = load_review(report)
    if provisional:
        for key, entry in index.items():
            if key not in review and P.restorable(entry.get("texto", key), entry["n"])[0]:
                review[key] = {"veredicto": "restaurar", "origen": "provisional"}
    cpath = work_dir(report) / "correcciones.json"
    lexicon = {w for k, e in index.items() if e["n"] >= P.MIN_EXPEDIENTES for w in k.split()}
    LEXICON.update(lexicon)
    _STATE.update(report=report, index=index, review=review, canon=RS.Canon(index),
                  corrections=json.loads(cpath.read_text()) if cpath.exists() else {})


def _process_row(row):
    t0 = time.time()
    work = work_dir(_STATE["report"])
    target = out_dir(_STATE["report"]) / row["sub"] / row["name"]
    try:
        record = process(Doc(row), _STATE["index"], _STATE["review"], target, work,
                         _STATE["canon"], _STATE["corrections"])
        (work / "qa").mkdir(exist_ok=True)
        (work / "qa" / f"{row['name']}.json").write_text(
            json.dumps(record, indent=1, ensure_ascii=False, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
        return {"name": row["name"], "status": "ok", "seconds": round(time.time() - t0, 1)}
    except Exception as exc:  # one document never stops the batch
        return {"name": row["name"], "status": "fallo", "seconds": round(time.time() - t0, 1),
                "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-1500:]}


def cmd_procesar(args):
    from concurrent.futures import ProcessPoolExecutor

    rows = load_inventory(args.report)
    if args.solo:
        rows = [r for r in rows if any(r["name"].startswith(s) for s in args.solo)]
    if args.limite:
        rows = rows[: args.limite]
    work = work_dir(args.report)
    state_path = work / "estado.jsonl"
    done = set()
    if state_path.exists() and not args.rehacer:
        for line in state_path.read_text().splitlines():
            s = json.loads(line)
            if s["status"] == "ok":
                done.add(s["name"])
    todo = [r for r in rows if r["name"] not in done]
    print(f"{len(todo)} documentos pendientes de {len(rows)}; ventana de {args.lote}, {args.workers} procesos",
          flush=True)
    from concurrent.futures import as_completed

    window, n_done = [], 0
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init_worker,
                             initargs=(args.report, args.revision_provisional)) as pool:
        futures = [pool.submit(_process_row, row) for row in todo]
        for fut in as_completed(futures):
            res = fut.result()
            n_done += 1
            with state_path.open("a") as fh:
                fh.write(json.dumps(res, ensure_ascii=False) + "\n")
            print(f"  {n_done}/{len(todo)} [{res['status']}] {res['name']} ({res['seconds']}s)" +
                  (f" {res['error']}" if res["status"] != "ok" else ""), flush=True)
            window.append(res["status"] != "ok")
            if len(window) == args.lote:  # brake: too many failures in the last window
                if sum(window) / len(window) > 0.2:
                    for f in futures:
                        f.cancel()
                    sys.exit(f"freno: {sum(window)} fallos en los ultimos {len(window)}; revisar estado.jsonl")
                window = []


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("inventario", "indice", "candidatos", "aplicar-revision", "exportar-visual", "hojas", "aplicar-visual",
                 "procesar", "resumen"):
        p = sub.add_parser(name)
        p.add_argument("--report", default="report5")
        if name == "candidatos":
            p.add_argument("--tam", type=int, default=300, help="lines per review batch")
        if name == "exportar-visual":
            p.add_argument("--tam", type=int, default=8, help="pages per visual review batch")
        if name == "procesar":
            p.add_argument("--solo", nargs="*")
            p.add_argument("--lote", type=int, default=10)
            p.add_argument("--limite", type=int)
            p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 3))
            p.add_argument("--rehacer", action="store_true")
            p.add_argument("--revision-provisional", action="store_true",
                           help="approve deterministic boilerplate until the line review exists")
    args = parser.parse_args()
    globals()[f"cmd_{args.cmd.replace('-', '_')}"](args)


if __name__ == "__main__":
    main()
