from __future__ import annotations

import hashlib

import pytest

from datajud_scraper.contract_redaction import (
    identifier_proposals,
    pixel_box,
    redact_pixels,
    region_rotation,
    render_contract_page,
    write_cleaned_contract,
)

pymupdf = pytest.importorskip("pymupdf")
Image = pytest.importorskip("PIL.Image")


@pytest.mark.parametrize("rotation", [True, 45, -90, 360, "180", 180.0])
def test_non_quarter_turn_rotation_is_rejected(rotation):
    with pytest.raises(ValueError, match="rotacion"):
        region_rotation({"rotation": rotation})


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_rotated_crop_and_masks_survive_pdf_round_trip(tmp_path, rotation):
    source = tmp_path / "upside-down.pdf"
    output = tmp_path / "upright.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=120, height=100)
        page.draw_rect((0, 0, 120, 100), color=None, fill=(0.2, 0.4, 0.6))
        page.draw_rect((10, 10, 70, 30), color=None, fill=(0.8, 0.2, 0.1))
        page.insert_text((10, 55), "Credit terms", fontsize=8)
        doc.save(source)
    region = {"page": 1, "rect": [0, 0.1, 1, 0.7], "rotation": rotation}
    with pymupdf.open(source) as doc:
        base = render_contract_page(doc, {**region, "rotation": 0}, dpi=180)
        upright = render_contract_page(doc, region, dpi=180)
    assert upright.size == (base.size if rotation == 180 else base.size[::-1])
    # Check the documented clockwise coordinates against the unrotated crop.
    for x, y in [(0, 0), (25, 25), (100, 80), (base.width - 1, base.height - 1)]:
        target = {
            90: (base.height - 1 - y, x),
            180: (base.width - 1 - x, base.height - 1 - y),
            270: (y, base.width - 1 - x),
        }[rotation]
        assert upright.getpixel(target) == base.getpixel((x, y))
    mask = {"rect": [0.2, 0.3, 0.4, 0.5], "category": "name", "confirmed": True}
    evidence = write_cleaned_contract(source, output, [region], {1: [mask]}, dpi=180)
    assert evidence["pages"][0]["source_rotation"] == rotation
    with pymupdf.open(output) as doc:
        pix = pymupdf.Pixmap(doc, doc[0].get_images()[0][0])
        clean = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    box = pixel_box(mask["rect"], *upright.size)
    for y in range(upright.height):
        for x in range(upright.width):
            expected = (
                (255, 255, 255)
                if box[0] <= x < box[2] and box[1] <= y < box[3]
                else upright.getpixel((x, y))
            )
            assert clean.getpixel((x, y)) == expected


def test_redaction_preserves_every_pixel_outside_masks():
    image = Image.new("RGB", (100, 100), (18, 34, 59))
    mask = {"rect": [0.1, 0.2, 0.3, 0.4], "confirmed": True, "category": "signature"}
    cleaned, boxes = redact_pixels(image, [mask])
    assert boxes == [(10, 20, 30, 40)]
    for y in range(100):
        for x in range(100):
            assert cleaned.getpixel((x, y)) == (
                (255, 255, 255) if 10 <= x < 30 and 20 <= y < 40 else (18, 34, 59)
            )
    with pytest.raises(ValueError, match="confirmacion manual"):
        redact_pixels(image, [{**mask, "confirmed": False}])


@pytest.mark.parametrize("rect", ([0, 0, 2, 1], [0.5, 0, 0.2, 1], [0, 0, float("nan"), 1]))
def test_invalid_redaction_boxes_rejected(rect):
    with pytest.raises(ValueError):
        pixel_box(rect, 100, 100)


def test_clean_pdf_contains_only_cropped_redacted_pixels_and_no_source_objects(tmp_path):
    source = tmp_path / "original.pdf"
    output = tmp_path / "cleaned.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=400)
        page.insert_text((40, 40), "CLIENTE TESTE - CPF 123.456.789-00")
        page.insert_text((40, 120), "Taxa de juros: 2,00%. Valor: R$ 1.500,00. Prazo: 24 meses.")
        page.insert_text((40, 370), "PECA JUDICIAL FORA DO CONTRATO")
        page.add_text_annot((380, 380), "Hidden personal data")
        document.embfile_add("private.txt", b"hidden personal data")
        document.set_metadata({"author": "CLIENTE TESTE"})
        document.save(source)
    original_bytes = source.read_bytes()
    regions = [{"page": 1, "rect": [0, 0, 1, 0.7]}]
    masks = {
        1: [{"rect": [0.09, 0.07, 0.85, 0.18], "category": "personal_identity", "confirmed": True}]
    }
    evidence = write_cleaned_contract(source, output, regions, masks, dpi=180)
    assert source.read_bytes() == original_bytes
    assert evidence["manual_review_status"] == "pending"
    with pymupdf.open(output) as cleaned:
        assert not cleaned.embfile_count()
        assert not cleaned[0].get_text()
        assert not list(cleaned[0].annots() or [])
        assert not cleaned.metadata["author"]
        pixels = pymupdf.Pixmap(cleaned, cleaned[0].get_images()[0][0])
        assert (
            hashlib.sha256(pixels.samples).hexdigest()
            == evidence["pages"][0]["cleaned_pixels_sha256"]
        )
    with pymupdf.open(source) as original:
        before = render_contract_page(original, regions[0], dpi=180)
        expected, _ = redact_pixels(before, masks[1])
    assert pixels.samples == expected.tobytes()
    # The contractual terms sit outside the mask and are preserved exactly.
    assert (
        expected.crop((50, 230, 950, 330)).tobytes() == before.crop((50, 230, 950, 330)).tobytes()
    )
    with pytest.raises(ValueError, match="ya existe"):
        write_cleaned_contract(source, output, regions, masks, dpi=180)


def test_identifier_proposals_are_unconfirmed_and_do_not_store_personal_values():
    words = [
        (10, 20, 30, 30, "CPF:"),
        (35, 20, 85, 30, "123.456.789-00"),
        (10, 50, 95, 60, "person@example.com"),
    ]
    proposals = identifier_proposals(words, 100, 100)
    assert {p["category"] for p in proposals} >= {"cpf", "email"}
    assert all(p["confirmed"] is False for p in proposals)
    assert "123.456" not in str(proposals) and "person@example.com" not in str(proposals)
