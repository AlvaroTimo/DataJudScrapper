"""Native-text pages: copy the original vector page and truly remove what must stay hidden.

Text decisions are per word (see recrear_politica.decide_words); images and line art under
hidden areas are removed as well. Nothing is painted over: removed text leaves the page
background, so personal data shows as blank space.
"""

from __future__ import annotations

import io

import numpy as np
import pymupdf
from PIL import Image

import recrear_politica as P


def native_lines(page):
    """[(text, rect_norm, words)] with words = [(text, rect_pt)] in reading order per line."""
    W, H = page.rect.width, page.rect.height
    words = page.get_text("words", sort=False)
    by_line = {}
    for x0, y0, x1, y1, w, b, l, n in words:
        by_line.setdefault((b, l), []).append((n, w, pymupdf.Rect(x0, y0, x1, y1)))
    out = []
    for (b, l), ws in sorted(by_line.items()):
        ws.sort(key=lambda t: t[0])
        rect = pymupdf.Rect(ws[0][2])
        for _, _, r in ws[1:]:
            rect |= r
        text = " ".join(w for _, w, _ in ws)
        out.append((text, (rect.x0 / W, rect.y0 / H, rect.x1 / W, rect.y1 / H), [(w, r) for _, w, r in ws]))
    return out


def image_hash(doc, xref):
    """Perceptual difference hash (64 bit hex) of an embedded image, or None."""
    try:
        pix = pymupdf.Pixmap(doc, xref)
        if pix.alpha or pix.n not in (1, 3):
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    except Exception:
        return None
    a = np.asarray(img, dtype=np.int16)
    bits = (a[:, 1:] > a[:, :-1]).flatten()
    return "%016x" % int("".join("1" if b else "0" for b in bits), 2)


def unique_images(page, image_index):
    """Rects of images not repeated across expedientes (selfies, signatures, ID scans)."""
    hits = []
    for info in page.get_images(full=True):
        h = image_hash(page.parent, info[0])
        if h is None or image_index.get(h, 0) < P.MIN_EXPEDIENTES:
            hits.extend(r for r in page.get_image_rects(info[0]) if not (r & page.rect).is_empty)
    return hits


def black_boxes(page):
    """Solid black rectangles left by an earlier redaction (stamp covered in black)."""
    out = []
    for d in page.get_drawings():
        if d.get("fill") and max(d["fill"]) < 0.1 and d["rect"].width > 0.5 * page.rect.width:
            out.append(d["rect"])
    return out


def build(src_path, page_number, decide, out_doc, image_index, whiten_black=False, keep_images=False):
    """Append to out_doc the original page with hidden words/areas removed.

    decide(lines, page) -> (hidden_word_rects_pt, hidden_areas) where hidden_areas is a list of
    (rect_pt, remove_line_art). Returns a dict with what was done (for the QA record).
    """
    src = pymupdf.open(src_path)
    tmp = pymupdf.open()
    tmp.insert_pdf(src, from_page=page_number - 1, to_page=page_number - 1, links=0, annots=0, widgets=0)
    page = tmp[0]
    page.remove_rotation() if page.rotation else None
    lines = native_lines(page)
    hidden_words, areas, shown_text = decide(lines, page)

    unique = [] if keep_images else unique_images(page, image_index)
    for rect in unique:
        areas.append((rect, False))

    # pass 1: text, word by word (vertical inset keeps neighbouring lines intact)
    for r in hidden_words:
        inset = r.height * 0.2
        page.add_redact_annot(pymupdf.Rect(r.x0, r.y0 + inset, r.x1, r.y1 - inset), fill=False)
    page.apply_redactions(
        images=pymupdf.PDF_REDACT_IMAGE_NONE,
        graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
        text=pymupdf.PDF_REDACT_TEXT_REMOVE,
    )
    # pass 2: images (and line art for signatures/graphics) under hidden areas, text untouched
    for art in (False, True):
        chosen = [r for r, a in areas if a == art]
        if not chosen:
            continue
        for r in chosen:
            page.add_redact_annot(r, fill=False)
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
            graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED if art else pymupdf.PDF_REDACT_LINE_ART_NONE,
            text=pymupdf.PDF_REDACT_TEXT_NONE,
        )
    if whiten_black:
        for r in black_boxes(page):
            page.draw_rect(r, color=None, fill=(1, 1, 1), width=0)
    page.clean_contents()
    out_doc.insert_pdf(tmp, links=0, annots=0, widgets=0)
    return {"mode": "vector", "hidden_words": len(hidden_words), "hidden_areas": len(areas),
            "hidden_images": len(unique), "shown_text": shown_text}
