"""Scanned pages: OCR + retypeset with geometry measured on the 300 dpi raster.

The page is rebuilt from three layers measured on the "visible" raster (what the reader must
see after privacy decisions): light/dark line art and fills as vector rectangles, the
remaining non-text marks (logos, card art, checkboxes) as image crops (photos are dropped),
and the text as real text. Text comes from Tesseract (hOCR); a line that matches a native
line of the same template (indice_lineas.json) takes that exact text and bold runs, so OCR
confusions such as "(1)" for "(i)" never reach the output. Corrections from the visual
review (correcciones.json) override both.
"""

from __future__ import annotations

import difflib
import io
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageFont

import recrear_politica as P

PX = 72 / 300  # pt per px at 300 dpi
LIB = "/usr/share/fonts/truetype/liberation/"
FONT_FILES = {
    "R": LIB + "LiberationSans-Regular.ttf", "B": LIB + "LiberationSans-Bold.ttf",
    "SR": LIB + "LiberationSerif-Regular.ttf", "SB": LIB + "LiberationSerif-Bold.ttf",
}
FONT_NAMES = {"R": "LSR", "B": "LSB", "SR": "LSSR", "SB": "LSSB"}
_PIL = {k: ImageFont.truetype(f, 1000) for k, f in FONT_FILES.items()}
_FITZ = {k: pymupdf.Font(fontfile=f) for k, f in FONT_FILES.items()}


def face(style, serif):
    return ("S" if serif else "") + ("B" if style == "B" else "R")


_ink_cache = {}


def ink(text, f):
    """(ink width, left bearing) in em for text in font f."""
    k = (text, f)
    if k not in _ink_cache:
        b = _PIL[f].getbbox(text)
        _ink_cache[k] = ((b[2] - b[0]) / 1000, b[0] / 1000) if b else (0.0, 0.0)
    return _ink_cache[k]


# ------------------------------------------------------------------ OCR
def tesseract():
    local = Path.home() / ".local/opt/datajud-tesseract"
    env = dict(os.environ, OMP_THREAD_LIMIT="1", LD_LIBRARY_PATH=str(local / "usr/lib/x86_64-linux-gnu"))
    return str(local / "usr/bin/tesseract"), env, str(Path.home() / ".local/share/datajud-scraper/tessdata/best")


def _title(el):
    out = {}
    for part in (el.get("title") or "").split(";"):
        bits = part.strip().split()
        if bits:
            out[bits[0]] = bits[1:]
    return out


def ocr_lines(img):
    """Tesseract hOCR -> [{id, par, bbox, baseline, words:[{bbox, text, conf}]}] in px."""
    binary, env, tessdata = tesseract()
    buf = io.BytesIO()
    img.convert("L").save(buf, format="PNG", compress_level=1)
    res = subprocess.run(
        [binary, "stdin", "stdout", "--tessdata-dir", tessdata, "-l", "por", "--psm", "3", "--dpi", "300",
         "-c", "tessedit_create_hocr=1", "-c", "hocr_char_boxes=0"],
        input=buf.getvalue(), capture_output=True, env=env, timeout=300, check=False,
    )
    if res.returncode:
        raise RuntimeError("tesseract fallo: " + res.stderr.decode()[-300:])
    root = ET.fromstring(res.stdout.decode("utf8"))
    lines, par_id = [], 0
    for par in root.iter():
        if not par.get("class", "").startswith("ocr_par"):
            continue
        par_id += 1
        for ln in par:
            cls = ln.get("class", "")
            if cls not in ("ocr_line", "ocr_header", "ocr_caption", "ocr_textfloat"):
                continue
            t = _title(ln)
            words = []
            for w in ln:
                if w.get("class") != "ocrx_word":
                    continue
                text = "".join(w.itertext()).strip()
                if not text:
                    continue
                wt = _title(w)
                words.append({"bbox": [int(v) for v in wt["bbox"]], "text": text, "conf": int(wt.get("x_wconf", ["0"])[0])})
            if words:
                bl = [float(v) for v in t.get("baseline", ["0", "0"])]
                lines.append({"id": len(lines), "par": par_id, "bbox": [int(v) for v in t["bbox"]],
                              "baseline": bl, "words": words})
    return lines


# ------------------------------------------------------------------ template lines
class Canon:
    """Fuzzy lookup of OCR lines in the native line index (trigram candidates + difflib)."""

    def __init__(self, index):
        self.entries = {k: v for k, v in index.items() if "texto" in v and len(k) >= 12}
        self.grams = defaultdict(set)
        for k in self.entries:
            for g in self._grams(k):
                self.grams[g].add(k)

    @staticmethod
    def _grams(k):
        return {k[i:i + 3] for i in range(len(k) - 2)}

    def snap(self, text):
        k = P.key(text)
        if len(k) < 12:
            return None
        if k in self.entries:
            return k, self.entries[k], 1.0
        votes = defaultdict(int)
        for g in self._grams(k):
            for cand in self.grams.get(g, ()):
                votes[cand] += 1
        best = None
        for cand, _ in sorted(votes.items(), key=lambda kv: -kv[1])[:8]:
            if abs(len(cand) - len(k)) > max(3, 0.06 * len(k)):
                continue
            r = difflib.SequenceMatcher(None, k, cand, autojunk=False).ratio()
            if r >= 0.93 and (best is None or r > best[2]):
                best = (cand, self.entries[cand], r)
        if best and re.sub(r"\D", "", best[0]) != re.sub(r"\D", "", k):
            # a digit differs: accept only OCR-typical splits of the same digits ("134" vs "13 4")
            if sorted(re.sub(r"\D", "", best[0])) != sorted(re.sub(r"\D", "", k)):
                return None
        return best


# ------------------------------------------------------------------ measuring
def stroke_widths(gray, words):
    """Median stroke width (px) of each word: 2x mean ridge of the distance transform."""
    out = []
    for w in words:
        x0, y0, x1, y1 = w["bbox"]
        sub = gray[y0:y1, x0:x1]
        if sub.size == 0:
            out.append(0.0)
            continue
        bw = (sub < 150).astype(np.uint8)
        if bw.sum() < 10:
            out.append(0.0)
            continue
        dt = cv2.distanceTransform(bw, cv2.DIST_L2, 3)
        ridge = (dt >= cv2.dilate(dt, np.ones((3, 3), np.uint8))) & (bw > 0)
        out.append(float(2 * dt[ridge].mean()) if ridge.any() else 0.0)
    return out


def ink_gray(gray, bbox):
    x0, y0, x1, y1 = bbox
    sub = gray[y0:y1, x0:x1]
    core = sub[sub < 170]
    return float(np.percentile(core, 8)) if core.size else 40.0


# ------------------------------------------------------------------ preprocessing
def normalize(rgb):
    """Flat-field: paper becomes white on scans with shading; dark artwork is untouched."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    bg = cv2.medianBlur(cv2.morphologyEx(gray, cv2.MORPH_CLOSE, np.ones((31, 31), np.uint8)), 31)
    factor = np.where(bg > 150, 255.0 / np.maximum(bg, 1), 1.0)
    return np.clip(rgb * factor[..., None], 0, 255).astype(np.uint8)


def figure_regions(gray):
    """Artwork drawn on a dark background (card images, banners): large solid dark areas."""
    h, w = gray.shape
    dark = (gray < 90).astype(np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((25, 25), np.uint8))
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(dark, 8)
    out = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area > 0.003 * h * w and area > 0.5 * bw * bh:
            out.append([int(x), int(y), int(x + bw), int(y + bh)])
    return out


def overlap(a, b):
    """Intersection over the smaller of two [x0, y0, x1, y1] boxes."""
    iw, ih = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return iw * ih / max(1, small)


def inside(box, regions, frac=0.5):
    x0, y0, x1, y1 = box
    a = max(1, (x1 - x0) * (y1 - y0))
    for rx0, ry0, rx1, ry1 in regions:
        iw, ih = min(x1, rx1) - max(x0, rx0), min(y1, ry1) - max(y0, ry0)
        if iw > 0 and ih > 0 and iw * ih / a >= frac:
            return True
    return False


def clean_lines(lines, gray, figures=()):
    """Drop OCR 'words' that are artwork: low confidence, light text on dark, inside figures."""
    out = []
    H, W = gray.shape
    for ln in lines:
        words = []
        line_h = ln["bbox"][3] - ln["bbox"][1]
        for j, w in enumerate(ln["words"]):
            x0, y0, x1, y1 = w["bbox"]
            # background = ring around the word (inside a small word the ink dominates)
            m = 6
            ring = gray[max(0, y0 - m):min(H, y1 + m), max(0, x0 - m):min(W, x1 + m)].copy()
            ring[min(m, y0):min(m, y0) + (y1 - y0), min(m, x0):min(m, x0) + (x1 - x0)] = 255
            dark_bg = ring.size and float(np.mean(ring < 110)) > 0.5
            gaps = []
            if j > 0:
                gaps.append(x0 - ln["words"][j - 1]["bbox"][2])
            if j + 1 < len(ln["words"]):
                gaps.append(ln["words"][j + 1]["bbox"][0] - x1)
            isolated = not gaps or min(gaps) > 3 * line_h
            if dark_bg or inside(w["bbox"], figures) or (w["conf"] < 50 and isolated):
                continue
            words.append(w)
        if words:
            out.append({**ln, "words": words})
    return out


def photo_regions(gray, text_boxes, block=48):
    """Photographs (selfies, ID photos): large areas dense in mid-tones without text."""
    h, w = gray.shape
    mid = ((gray > 50) & (gray < 205)).astype(np.float32)
    dens = cv2.resize(mid, (w // block, h // block), interpolation=cv2.INTER_AREA)
    cand = (dens > 0.35).astype(np.uint8)
    for x0, y0, x1, y1 in text_boxes:
        cand[y0 // block:y1 // block + 1, x0 // block:x1 // block + 1] = 0
    cand = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(cand, 8)
    out = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area * block * block > 0.004 * h * w:
            out.append([max(0, (x - 1) * block), max(0, (y - 1) * block),
                        min(w, (x + bw + 1) * block), min(h, (y + bh + 1) * block)])
    return out


def text_cover_boxes(lines):
    """Boxes that hide each OCR word up to its line height (accents, descenders)."""
    out = []
    for ln in lines:
        ly0, ly1 = ln["bbox"][1], ln["bbox"][3]
        for w in ln["words"]:
            out.append([w["bbox"][0] - 4, min(ly0, w["bbox"][1]) - 4, w["bbox"][2] + 4, max(ly1, w["bbox"][3]) + 4])
    return out


def line_cover_boxes(lines, height_margin=4):
    """The full extent of each typeset line: fragments the OCR did not box are text too."""
    out = []
    for ln in lines:
        xs = [w["bbox"][0] for w in ln["words"]] + [w["bbox"][2] for w in ln["words"]]
        out.append([min(xs) - 4, ln["bbox"][1] - height_margin, max(xs) + 4, ln["bbox"][3] + height_margin])
    return out


# ------------------------------------------------------------------ graphics
def _runs(row):
    d = np.diff(np.concatenate([[0], row.astype(np.int8), [0]]))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def decompose(mask, tol=3):
    rects, open_ = [], []
    for y in range(mask.shape[0]):
        new_open = []
        for x0, x1 in _runs(mask[y]):
            hit = next((r for r in open_ if abs(r[0] - x0) <= tol and abs(r[1] - x1) <= tol), None)
            if hit is not None:
                open_.remove(hit)
                hit[3] = y + 1
                new_open.append(hit)
            else:
                new_open.append([x0, x1, y, y + 1])
        rects.extend(open_)
        open_ = new_open
    return rects + open_


def graphics(vis_gray, vis_rgb, text_boxes, figures=(), line_boxes=(), text_h=40):
    """Vector rects (line art + fills) and image crops for what is not text."""
    H, W = vis_gray.shape
    g = vis_gray.copy()
    for x0, y0, x1, y1 in text_boxes:
        g[max(0, y0):y1, max(0, x0):x1] = 255
    crops, dropped = [], []
    for x0, y0, x1, y1 in figures:  # artwork kept whole, as in the source
        crops.append(([x0, y0, x1, y1], Image.fromarray(vis_rgb[y0:y1, x0:x1])))
        g[y0:y1, x0:x1] = 255
    lineart_src = vis_gray.copy()
    for x0, y0, x1, y1 in figures:
        lineart_src[y0:y1, x0:x1] = 255
    rects = []
    # fills: large uniform light-gray areas (text holes closed, never grown into paper)
    light = ((g >= 200) & (g <= 250)).astype(np.uint8)
    light = cv2.morphologyEx(light, cv2.MORPH_OPEN, np.ones((15, 15), np.uint8))
    light = cv2.morphologyEx(light, cv2.MORPH_CLOSE, np.ones((41, 41), np.uint8)) & (vis_gray < 252)
    light = cv2.morphologyEx(light.astype(np.uint8), cv2.MORPH_OPEN, np.ones((9, 9), np.uint8)).astype(bool)
    for x0, x1, y0, y1 in decompose(light):
        if (x1 - x0) * (y1 - y0) >= 400:
            rects.append(("fill", x0, y0, x1, y1, float(np.median(vis_gray[y0:y1, x0:x1]))))
    # line art: long straight runs of any ink (text strokes are never 40 px straight)
    inkm = (lineart_src < 235).astype(np.uint8)
    lines = cv2.morphologyEx(inkm, cv2.MORPH_OPEN, np.ones((1, 40), np.uint8)) | \
        cv2.morphologyEx(inkm, cv2.MORPH_OPEN, np.ones((40, 1), np.uint8))
    n_l, lab_l, st_l, _ = cv2.connectedComponentsWithStats(lines.astype(np.uint8), 8)
    for i in range(1, n_l):  # strokes of (bold, scanned) text that look straight
        x, y, bw, bh, _ = st_l[i]
        if inside([x, y, x + bw, y + bh], line_boxes, 0.9):
            lines[lab_l == i] = 0
    for x0, x1, y0, y1 in decompose(lines.astype(bool)):
        if (x1 - x0) * (y1 - y0) >= 60:
            rects.append(("line", x0, y0, x1, y1, float(np.median(lineart_src[y0:y1, x0:x1]))))
    # residual marks: logos in the header, small marks and text-like blocks are kept; the rest
    # (signatures, initials, fingerprints, stamps) is personal and dropped
    rest = (g < 200).astype(np.uint8) & (1 - cv2.dilate(lines.astype(np.uint8), np.ones((5, 5), np.uint8)))
    rest = cv2.morphologyEx(rest, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    grouped = cv2.dilate(rest, np.ones((25, 25), np.uint8))
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(grouped, 8)
    for i in range(1, n):
        x, y, w, h, _area = stats[i]
        x0, y0, x1, y1 = max(0, x + 10), max(0, y + 10), min(W, x + w - 10), min(H, y + h - 10)
        if x1 <= x0 or y1 <= y0:
            continue
        sub = g[y0:y1, x0:x1]
        inked = sub < 245
        if inked.sum() < 150:  # specks
            continue
        kind = mark_kind(sub, (x0, y0, x1, y1), (W, H), text_h)
        if kind == "drop":
            dropped.append([int(x0), int(y0), int(x1), int(y1)])
            continue
        crop = vis_rgb[y0:y1, x0:x1].copy()
        crop[sub >= 245] = 255  # only this mark: text and rects are separate layers
        crops.append(([int(x0), int(y0), int(x1), int(y1)], Image.fromarray(crop)))
    return rects, crops, dropped


def mark_kind(sub, box, size, text_h=40):
    """drop only with evidence of a personal mark, keep otherwise (content must not vanish):
    - handwriting/signature: strokes much taller than the printed lines and irregular;
    - fingerprint: mid-tone texture without printed strokes, not a solid bar."""
    x0, y0, x1, y1 = box
    W, H = size
    area = (x1 - x0) * (y1 - y0)
    inked = sub < 245
    if y1 < 0.13 * H and area < 0.03 * W * H:
        return "keep"  # letterhead logo
    fill = inked.mean()
    mid = ((sub > 110) & (sub < 235)).sum() / max(1, inked.sum())
    n, _l, st, _ = cv2.connectedComponentsWithStats((sub < 160).astype(np.uint8), 8)
    heights = np.array([st[i][3] for i in range(1, n) if st[i][4] >= 8])
    if mid > 0.55 and len(heights) < 5 and 0.3 < fill < 0.85 and area > 0.002 * W * H:
        return "drop"  # fingerprint
    if len(heights) >= 1:
        tall = heights.max() / max(1, text_h)
        spread = (np.percentile(heights, 75) - np.percentile(heights, 25)) / max(1, np.median(heights)) \
            if len(heights) >= 3 else 1.0
        if tall >= 1.8 and spread >= 0.8:
            return "drop"  # signature / handwriting
    return "keep"


# ------------------------------------------------------------------ typesetting
def word_size(text, width_pt, style, serif):
    em, _ = ink(text, face(style, serif))
    return width_pt / em if em > 0 else None


def segments(words, line_h):
    """Split a line where the gap is much larger than a space (label | value, columns)."""
    out, cur = [], [0]
    for i in range(1, len(words)):
        gap = words[i]["bbox"][0] - words[i - 1]["bbox"][2]
        if gap > 2.5 * line_h:
            out.append(cur)
            cur = []
        cur.append(i)
    out.append(cur)
    return out


def baseline(line):
    x0, y0, x1, y1 = line["bbox"]
    m, c = line["baseline"]
    return (y1 + c + m * (x1 - x0) / 2) * PX


def underline(gray, line):
    x0, y0, x1, y1 = line["bbox"]
    b = int(baseline(line) / PX)
    band = gray[b + 1:b + 8, x0:x1] < 150
    return bool(band.size and band.any(axis=0).mean() > 0.85)


def plan_text(lines, gray, canon, decisions, corrections):
    """Typeset plan for the visible words of each line (sizes per segment, x-scale per word)."""
    per_line, strokes_all = [], []
    for ln in lines:
        sw = stroke_widths(gray, ln["words"])
        per_line.append(sw)
        strokes_all += [s for s, w in zip(sw, ln["words"]) if s > 0 and len(w["text"]) >= 3]
    regular = float(np.percentile(strokes_all, 35)) if strokes_all else 0
    snaps, votes = {}, []
    for ln in lines:
        snap = canon.snap(" ".join(w["text"] for w in ln["words"])) if canon else None
        snaps[ln["id"]] = snap
        if snap:
            votes.append(snap[1].get("serif", False))
    serif = bool(votes) and sum(votes) > len(votes) / 2

    plan = []
    for ln, sw in zip(lines, per_line):
        visible = list(decisions[ln["id"]]["visible"])
        if not any(visible):
            continue
        words = ln["words"]
        styles = ["B" if regular and s > 1.3 * regular else "R" for s in sw]
        texts = [w["text"] for w in words]
        snap, corr, source = snaps[ln["id"]], corrections.get(str(ln["id"])), "ocr"
        new = None
        if corr is not None:
            source, new = "revision", [(t, None) for t in corr["texto"].split()]
        elif snap and all(visible):
            source, new = "plantilla", [(w, st) for t, st in snap[1]["runs"] for w in t.split()]
        if new:
            if len(new) != len(words):
                words = _respace(ln, [t for t, _ in new], styles)
                styles = [styles[0]] * len(new)
                visible = [all(visible)] * len(new)
            texts = [t for t, _ in new]
            styles = [st or s0 for (_, st), s0 in zip(new, styles)]
        line_h = ln["bbox"][3] - ln["bbox"][1]
        est_all = {}
        for i in range(len(words)):
            if len(texts[i]) >= 3:
                e = word_size(texts[i], (words[i]["bbox"][2] - words[i]["bbox"][0]) * PX, styles[i], serif)
                if e:
                    est_all[i] = e
        line_size = float(np.median(list(est_all.values()))) if est_all else line_h * PX / 1.15
        items = []
        for seg in segments(words, line_h):
            seg_est = [est_all[i] for i in seg if i in est_all]
            size = line_size
            if len(seg_est) >= 2 and abs(np.median(seg_est) - line_size) / line_size > 0.2:
                size = float(np.median(seg_est))  # e.g. a small gray label next to a large value
            for i in seg:
                if visible[i]:
                    items.append({"text": texts[i], "x": words[i]["bbox"][0] * PX,
                                  "w": (words[i]["bbox"][2] - words[i]["bbox"][0]) * PX,
                                  "style": styles[i], "size": size})
        plan.append({"id": ln["id"], "par": ln["par"], "source": source,
                     "conf": min(w.get("conf", 100) for w in ln["words"]),
                     "base": baseline(ln), "serif": serif, "underline": underline(gray, ln),
                     "color": ink_gray(gray, ln["bbox"]), "words": items, "bbox": ln["bbox"]})
    # one size per paragraph when the measurements agree (OCR boxes jitter, fonts do not)
    by_par = defaultdict(list)
    for p_ in plan:
        by_par[p_["par"]] += p_["words"]
    for items in by_par.values():
        if items:
            med = float(np.median([i["size"] for i in items]))
            for i in items:
                if abs(i["size"] - med) / med < 0.15:
                    i["size"] = med
    for p_ in plan:  # horizontal scale so each word fills its measured box exactly
        for i in p_["words"]:
            em, _ = ink(i["text"], face(i["style"], p_["serif"]))
            natural = em * i["size"]
            sx = i["w"] / natural if natural > 0 else 1.0
            i["sx"] = float(np.clip(sx, 0.93, 1.07))
    return plan


def _respace(line, texts, styles):
    """Word boxes for a different word list on the same line: spread over the OCR extent."""
    x0, x1 = line["words"][0]["bbox"][0], line["words"][-1]["bbox"][2]
    y0, y1 = line["bbox"][1], line["bbox"][3]
    lens = [len(t) for t in texts]
    total = sum(lens) + len(texts) - 1
    out, cur = [], x0
    for t in texts:
        w = (x1 - x0) * len(t) / total
        out.append({"bbox": [int(cur), y0, int(cur + w), y1], "text": t, "conf": 0})
        cur += w + (x1 - x0) / total
    return out


def _new_page(out_doc, size_px):
    page = out_doc.new_page(width=size_px[0] * PX, height=size_px[1] * PX)
    for k, f in FONT_FILES.items():
        page.insert_font(fontname=FONT_NAMES[k], fontfile=f)
    return page


def _text(shape, plan, render_mode=0):
    for ln in plan:
        col = (ln["color"] / 255,) * 3
        for i, w in enumerate(ln["words"]):
            f = face(w["style"], ln["serif"])
            em, lb = ink(w["text"], f)
            x = w["x"] - lb * w["size"] * w["sx"]
            t = w["text"] + (" " if i < len(ln["words"]) - 1 else "")
            morph = (pymupdf.Point(x, ln["base"]), pymupdf.Matrix(w["sx"], 1)) if w["sx"] != 1.0 else None
            shape.insert_text((x, ln["base"]), t, fontname=FONT_NAMES[f], fontsize=w["size"], color=col,
                              morph=morph, render_mode=render_mode)
        if ln["underline"] and ln["words"] and render_mode == 0:
            a = ln["words"][0]["x"]
            b = ln["words"][-1]["x"] + ln["words"][-1]["w"]
            shape.draw_rect(pymupdf.Rect(a, ln["base"] + 0.7, b, ln["base"] + 1.45))
            shape.finish(color=None, fill=col, width=0)


def render(out_doc, size_px, rects, crops, plan):
    """Retypeset page: vector rects, artwork crops, real text (one content commit)."""
    page = _new_page(out_doc, size_px)
    shape = page.new_shape()
    for kind, x0, y0, x1, y1, gv in sorted(rects, key=lambda r: r[0] != "fill"):
        shape.draw_rect(pymupdf.Rect(x0 * PX, y0 * PX, x1 * PX, y1 * PX))
        shape.finish(color=None, fill=(gv / 255,) * 3, width=0)
    shape.commit()
    for (x0, y0, x1, y1), im in crops:
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        page.insert_image(pymupdf.Rect(x0 * PX, y0 * PX, x1 * PX, y1 * PX), stream=buf.getvalue())
    shape = page.new_shape()
    _text(shape, plan)
    shape.commit()
    return page


def render_image(out_doc, vis_rgb, plan):
    """Clean-image page: the scan after privacy whitening, with an invisible text layer."""
    size_px = (vis_rgb.shape[1], vis_rgb.shape[0])
    page = _new_page(out_doc, size_px)
    rgb = vis_rgb.astype(np.int16)
    chroma = int(np.percentile(rgb.max(2) - rgb.min(2), 99.5))
    buf = io.BytesIO()
    if chroma < 25:
        Image.fromarray(cv2.cvtColor(vis_rgb, cv2.COLOR_RGB2GRAY)).save(buf, format="PNG", optimize=True)
    else:
        Image.fromarray(vis_rgb).save(buf, format="JPEG", quality=88)
    page.insert_image(page.rect, stream=buf.getvalue())
    shape = page.new_shape()
    _text(shape, plan, render_mode=3)
    shape.commit()
    return page


def to_gray(page, dpi=300):
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)


def line_scores(rendered, vis_gray, plan):
    """Ink agreement (IoU) between the rebuilt page and the visible raster, per line."""
    h = min(rendered.shape[0], vis_gray.shape[0])
    w = min(rendered.shape[1], vis_gray.shape[1])
    out = {}
    for ln in plan:
        x0, y0, x1, y1 = ln["bbox"]
        a = rendered[y0:min(y1, h), x0:min(x1, w)] < 160
        b = vis_gray[y0:min(y1, h), x0:min(x1, w)] < 160
        a = cv2.dilate(a.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        b2 = cv2.dilate(b.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        inter = (a & b2).sum() + (b & a).sum()
        out[ln["id"]] = float(inter / max(1, a.sum() + b.sum()))
    return out
