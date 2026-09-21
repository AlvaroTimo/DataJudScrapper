from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from datajud_scraper.adhesion.ocr import sparse_words

pymupdf = pytest.importorskip("pymupdf")
Image = pytest.importorskip("PIL.Image")


def runtime_present():
    return (
        shutil.which("tesseract")
        or (Path.home() / ".local/opt/datajud-tesseract/usr/bin/tesseract").exists()
    )


@pytest.mark.skipif(not runtime_present(), reason="requiere OCR de tablas local")
def test_sparse_ocr_reads_form_identifiers_and_returns_actual_pixel_boxes():
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=400, height=250)
        page.draw_rect((20, 20, 380, 90))
        page.draw_line((20, 52), (380, 52))
        page.insert_text((30, 43), "CONTRATO 52-0298810/18", fontsize=15)
        page.insert_text((30, 78), "CPF: 123.456.789-00", fontsize=15)
        page.insert_text((30, 160), "Taxa de juros: 2,00% ao mes.", fontsize=15)
        pix = page.get_pixmap(dpi=240, alpha=False)
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    words = sparse_words(im)
    assert any("0298810" in w[4] for w in words)
    assert any("123.456.789" in w[4] for w in words)
    assert all(0 <= w[0] < w[2] <= 1 and 0 <= w[1] < w[3] <= 1 for w in words)


@pytest.mark.skipif(not runtime_present(), reason="requiere OCR de tablas local")
def test_missing_tsv_configuration_cannot_be_mistaken_for_empty_page(monkeypatch):
    monkeypatch.setattr(
        "datajud_scraper.adhesion.ocr.subprocess.run",
        lambda *_a, **_kw: SimpleNamespace(returncode=0, stdout=b"ordinary text, not TSV"),
    )
    with pytest.raises(RuntimeError, match="no devolvio TSV"):
        sparse_words(Image.new("RGB", (100, 100), "white"))
