from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from datajud_scraper.card_ocr import sparse_words
from datajud_scraper.card_privacy import (
    entity_boxes,
    pattern_masks,
    protect_labels_and_dates,
    validate_masks,
)

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
        "datajud_scraper.card_ocr.subprocess.run",
        lambda *_a, **_kw: SimpleNamespace(returncode=0, stdout=b"ordinary text, not TSV"),
    )
    with pytest.raises(RuntimeError, match="no devolvio TSV"):
        sparse_words(Image.new("RGB", (100, 100), "white"))


def test_entity_alignment_preserves_label_and_uses_hint_for_repeated_values():
    words = [[0.1, 0.1, 0.3, 0.12, "CPF:12345678900"], [0.1, 0.6, 0.2, 0.62, "12345678900"]]
    boxes = entity_boxes(words, "12345678900", [0.1, 0.1, 0.3, 0.12])
    assert len(boxes) == 1 and boxes[0][0] > 0.14 and boxes[0][1] < 0.2
    missing, uncertain = validate_masks(
        {
            "text_entities": [
                {"value": "no OCR", "bbox": [500, 500, 600, 600], "category": "person_name"}
            ],
            "visual_regions": [],
            "uncertain": False,
        },
        words,
    )
    assert uncertain and missing == []


def test_unique_long_identifier_survives_an_inaccurate_model_hint():
    words = [[0.6, 0.1, 0.8, 0.12, "Proposta:123456789"]]
    boxes = entity_boxes(words, "123456789", [0.05, 0.1, 0.2, 0.12])
    assert len(boxes) == 1 and boxes[0][0] > 0.68
    assert entity_boxes([[0.7, 0.1, 0.8, 0.12, "BA"]], "BA", [0.1, 0.1, 0.2, 0.12]) == []


def test_ocr_cache_separates_sparse_page_and_uniform_block_readings(tmp_path, monkeypatch):
    from datajud_scraper.card_privacy import ocr_words

    calls = []

    def recognize(_image, *, segmentation):
        calls.append(segmentation)
        return [[0.1, 0.1, 0.4, 0.2, str(segmentation)]]

    monkeypatch.setattr("datajud_scraper.card_ocr.sparse_words", recognize)
    image = Image.new("RGB", (100, 100), "white")
    assert ocr_words(image, tmp_path, engine="tesseract")[0][4] == "11"
    assert ocr_words(image, tmp_path, engine="tesseract", segmentation=6)[0][4] == "6"
    assert ocr_words(image, tmp_path, engine="tesseract")[0][4] == "11"
    assert calls == [11, 6]


def test_corporate_cnpj_and_card_margin_cannot_be_masked_as_cpf_or_salary():
    words = [
        [0.1, 0.1, 0.4, 0.12, "12.109.730/0001-04"],
        [0.75, 0.3, 0.85, 0.32, "Margem(%)"],
        [0.75, 0.33, 0.8, 0.35, "10,00"],
    ]
    masks, _ = validate_masks(
        {
            "text_entities": [
                {"value": "12.109.730/0001-04", "category": "cpf", "bbox": [100, 100, 400, 120]},
                {"value": "10,00", "category": "salary", "bbox": [750, 330, 800, 350]},
            ],
            "visual_regions": [],
            "uncertain": False,
        },
        words,
    )
    assert masks == []


def test_execution_date_and_signature_label_survive_a_broad_visual_mask():
    words = [
        [0.1, 0.75, 0.14, 0.77, "Local"],
        [0.145, 0.75, 0.15, 0.77, "e"],
        [0.16, 0.75, 0.2, 0.77, "Data:"],
        [0.35, 0.75, 0.5, 0.77, "14052018"],
        [0.1, 0.85, 0.2, 0.87, "ASSINATURA"],
        [0.21, 0.85, 0.3, 0.87, "TITULAR"],
    ]
    masks = protect_labels_and_dates(
        words, [{"rect": [0.05, 0.72, 0.6, 0.9], "category": "signature", "origin": "automatic"}]
    )
    assert masks
    for rect in [m["rect"] for m in masks]:
        for word in [words[3], words[4], words[5]]:
            assert not (
                max(rect[0], word[0]) < min(rect[2], word[2])
                and max(rect[1], word[1]) < min(rect[3], word[3])
            )


def test_execution_timestamps_survive_but_birth_date_and_identity_photos_do_not():
    words = [
        [0.1, 0.1, 0.2, 0.12, "Nascimento:"],
        [0.22, 0.1, 0.35, 0.12, "01/01/1980"],
        [0.1, 0.5, 0.3, 0.52, "Assinado"],
        [0.32, 0.5, 0.45, 0.52, "06/06/2022"],
        [0.47, 0.5, 0.58, 0.52, "16:50:20"],
        [0.1, 0.8, 0.3, 0.82, "01/01/2000"],
    ]
    original = [
        {"rect": [0.21, 0.09, 0.36, 0.13], "category": "birth_date", "origin": "automatic"},
        {"rect": [0.31, 0.49, 0.59, 0.53], "category": "signature", "origin": "automatic"},
        {"rect": [0.05, 0.7, 0.5, 0.9], "category": "identity_document", "origin": "automatic"},
    ]
    masks = protect_labels_and_dates(words, original)

    def covered(word):
        return any(
            min(m["rect"][2], word[2]) > max(m["rect"][0], word[0])
            and min(m["rect"][3], word[3]) > max(m["rect"][1], word[1])
            for m in masks
        )

    assert covered(words[1]) and covered(words[5])
    assert not covered(words[3]) and not covered(words[4])


def test_personal_fields_and_checkmarks_are_masked_without_erasing_contract_choices():
    words = [
        [0.7, 0.1, 0.79, 0.12, "NASCIMENTO:"],
        [0.7, 0.13, 0.8, 0.15, "01/01/1980"],
        [0.1, 0.3, 0.2, 0.32, "NACIONALIDADE:"],
        [0.1, 0.33, 0.2, 0.35, "BRASILEIRO"],
        [0.1, 0.4, 0.12, 0.42, "(X)"],
        [0.13, 0.4, 0.22, 0.42, "Feminino"],
        [0.3, 0.4, 0.4, 0.42, "Masculino"],
        [0.1, 0.7, 0.12, 0.72, "(X)"],
        [0.13, 0.7, 0.15, 0.72, "Sim"],
        [0.3, 0.7, 0.5, 0.72, "Autoriza cartão adicional"],
    ]
    masks = pattern_masks(words)
    assert len(masks) == 3
    assert {m["category"] for m in masks} == {"birth_date", "personal_other"}
    assert all(m["rect"][3] < 0.5 for m in masks)


def test_short_personal_value_cannot_match_inside_a_field_label_or_brand():
    words = [[0.1, 0.1, 0.3, 0.12, "CLASSE"], [0.1, 0.2, 0.3, 0.22, "BANCO"]]
    assert entity_boxes(words, "SS", [0.1, 0.1, 0.3, 0.12]) == []
    assert entity_boxes(words, "BA", [0.1, 0.2, 0.3, 0.22]) == []


def test_nationality_cannot_be_confused_with_a_law_reference_to_restore_birth_date():
    words = [
        [0.1, 0.1, 0.25, 0.12, "BRASILEIRO"],
        [0.7, 0.08, 0.85, 0.1, "NASCIMENTO:"],
        [0.7, 0.1, 0.85, 0.12, "01/01/1980"],
    ]
    masks = pattern_masks(words)
    assert any(m["category"] == "birth_date" for m in protect_labels_and_dates(words, masks))


def test_unselected_marital_status_choices_are_preserved():
    words = [
        [0.1, 0.28, 0.3, 0.29, "ESTADO CIVIL:"],
        [0.1, 0.3, 0.2, 0.32, "Casado"],
        [0.3, 0.3, 0.4, 0.32, "Divorciado"],
        [0.5, 0.3, 0.6, 0.32, "Solteiro"],
    ]
    assert not pattern_masks(words)
    masks, _ = validate_masks(
        {
            "text_entities": [
                {"value": "Casado", "bbox": [100, 300, 200, 320], "category": "personal_other"}
            ],
            "visual_regions": [],
            "uncertain": False,
        },
        words,
    )
    assert not masks


def test_wide_form_birth_date_is_not_restored_as_an_execution_date():
    words = [
        [0.05, 0.1, 0.2, 0.12, "Nascimento:"],
        [0.6, 0.1, 0.75, 0.12, "01/01/1980"],
        [0.6, 0.5, 0.75, 0.52, "01/01/2025"],
    ]
    birth_mask = {"rect": [0.599, 0.099, 0.751, 0.121], "category": "birth_date"}
    other_mask = {"rect": [0.599, 0.499, 0.751, 0.521], "category": "signature"}
    masks = protect_labels_and_dates(words, [birth_mask, other_mask])
    assert birth_mask in masks
    assert not any(
        min(m["rect"][2], words[2][2]) > max(m["rect"][0], words[2][0])
        and min(m["rect"][3], words[2][3]) > max(m["rect"][1], words[2][1])
        for m in masks
    )


def test_slanted_address_masks_do_not_bridge_into_adjacent_contractual_row():
    words = [
        [0.1, 0.5, 0.2, 0.52, "RUA"],
        [0.21, 0.49, 0.4, 0.51, "EXEMPLO"],
        [0.41, 0.48, 0.5, 0.5, "123"],
        [0.41, 0.505, 0.5, 0.525, "Juros"],
    ]
    boxes = entity_boxes(words, "RUA EXEMPLO 123", [0.1, 0.48, 0.5, 0.52])
    assert boxes
    assert not any(
        min(box[2], words[3][2]) > max(box[0], words[3][0])
        and min(box[3], words[3][3]) > max(box[1], words[3][1])
        for box in boxes
    )


def test_neural_ocr_cache_tracks_model_runtime_and_separates_legacy_ocr(tmp_path, monkeypatch):
    from datajud_scraper.card_privacy import ocr_words

    runtime = {"model": "first"}
    calls = []
    monkeypatch.setattr("datajud_scraper.card_ocr.neural_configuration", lambda: runtime)

    def recognize(_image):
        calls.append(runtime["model"])
        return [[0.1, 0.1, 0.4, 0.2, runtime["model"]]]

    monkeypatch.setattr("datajud_scraper.card_ocr.neural_words", recognize)
    image = Image.new("RGB", (100, 100), "white")
    assert ocr_words(image, tmp_path)[0][4] == "first"
    assert ocr_words(image, tmp_path)[0][4] == "first"
    runtime["model"] = "second"
    assert ocr_words(image, tmp_path)[0][4] == "second"
    assert calls == ["first", "second"]


def test_authentication_hash_fused_to_label_uses_observed_character_positions():
    label, value = "eletrônica", "AB12345678CD12345678EF1234567890AB"
    glyphs = [[0.1 + i * 0.005, 0.1, 0.105 + i * 0.005, 0.12] for i in range(len(label))]
    glyphs += [[0.2 + i * 0.01, 0.1, 0.21 + i * 0.01, 0.12] for i in range(len(value))]
    words = [
        [0.01, 0.1, 0.09, 0.12, "Autenticação"],
        [0.1, 0.1, glyphs[-1][2], 0.12, label + value, {"glyphs": glyphs}],
    ]
    masks = [m for m in pattern_masks(words) if m["detector"] == "signature_hash_pattern"]
    assert len(masks) == 1
    assert 0.19 < masks[0]["rect"][0] <= 0.2
    assert masks[0]["rect"][2] >= glyphs[-1][2]
    assert masks[0]["rect"][0] > glyphs[len(label) - 1][2]


def test_fingerprint_field_label_survives_while_the_fingerprint_is_erased():
    words = [
        [0.7, 0.4, 0.76, 0.42, "Polegar"],
        [0.77, 0.4, 0.83, 0.42, "Direito"],
        [0.84, 0.4, 0.9, 0.42, "Cliente"],
    ]
    masks = protect_labels_and_dates(
        words, [{"rect": [0.69, 0.39, 0.92, 0.6], "category": "biometric"}]
    )
    assert any(m["rect"][1] < 0.5 < m["rect"][3] for m in masks)
    for word in words:
        assert not any(
            min(m["rect"][2], word[2]) > max(m["rect"][0], word[0])
            and min(m["rect"][3], word[3]) > max(m["rect"][1], word[1])
            for m in masks
        )


def test_fingerprint_mask_reaches_the_observed_panel_edges():
    from PIL import ImageDraw

    from datajud_scraper.card_privacy import snap_sensitive_codes

    image = Image.new("RGB", (1000, 1000), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((600, 300, 900, 550), outline="black", width=2)
    masks = snap_sensitive_codes(
        image, [{"rect": [0.62, 0.31, 0.87, 0.54], "category": "biometric"}]
    )
    left, top, right, bottom = masks[0]["rect"]
    assert left <= 0.6 and top <= 0.3 and right >= 0.9 and bottom >= 0.55


def test_residual_execution_date_is_not_counted_as_unremoved_personal_data(tmp_path, monkeypatch):
    from datajud_scraper.card_privacy import anonymize_page

    words = [[0.1, 0.1, 0.3, 0.12, "Data/Hora:"], [0.31, 0.1, 0.5, 0.12, "04/04/2025"]]
    monkeypatch.setattr("datajud_scraper.card_privacy.ocr_words", lambda *_: words)

    class Model:
        def ask(self, task, *_):
            if task == "privacy_comparison":
                return {
                    "personal_data_remaining": False,
                    "contract_content_preserved": True,
                    "uncertain": False,
                }
            entities = []
            if task == "privacy_residual":
                entities = [
                    {
                        "value": "04/04/2025",
                        "category": "personal_address",
                        "bbox": [310, 100, 500, 120],
                    }
                ]
            return {"text_entities": entities, "visual_regions": [], "uncertain": False}

    result = anonymize_page(Model(), Image.new("RGB", (100, 100), "white"), tmp_path)
    assert result["status"] == "automatic_checks_passed"
    assert result["checks"]["remaining_detections"] == 0
    assert result["masks"] == []


def test_textual_repair_uses_original_context_instead_of_a_residual_corporate_guess(
    tmp_path, monkeypatch
):
    from datajud_scraper.card_privacy import anonymize_page

    words = [[0.1, 0.1, 0.3, 0.12, "Empresa"], [0.31, 0.1, 0.5, 0.12, "Exemplo"]]
    monkeypatch.setattr("datajud_scraper.card_privacy.ocr_words", lambda *_: words)
    monkeypatch.setattr(
        "datajud_scraper.card_privacy.detect_tiled_masks", lambda *_: ([], False, [])
    )

    class Model:
        def ask(self, task, *_):
            if task == "privacy_comparison":
                return {
                    "personal_data_remaining": True,
                    "contract_content_preserved": True,
                    "uncertain": False,
                }
            entities = []
            if task == "privacy_residual":
                entities = [
                    {
                        "value": "Empresa Exemplo",
                        "category": "personal_address",
                        "bbox": [100, 100, 500, 120],
                    }
                ]
            return {"text_entities": entities, "visual_regions": [], "uncertain": False}

    result = anonymize_page(Model(), Image.new("RGB", (100, 100), "white"), tmp_path)
    assert result["masks"] == []
    assert result["status"] == "needs_review"  # A disputed residual is not silently approved.


def test_execution_field_protection_does_not_restore_adjacent_contract_identifier():
    words = [
        [0.05, 0.1, 0.24, 0.12, "Local e Data:"],
        [0.25, 0.1, 0.4, 0.12, "Recife/PE"],
        [0.41, 0.1, 0.56, 0.12, "15/03/2025"],
        [0.65, 0.1, 0.74, 0.12, "Nº ADE:"],
        [0.75, 0.1, 0.9, 0.12, "12345678"],
        [0.05, 0.3, 0.3, 0.32, "Endereco residencial:"],
        [0.31, 0.3, 0.55, 0.32, "Rua Particular 55"],
    ]
    masks = protect_labels_and_dates(
        words,
        [
            {"rect": [0.249, 0.099, 0.401, 0.121], "category": "personal_address"},
            {"rect": [0.749, 0.099, 0.901, 0.121], "category": "customer_identifier"},
            {"rect": [0.309, 0.299, 0.551, 0.321], "category": "personal_address"},
        ],
    )

    def covered(x, y):
        return any(
            m["rect"][0] < x < m["rect"][2] and m["rect"][1] < y < m["rect"][3] for m in masks
        )

    assert not covered(0.3, 0.11)
    assert covered(0.8, 0.11)
    assert covered(0.4, 0.31)


def test_public_service_phone_survives_while_personal_phone_is_masked():
    words = [
        [0.05, 0.1, 0.13, 0.12, "SAC"],
        [0.14, 0.1, 0.4, 0.12, "0800 123 4567"],
        [0.05, 0.3, 0.2, 0.32, "Telefone pessoal:"],
        [0.21, 0.3, 0.45, 0.32, "(11)98765-4321"],
    ]
    masks = protect_labels_and_dates(
        words,
        [
            {"rect": [0.139, 0.099, 0.401, 0.121], "category": "phone"},
            {"rect": [0.209, 0.299, 0.451, 0.321], "category": "phone"},
        ],
    )
    assert all(m["rect"][1] > 0.2 for m in masks)
    assert masks


def test_transcript_anonymization_preserves_assent_and_roles_but_not_customer_name():
    words = [
        [0.05, 0.1, 0.2, 0.12, "Atendente:"],
        [0.21, 0.1, 0.55, 0.12, "Confirma o cartao?"],
        [0.1, 0.2, 0.18, 0.22, "(Sra."],
        [0.2, 0.2, 0.32, 0.22, "Maria"],
        [0.34, 0.2, 0.46, 0.22, "Autora):"],
        [0.48, 0.2, 0.64, 0.22, "Confirmo."],
    ]
    masks = protect_labels_and_dates(
        words, [{"rect": [0.099, 0.199, 0.641, 0.221], "category": "personal_other"}]
    )
    assert any(m["rect"][0] < 0.25 < m["rect"][2] for m in masks)
    assert not any(m["rect"][0] < 0.55 < m["rect"][2] for m in masks)
    assert not any(m["rect"][0] < 0.4 < m["rect"][2] for m in masks)


@pytest.mark.parametrize("page_rotation,crop_rotation", [(0, 0), (90, 0), (0, 90), (90, 270)])
def test_native_proportional_glyphs_remove_full_fused_hash_and_preserve_label(
    page_rotation, crop_rotation
):
    from datajud_scraper.card_automation import native_crop_layout

    label = "eletrônica"
    value = "AB12345678CD12345678EF1234567890AB"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=300)
        page.insert_text((60, 120), "Autenticação " + label + value, fontsize=12)
        page.set_rotation(page_rotation)
        words, _ = native_crop_layout(
            page, {"rect": [0.05, 0.05, 0.95, 0.95], "rotation": crop_rotation}
        )
    word = next(w for w in words if value in w[4])
    glyphs = word[5]["glyphs"]
    assert len(glyphs) == len(word[4])
    masks = protect_labels_and_dates(words, pattern_masks(words))

    def covered(glyph):
        x, y = (glyph[0] + glyph[2]) / 2, (glyph[1] + glyph[3]) / 2
        return any(
            m["rect"][0] <= x <= m["rect"][2] and m["rect"][1] <= y <= m["rect"][3] for m in masks
        )

    assert all(covered(g) for g in glyphs[len(label) :])
    assert not any(covered(g) for g in glyphs[: len(label)])
    # A visible half-character still leaks part of the identifier. Check the
    # complete observed glyph rectangles after public-label protection, not only
    # character centres before the protection step.
    from datajud_scraper.card_privacy import subtract_box

    for glyph in glyphs[len(label) :]:
        remaining = [glyph]
        for mask in masks:
            remaining = [piece for rect in remaining for piece in subtract_box(rect, mask["rect"])]
        assert sum((r[2] - r[0]) * (r[3] - r[1]) for r in remaining) < 1e-12


def test_fused_cpf_label_survives_without_leaving_personal_digits():
    words = [[0.1, 0.1, 0.8, 0.12, "CPF:123.456.789-00"]]
    masks = pattern_masks(words)
    boundary = 0.1 + 4 / len(words[0][4]) * 0.7
    assert masks and min(m["rect"][0] for m in masks) >= boundary - 0.002
    assert max(m["rect"][2] for m in masks) >= 0.8


@pytest.mark.parametrize("suffix", ["S.A.", "S/A", "SA"])
def test_slash_bank_row_keeps_institution_code_and_removes_short_agency(suffix):
    value = f"Banco Exemplo {suffix}/69/1/1234567-8"
    words = [[0.05, 0.1, 0.95, 0.12, value]]
    masks = protect_labels_and_dates(words, pattern_masks(words))
    width = 0.9 / len(value)

    def masked(char_index):
        x = 0.05 + (char_index + 0.5) * width
        return any(
            m["rect"][0] < x < m["rect"][2] and m["rect"][1] < 0.11 < m["rect"][3] for m in masks
        )

    assert not any(masked(i) for i in range(value.index("/1/")))
    assert masked(value.index("/1/") + 1)
    assert all(masked(i) for i in range(value.index("1234567"), len(value)))


def test_bank_code_in_its_own_field_survives_but_adjacent_agency_is_removed():
    words = [
        [0.1, 0.1, 0.2, 0.12, "Banco"],
        [0.1, 0.13, 0.15, 0.15, "104"],
        [0.4, 0.1, 0.55, 0.12, "Agência"],
        [0.4, 0.13, 0.45, 0.15, "001"],
    ]
    masks = protect_labels_and_dates(
        words,
        [
            {"rect": [0.099, 0.129, 0.151, 0.151], "category": "account"},
            {"rect": [0.399, 0.129, 0.451, 0.151], "category": "account"},
        ],
    )
    assert masks and all(m["rect"][0] > 0.3 for m in masks)


def test_wrapped_correspondent_phone_is_public_but_following_agent_cpf_is_not():
    words = [
        [0.05, 0.1, 0.7, 0.12, "DADOS DO CORRESPONDENTE"],
        [0.05, 0.14, 0.32, 0.16, "1. Empresa / CNPJ"],
        [0.4, 0.14, 0.42, 0.16, "1."],
        [0.43, 0.14, 0.8, 0.16, "Empresa Exemplo 12.109.730/0001-04"],
        [0.05, 0.17, 0.32, 0.19, "2. Endereço/telefone"],
        [0.4, 0.17, 0.42, 0.19, "2."],
        [0.43, 0.17, 0.8, 0.19, "Avenida Empresarial 100"],
        [0.05, 0.2, 0.07, 0.22, "3."],
        [0.08, 0.2, 0.18, 0.22, "Nome/CPF"],
        [0.19, 0.2, 0.32, 0.22, "do Agente"],
        [0.4, 0.22, 0.6, 0.24, "(11)3333-4444"],
        [0.4, 0.25, 0.42, 0.27, "3."],
        [0.43, 0.25, 0.7, 0.27, "123.456.789-00"],
    ]
    masks = protect_labels_and_dates(
        words,
        [
            {"rect": [0.399, 0.219, 0.601, 0.241], "category": "phone"},
            {"rect": [0.429, 0.249, 0.701, 0.271], "category": "cpf"},
        ],
    )
    assert masks and all(m["rect"][1] >= 0.249 for m in masks)


def test_two_line_regulatory_code_keeps_column_identity_and_not_neighbor_customer_number():
    words = [
        [0.6, 0.1, 0.67, 0.12, "Código"],
        [0.68, 0.1, 0.75, 0.12, "SUSEP"],
        [0.6, 0.121, 0.74, 0.141, "Seguradora"],
        [0.6, 0.145, 0.67, 0.165, "03417"],
        [0.8, 0.1, 0.9, 0.12, "Nº ADE"],
        [0.8, 0.145, 0.9, 0.165, "12345678"],
    ]
    masks = protect_labels_and_dates(
        words,
        [
            {"rect": [0.599, 0.144, 0.671, 0.166], "category": "customer_identifier"},
            {"rect": [0.799, 0.144, 0.901, 0.166], "category": "customer_identifier"},
        ],
    )
    assert masks and all(m["rect"][0] > 0.7 for m in masks)


def test_qr_evidence_is_decoded_from_actual_pixels_and_sent_as_untrusted_data():
    import json

    import cv2
    import numpy as np

    from datajud_scraper.card_privacy import validated_detection

    payload = "form-v2-page3"
    pixels = cv2.QRCodeEncoder_create().encode(payload)
    pixels = np.pad(pixels, 4, constant_values=255)
    image = Image.fromarray(pixels).resize((500, 500), Image.Resampling.NEAREST).convert("RGB")

    class Model:
        def ask(self, task, prompt, content, *_):
            codes = json.loads(content)["decoded_qr_codes"]
            assert len(codes) == 1 and codes[0]["payload"] == payload
            assert not codes[0]["truncated"]
            assert "untrusted" in prompt and "form/template/version/page" in prompt
            return {"text_entities": [], "visual_regions": [], "uncertain": False}

    assert validated_detection(Model(), image, []) == ([], False)


def test_supplemental_heading_is_inserted_before_body_without_reordering_slanted_rows():
    from datajud_scraper.card_ocr import merge_reading_words, transform_layout_words

    body = [[0.1, 0.3, 0.3, 0.33, "Linha"], [0.4, 0.29, 0.6, 0.32, "inclinada"]]
    band = [[0.1, 0.1, 0.8, 0.2, "CONDIÇÕES GERAIS", {"glyphs": [[0.1, 0.1, 0.2, 0.2]]}]]
    placed = transform_layout_words(band, [0, 0, 1, 0.25])
    merged = merge_reading_words(body, placed + body)
    assert [w[4] for w in merged] == ["CONDIÇÕES GERAIS", "Linha", "inclinada"]
    assert merged[0][1:4:2] == pytest.approx([0.025, 0.05])
    assert merged[0][5]["glyphs"][0] == pytest.approx([0.1, 0.025, 0.2, 0.05])


@pytest.mark.parametrize(
    "address", ["192.0.2.25", "2001:db8:0:1:2:3:4:5", "2001:db8::25", "fe80::1", "::1"]
)
def test_signing_ip_patterns_remove_full_address_but_keep_execution_time(address):
    words = [
        [0.05, 0.1, 0.5, 0.12, "IP/Terminal:" + address],
        [0.05, 0.2, 0.18, 0.22, "Data/Hora:"],
        [0.2, 0.2, 0.3, 0.22, "15:24:01"],
        [0.05, 0.3, 0.3, 0.32, "999.1.2.3"],
    ]
    masks = pattern_masks(words)
    assert len(masks) == 1 and masks[0]["detector"] == "authentication_ip_pattern"
    assert masks[0]["rect"][0] > words[0][0]
    assert masks[0]["rect"][2] >= words[0][2]
    assert masks[0]["rect"][3] < 0.15


def test_wide_form_marital_status_is_removed_without_row_number_or_unselected_options():
    words = [
        [0.05, 0.1, 0.25, 0.12, "5. Estado Civil:"],
        [0.6, 0.1, 0.9, 0.12, "5.Solteiro"],
        [0.05, 0.3, 0.25, 0.32, "Estado Civil:"],
        [0.4, 0.3, 0.55, 0.32, "Casado"],
        [0.6, 0.3, 0.75, 0.32, "Solteiro"],
    ]
    masks = pattern_masks(words)
    assert len(masks) == 1 and masks[0]["category"] == "personal_other"
    assert masks[0]["rect"][0] > 0.65 and masks[0]["rect"][2] >= 0.9
    assert masks[0]["rect"][3] < 0.15


def test_corporate_section_preserves_company_but_not_individual_agent_or_account():
    words = [
        [0.05, 0.1, 0.25, 0.12, "Banco Exemplo S.A."],
        [0.3, 0.1, 0.5, 0.12, "/ 0123 / 456789"],
        [0.05, 0.2, 0.6, 0.22, "Dados do Correspondente"],
        [0.05, 0.24, 0.4, 0.26, "Empresa / CNPJ / telefone"],
        [0.42, 0.24, 0.8, 0.26, "Empresa Comercial ME"],
        [0.05, 0.28, 0.35, 0.3, "Endereço"],
        [0.42, 0.28, 0.8, 0.3, "Rua Comercial 123"],
        [0.05, 0.32, 0.35, 0.34, "Nome/CPF do Agente"],
        [0.42, 0.32, 0.8, 0.34, "Maria Exemplo"],
    ]
    masks = protect_labels_and_dates(
        words, [{"rect": w[:4], "category": "person_name"} for w in words]
    )

    def covered(word):
        x, y = (word[0] + word[2]) / 2, (word[1] + word[3]) / 2
        return any(
            m["rect"][0] <= x <= m["rect"][2] and m["rect"][1] <= y <= m["rect"][3] for m in masks
        )

    assert not any(covered(words[i]) for i in (0, 4, 6))
    assert all(covered(words[i]) for i in (1, 8))


def test_collective_policy_column_is_preserved_without_restoring_individual_ade():
    words = [
        [0.2, 0.1, 0.35, 0.12, "Nº Apólice:"],
        [0.65, 0.1, 0.85, 0.12, "Nº Contrato (ADE):"],
        [0.2, 0.13, 0.35, 0.15, "759300001/"],
        [0.65, 0.13, 0.85, 0.15, "12345678"],
        [0.2, 0.16, 0.35, 0.18, "759300002"],
        [0.05, 0.5, 0.9, 0.52, "O banco possui uma apólice de seguros de vida coletiva."],
    ]
    masks = protect_labels_and_dates(
        words, [{"rect": words[i][:4], "category": "customer_identifier"} for i in (2, 3, 4)]
    )
    assert masks == [{"rect": words[3][:4], "category": "customer_identifier"}]


@pytest.mark.parametrize("category", ["identity_document", "photo", "biometric"])
def test_execution_and_public_looking_text_inside_identifying_image_is_not_restored(category):
    words = [
        [0.2, 0.3, 0.8, 0.33, "Local e Data: Recife/PE 15/03/2025"],
        [0.2, 0.4, 0.8, 0.43, "Banco Exemplo S.A."],
        [0.2, 0.5, 0.8, 0.53, "SAC 0800 123 4567"],
    ]
    mask = {"rect": [0.1, 0.2, 0.9, 0.8], "category": category}
    assert protect_labels_and_dates(words, [mask]) == [mask]
