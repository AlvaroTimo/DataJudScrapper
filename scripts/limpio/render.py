"""Render a transcription markup file (.txt) into a clean A4 PDF.

Markup (one block per line; blank lines ignored; lines starting with '%' are comments):
  @title TEXT          title, centered, bold 14 (consecutive lines = one title block)
  @sec TEXT            section heading, bold 12
  @cl TEXT             clause heading on its own line, bold 11
  @sub TEXT            subclause heading on its own line, bold italic 11
  @clp HEAD | TEXT     paragraph opening with a bold clause number/heading
  @subp HEAD | TEXT    paragraph opening with a bold-italic subclause number/heading
  @p TEXT              body paragraph (justified)
  @f TEXT              form field / short line (left aligned), e.g. "Nome:"
  @i1 LABEL | TEXT     item, hanging indent depth 1 (also @i2, @i3, @i4)
  @ip1 TEXT            continuation paragraph inside an item of depth 1 (also @ip2...)
Inline: **bold**, *italic*, ***bold italic***, \\* literal asterisk, {{original||corrected}} for an
obvious error fix, […] for cuts.

usage: render.py IN.txt OUT.pdf [--literal]
"""

import re
import sys

from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate

pdfmetrics.registerFont(TTFont("FallbackSerif", "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"))
pdfmetrics.registerFont(TTFont("FallbackSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
_SERIF_CMAP = pdfmetrics.getFont("FallbackSerif").face.charToGlyph

CONNECTIVES = {"DO", "DA", "DE", "DOS", "DAS", "E", "A", "AO", "AOS", "AS", "À", "ÀS", "O", "OS",
               "PARA", "EM", "NO", "NA", "NOS", "NAS", "COM", "POR", "PELO", "PELA", "–", "-"}
MARGIN = 2.5 * cm
STEP = 1.0 * cm  # one indentation level for items
BODY = 11
LEAD = 14.5

base = ParagraphStyle("base", fontName="Times-Roman", fontSize=BODY, leading=LEAD,
                      alignment=TA_JUSTIFY, spaceAfter=5, textColor="black",
                      allowWidows=0, allowOrphans=0, uriWasteReduce=0.3)
S = {
    "title": ParagraphStyle("title", base, fontName="Times-Bold", fontSize=14, leading=18,
                            alignment=TA_CENTER, spaceAfter=4, keepWithNext=1),
    "sec": ParagraphStyle("sec", base, fontName="Times-Bold", fontSize=12, leading=15,
                          alignment=TA_LEFT, spaceBefore=10, spaceAfter=5, keepWithNext=1),
    "cl": ParagraphStyle("cl", base, fontName="Times-Bold", alignment=TA_LEFT,
                         spaceBefore=6, spaceAfter=3, keepWithNext=1),
    "sub": ParagraphStyle("sub", base, fontName="Times-BoldItalic", alignment=TA_LEFT,
                          spaceBefore=3, spaceAfter=3, keepWithNext=1),
    "p": base,
    "f": ParagraphStyle("f", base, alignment=TA_LEFT, spaceAfter=3),
}
FIX = r"\{\{(.*?)\|\|(.*?)\}\}"


def _fallback_font(ch):
    return "FallbackSerif" if ord(ch) in _SERIF_CMAP else "FallbackSans"


def _winansi(ch):
    try:
        ch.encode("cp1252")
        return True
    except UnicodeEncodeError:
        return False


def fallback(s):
    """Wrap characters Times (WinAnsi) cannot show in an embedded font."""
    return "".join(ch if _winansi(ch) else f'<font name="{_fallback_font(ch)}">{ch}</font>' for ch in s)


def inline(s, literal, fixes):
    def fix(m):
        fixes.append((m.group(1), m.group(2)))
        return m.group(1) if literal else m.group(2)
    s = re.sub(FIX, fix, s)
    s = s.replace("\\*", "\x00")  # escaped literal asterisk
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
    return fallback(s).replace("\x00", "*")


def _sep(rest):
    """Index of the HEAD/LABEL separator '|', ignoring the '||' inside {{orig||fix}}."""
    return re.sub(r"\{\{.*?\}\}", lambda m: "#" * len(m.group(0)), rest).find("|")


def split_head(rest, tag, n):
    i = _sep(rest)
    if i < 0:
        raise ValueError(f"line {n}: @{tag} needs 'HEAD | TEXT'")
    return rest[:i].strip(), rest[i + 1:].strip()


def build(src, out, literal=False):
    fixes, flow, title_buf = [], [], []

    def flush_title():
        i = 0  # a title line ending in a connective continues on the next line: join them
        while i < len(title_buf) - 1:
            last = re.sub(r"<[^>]+>", "", title_buf[i]).split()[-1:] or [""]
            if last[0].upper() in CONNECTIVES:
                title_buf[i:i + 2] = [title_buf[i] + " " + title_buf[i + 1]]
            else:
                i += 1
        for i, t in enumerate(title_buf):
            last = i == len(title_buf) - 1
            flow.append(Paragraph(t, ParagraphStyle("t", S["title"], spaceAfter=14) if last else S["title"]))
        title_buf.clear()

    items = []
    for n, raw in enumerate(open(src, encoding="utf-8").read().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("%"):
            continue
        m = re.match(r"@(\w+)\s?(.*)$", line)
        if not m:
            raise ValueError(f"line {n}: missing tag: {line[:60]}")
        items.append((n, m.group(1), m.group(2).strip()))

    # label column width per depth, shared by each run of consecutive items
    col, run_max = {}, {}
    for idx, (n, tag, rest) in enumerate(items):
        if re.fullmatch(r"i\d", tag):
            d = int(tag[1])
            label = re.sub(FIX, r"\2", rest[:_sep(rest)].strip().strip("*"))
            w = pdfmetrics.stringWidth(label, "Times-Roman", BODY) + 6
            run_max[d] = max(run_max.get(d, STEP), STEP, w)
        elif not re.fullmatch(r"ip\d", tag):
            run_max = {}
        col[idx] = dict(run_max)
    for idx in range(len(items) - 2, -1, -1):
        if re.fullmatch(r"i\d|ip\d", items[idx][1]) and re.fullmatch(r"i\d|ip\d", items[idx + 1][1]):
            for d, w in col[idx + 1].items():
                col[idx][d] = max(col[idx].get(d, 0), w)

    def indent(idx, d):
        return sum(col[idx].get(k, STEP) for k in range(1, d + 1))

    for idx, (n, tag, rest) in enumerate(items):
        if tag != "title":
            flush_title()
        if tag == "title":
            title_buf.append(inline(rest, literal, fixes))
        elif tag in ("sec", "cl", "sub", "p", "f"):
            flow.append(Paragraph(inline(rest, literal, fixes), S[tag]))
        elif tag in ("clp", "subp"):
            head, text = split_head(rest, tag, n)
            h = inline(head, literal, fixes)
            h = f"<b>{h}</b>" if tag == "clp" else f"<b><i>{h}</i></b>"
            flow.append(Paragraph(f"{h} {inline(text, literal, fixes)}", S["p"]))
        elif re.fullmatch(r"i\d", tag):
            d = int(tag[1])
            label, text = split_head(rest, tag, n)
            bold = label.startswith("**") and label.endswith("**")
            label = label.strip("*")
            for m in re.finditer(FIX, label):
                fixes.append((m.group(1), m.group(2)))
            label = re.sub(FIX, lambda m: m.group(1) if literal else m.group(2), label)
            font = ("Times-Bold" if bold else "Times-Roman") if all(_winansi(c) for c in label) \
                else _fallback_font(label[0])
            left = indent(idx, d)
            st = ParagraphStyle(f"i{d}", base, leftIndent=left, bulletIndent=left - col[idx].get(d, STEP),
                                bulletFontName=font, bulletFontSize=BODY, spaceAfter=4)
            flow.append(Paragraph(inline(text, literal, fixes), st, bulletText=label or None))
        elif re.fullmatch(r"ip\d", tag):
            st = ParagraphStyle(f"ip{tag[2]}", base, leftIndent=indent(idx, int(tag[2])), spaceAfter=4)
            flow.append(Paragraph(inline(rest, literal, fixes), st))
        else:
            raise ValueError(f"line {n}: unknown tag @{tag}")
    flush_title()

    SimpleDocTemplate(out, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN,
                      bottomMargin=MARGIN, title="", author="").build(flow)
    return fixes


if __name__ == "__main__":
    for o, c in build(sys.argv[1], sys.argv[2], literal="--literal" in sys.argv):
        print(f"fix: {o!r} -> {c!r}")
