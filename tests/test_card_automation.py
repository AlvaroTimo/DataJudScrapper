from __future__ import annotations

import json
from pathlib import Path

import pytest

from datajud_scraper.card_automation import (
    configuration,
    group_occurrences,
    initialize_automation_schema,
    run_document,
)
from datajud_scraper.card_corpus import choose_validation
from datajud_scraper.card_detection import (
    candidate_pages,
    classify_text,
    contract_continuations,
    contractual_page_rect,
    locate_regions,
    needs_visual_segmentation,
    normalized_box,
    signature_annexes,
    validate_page_results,
)
from datajud_scraper.card_model import LocalModel
from datajud_scraper.card_privacy import economic_overlap, validate_masks, validated_detection
from datajud_scraper.contract_inventory import inventory_document, save_inventory
from datajud_scraper.contract_redaction import redact_pixels
from datajud_scraper.pdf_validation import hash_file
from datajud_scraper.runtime import Database

pymupdf = pytest.importorskip("pymupdf")
Image = pytest.importorskip("PIL.Image")


class Model:
    digest = "test-model-digest"
    runtime = "test-runtime"


@pytest.mark.parametrize(
    "endpoint,model",
    [
        ("https://example.com", "local"),
        ("http://10.0.0.1:11434", "local"),
        ("http://127.0.0.1:11434", "qwen-cloud"),
        ("http://user:pass@localhost", "local"),
    ],
)
def test_documents_cannot_be_sent_to_external_model(tmp_path, endpoint, model):
    with pytest.raises(ValueError):
        LocalModel(tmp_path, endpoint=endpoint, model=model)


def test_holdout_is_reproducible_independent_and_contains_100_distinct_documents():
    rows = [
        {
            "document_id": str(i),
            "available": i != 3,
            "development_example": i < 20,
            "stratum": str(i % 8),
        }
        for i in range(180)
    ]
    selected = choose_validation(rows)
    assert len(selected) == len({r["document_id"] for r in selected}) == 100
    assert selected == choose_validation(list(reversed(rows)))
    assert all(r["available"] and not r["development_example"] for r in selected)
    assert len({r["stratum"] for r in selected}) == 8


def test_incomplete_or_hallucinated_page_results_are_not_accepted():
    batch = [{"page": 2, "text": "Termo de adesao ao cartao de credito"}]
    with pytest.raises(ValueError, match="exactamente"):
        validate_page_results({"pages": []}, batch)
    row = {
        "page": 2,
        "state": "card_contract",
        "kind": "adhesion",
        "start": True,
        "confidence": 0.99,
        "evidence": "texto inexistente",
    }
    assert validate_page_results({"pages": [row]}, batch)[0]["state"] == "uncertain"
    with pytest.raises(ValueError, match="exactamente"):
        validate_page_results({"pages": [row, row]}, batch)


def test_unnamed_scan_is_routed_for_visual_reading():
    pages = [
        {
            "page_number": 1,
            "height": 100,
            "words": [],
            "text": "",
            "image_fraction": 1,
            "ocr_error": None,
        }
    ]
    attachments = [{"start_page": 1, "end_page": 1, "title": "Anexo.pdf"}]
    assert candidate_pages(pages, attachments) == {1}


def test_confident_negative_with_explicit_card_ccb_requires_image_check():
    page = {
        "height": 100,
        "words": [],
        "text": "Cédula de crédito bancário: saque através do cartão consignado benefício",
    }
    assert needs_visual_segmentation(page, {"state": "other_credit", "confidence": 0.99})


def test_confident_contract_inside_pleading_requires_localized_extraction():
    page = {
        "height": 100,
        "words": [],
        "text": "A parte autora assinou o termo de adesão ao cartão reproduzido a seguir",
    }
    assert needs_visual_segmentation(page, {"state": "card_contract", "confidence": 0.99})


def test_card_charge_dispute_clause_is_not_confused_with_a_court_defense():
    from datajud_scraper.card_detection import mixed_contract_page

    page = {
        "height": 100,
        "words": [],
        "text": "Contestação de valores: confira sua fatura. Você tem 90 dias para contestar a compra.",
    }
    assert not mixed_contract_page(page)
    assert not needs_visual_segmentation(page, {"state": "card_contract", "confidence": 0.99})
    assert mixed_contract_page(
        {**page, "text": "CONTESTAÇÃO. A parte autora alega que não contratou o cartão."}
    )


def test_native_footer_is_removed_when_scan_ocr_damaged_the_footer_text():
    page = {
        "height": 100,
        "words": [[0, 95, 10, 96, "Asslnado"]],
        "native_words": [
            [0, 95, 10, 96, "Assinado"],
            [11, 95, 20, 96, "eletronicamente"],
            [21, 95, 30, 96, "por:"],
        ],
    }
    assert contractual_page_rect(page) == [0, 0, 1, 0.947]


def test_incomplete_model_batch_is_retried_without_losing_pages():
    class IncompleteModel:
        def ask(self, _task, _system, content, _schema):
            requested = json.loads(content)["classify_pages"]
            # Mimic a malformed model batch. Single-page retries are complete.
            return {
                "pages": [
                    {
                        "page": requested[0]["page"],
                        "state": "noncontract",
                        "kind": "not_applicable",
                        "start": False,
                        "confidence": 0.99,
                        "evidence": "",
                    }
                ]
            }

    pages = [
        {
            "page_number": n,
            "height": 100,
            "words": [],
            "text": "Contrato mencionado",
            "attachment_position": 1,
        }
        for n in range(1, 5)
    ]
    attachments = [{"position": 1, "start_page": 1, "end_page": 4, "title": "contrato"}]
    decisions = classify_text(IncompleteModel(), pages, attachments)
    assert set(decisions) == {1, 2, 3, 4}
    assert all(p["state"] == "noncontract" for p in decisions.values())


def test_invalid_single_page_response_requires_visual_reading():
    class BrokenModel:
        def ask(self, *_args):
            return {"pages": []}

    pages = [
        {
            "page_number": 1,
            "height": 100,
            "words": [],
            "text": "Contrato",
            "attachment_position": 1,
        }
    ]
    attachments = [{"position": 1, "start_page": 1, "end_page": 1, "title": "contrato"}]
    decision = classify_text(BrokenModel(), pages, attachments)[1]
    assert decision["state"] == "uncertain" and decision["confidence"] == 0


@pytest.mark.parametrize(
    "box", [[0, 0, 1001, 999], [20, 10, 0, 30], [True, 0, 100, 100], [0, 0, 0, 100]]
)
def test_model_cannot_select_out_of_bounds_or_zero_area_crops(box):
    with pytest.raises(ValueError):
        normalized_box(box)


def test_visual_segmentation_retries_invalid_boxes_and_keeps_distinct_documents():
    class Replies:
        calls = 0

        def ask(self, *args):
            self.calls += 1
            return {
                "state": "card_excerpt",
                "kind": "card_excerpt",
                "confidence": 0.98,
                "regions": [
                    {
                        "bbox": [10, 10, 900, 1100 if self.calls == 1 else 400],
                        "rotation": 0,
                        "document": 1,
                    },
                    {"bbox": [10, 500, 900, 800], "rotation": 0, "document": 2},
                ],
            }

    model = Replies()
    page = {"page_number": 1, "words": [], "height": 100, "text": ""}
    result = locate_regions(model, Image.new("RGB", (100, 100)), page, "")
    assert model.calls == 2
    assert result["state"] == "card_excerpt"
    assert [r["document"] for r in result["regions"]] == [1, 2]


def test_repeatedly_invalid_segmentation_is_unresolved_not_a_negative():
    class Broken:
        def ask(self, *args):
            return {"state": "noncontract"}

    page = {"page_number": 1, "words": [], "height": 100, "text": ""}
    result = locate_regions(Broken(), Image.new("RGB", (100, 100)), page, "")
    assert result["state"] == "uncertain" and result["confidence"] == 0


@pytest.mark.parametrize("retry_succeeds", [True, False])
def test_privacy_invalid_box_is_retried_or_left_unresolved(retry_succeeds):
    class Replies:
        calls = 0

        def ask(self, *args):
            self.calls += 1
            valid = retry_succeeds and self.calls > 1
            return {
                "text_entities": [
                    {"value": "Cliente", "category": "person_name", "bbox": [100, 100, 200, 120]}
                ],
                "visual_regions": [
                    {"category": "signature", "bbox": [950, 800, 990 if valid else 1030, 850]}
                ],
                "uncertain": False,
            }

    model = Replies()
    masks, uncertain = validated_detection(
        model, Image.new("RGB", (100, 100)), [[0.1, 0.1, 0.2, 0.12, "Cliente"]]
    )
    assert model.calls == 2
    assert uncertain is not retry_succeeds
    assert len(masks) == (2 if retry_succeeds else 1)
    assert all(0 <= coordinate <= 1 for mask in masks for coordinate in mask["rect"])


def test_occurrences_keep_repeated_contracts_and_exclude_other_credit():
    pages = [{"page_number": n, "attachment_position": 1} for n in range(1, 6)]
    predictions = {
        n: {
            "state": "card_contract",
            "kind": "adhesion",
            "start": n in (1, 3),
            "regions": [{"page": n, "rect": [0, 0, 1, 0.94]}],
        }
        for n in range(1, 6)
    }
    predictions[5]["state"] = "other_credit"
    grouped = group_occurrences(predictions, pages)
    assert [[r["page"] for r in c["regions"]] for c in grouped] == [[1, 2], [3, 4]]


def test_card_signature_annex_is_kept_but_transfer_receipt_is_excluded():
    pages = [
        {"page_number": n, "attachment_position": 1, "height": 100, "words": [], "text": text}
        for n, text in [
            (1, "Adesao cartao"),
            (2, "ASSINATURA DIGITAL. Nome e data"),
            (3, "COMPROVANTE DE TED. Assinatura digital"),
        ]
    ]
    predictions = {
        1: {"state": "card_contract", "kind": "adhesion"},
        2: {"state": "uncertain"},
        3: {"state": "uncertain"},
    }
    signature_annexes(predictions, pages)
    assert predictions[2]["state"] == "card_contract"
    assert predictions[3]["state"] == "uncertain"


def test_contract_continuation_not_lost_due_to_nonliteral_ocr_quote():
    pages = [
        {
            "page_number": n,
            "attachment_position": 1,
            "height": 100,
            "words": [],
            "text": "Capitulo 8 - Obrigações do ASSOCIADO",
        }
        for n in (1, 2, 3)
    ]
    predictions = {
        1: {"state": "card_contract", "kind": "general_conditions"},
        2: {"state": "uncertain", "proposed_state": "card_contract"},
        3: {"state": "card_contract", "kind": "general_conditions"},
    }
    contract_continuations(predictions, pages)
    assert predictions[2]["state"] == "card_contract"


@pytest.mark.parametrize("mismatch", [None, "url", "counter", "attachment"])
def test_numbered_native_contract_recovers_uncertain_pages_only_with_matching_instrument(mismatch):
    from datajud_scraper.card_detection import numbered_contract_continuations

    pages = [
        {
            "page_number": n,
            "attachment_position": 1,
            "width": 100,
            "height": 100,
            "text_method": "native",
            "words": [
                [5, 20, 80, 25, "Condições do contrato"],
                [5, 90, 60, 93, "https://example.com/contratos/cartao"],
                [90, 90, 99, 93, f"{n}/4"],
            ],
        }
        for n in range(1, 5)
    ]
    if mismatch == "url":
        pages[1]["words"][1][4] = "https://example.com/contratos/conta"
    elif mismatch == "counter":
        pages[1]["words"][2][4] = "1/4"
    elif mismatch == "attachment":
        pages[1]["attachment_position"] = 2
    predictions = {
        n: {"state": "card_contract" if n in (1, 4) else "uncertain", "kind": "general_conditions"}
        for n in range(1, 5)
    }
    numbered_contract_continuations(predictions, pages)
    if mismatch:
        assert all(predictions[n]["state"] == "uncertain" for n in (2, 3))
    else:
        assert all(predictions[n]["state"] == "card_contract" for n in (2, 3))
        assert predictions[2]["regions"][0]["page"] == 2


def test_website_menu_is_excluded_without_excluding_contractual_contacts_and_clauses():
    from datajud_scraper.card_detection import website_navigation_page

    menu = (
        "Contrato e regulamento Cartão | Banco. Fornecedores Seja um fornecedor "
        "Sala de Imprensa Perguntas frequentes Trabalhe com a gente Carreiras "
        "Dados abertos Relatórios financeiros Ouvidoria 0800 123 4567"
    )
    pages = [
        {
            "page_number": n,
            "height": 100,
            "words": [],
            "attachment_position": 1,
            "text_method": "native",
            "text": text,
        }
        for n, text in enumerate(["Condições do cartão", menu], 1)
    ]
    predictions = {n: {"state": "card_contract", "kind": "general_conditions"} for n in (1, 2)}
    contract_continuations(predictions, pages)
    assert predictions[2]["state"] == "noncontract" and predictions[2]["regions"] == []
    assert not website_navigation_page({**pages[1], "text": "CNPJ: 00.000.000/0001-00 " + menu})
    assert not website_navigation_page(
        {**pages[1], "text": "Você pagará juros de 2% ao mês. " + menu}
    )
    assert not website_navigation_page(
        {**pages[1], "text": "Central de atendimento SAC 0800 123 4567"}
    )


def test_mask_words_do_not_erase_between_columns_and_protect_money():
    words = [
        [0.1, 0.1, 0.2, 0.12, "Cliente"],
        [0.75, 0.1, 0.9, 0.12, "Teste"],
        [0.3, 0.3, 0.35, 0.32, "R$"],
        [0.36, 0.3, 0.45, 0.32, "1.000,00"],
    ]
    masks, uncertain = validate_masks(
        {
            "text_entities": [
                {"value": "Cliente Teste", "bbox": [100, 100, 900, 120], "category": "person_name"}
            ],
            "visual_regions": [],
            "uncertain": False,
        },
        words,
    )
    assert len(masks) == 2 and not uncertain
    assert not economic_overlap(words, masks)
    assert economic_overlap(words, [{"rect": [0.25, 0.25, 0.5, 0.35]}])
    with pytest.raises(ValueError):
        validate_masks({"uncertain": False}, words)
    with pytest.raises(ValueError):
        validate_masks(
            {
                "text_entities": [
                    {"value": "Cliente", "bbox": [0, 0, 1001, 10], "category": "person_name"}
                ],
                "visual_regions": [],
                "uncertain": False,
            },
            words,
        )


def test_automatic_masks_do_not_forge_manual_confirmation():
    im = Image.new("RGB", (100, 100), "black")
    masks = [{"rect": [0.1, 0.1, 0.2, 0.2], "category": "signature", "origin": "automatic"}]
    with pytest.raises(ValueError, match="confirmacion manual"):
        redact_pixels(im, masks)
    cleaned, _ = redact_pixels(im, masks, decision_mode="automatic")
    assert cleaned.getpixel((15, 15)) == (255, 255, 255)
    assert cleaned.getpixel((30, 30)) == (0, 0, 0)


@pytest.fixture
def auto_source(tmp_path):
    root = tmp_path
    (root / "state").mkdir()
    source = root / "source.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((30, 30), "Indice")
        p = pdf.new_page()
        p.insert_text((30, 30), "TERMO DE ADESAO AO CARTAO DE CREDITO\nCPF 123.456.789-00")
        p.insert_text((30, 150), "Taxa: 2,00%. Limite: R$ 1.500,00. Prazo: 24 meses.")
        pdf.new_page().insert_text((30, 30), "CONTRATO DE EMPRESTIMO PESSOAL")
        pdf.set_toc([[1, "Card", 2], [1, "Loan", 3]])
        pdf.save(source)
    db = Database(root / "state/scraper.sqlite3")
    with db.connection:
        db.connection.execute(
            "INSERT INTO cases(case_id,process_number,process_number_digits,first_seen_at) "
            "VALUES ('case','0000001-59.2026.8.05.0001','00000015920268050001','now')"
        )
        db.connection.execute(
            "INSERT INTO documents(document_id,case_id,retrieved_at,relative_path,sha256,"
            "size_bytes,mime_type,page_count,validation_status) "
            "VALUES ('doc','case','now','source.pdf',?,?,'application/pdf',3,'valid')",
            (hash_file(source), source.stat().st_size),
        )
    initialize_automation_schema(db.connection)
    result = inventory_document(
        {
            "source_path": str(source),
            "folder": str(root / "contract-work/doc"),
            "document_id": "doc",
            "source_sha256": hash_file(source),
            "page_count": 3,
            "tessdata": "unused-no-ocr",
        }
    )
    save_inventory(db.connection, result)
    yield root, db.connection
    db.close()


def test_automatic_pipeline_writes_card_only_without_manual_ranges_and_is_idempotent(
    auto_source, monkeypatch
):
    root, db = auto_source

    def classify(_model, pages, _attachments):
        return {
            p["page_number"]: {
                "page": p["page_number"],
                "state": "card_contract" if p["page_number"] == 2 else "noncontract",
                "confidence": 0.99,
                "kind": "adhesion",
                "start": True,
            }
            for p in pages
        }

    def locate(_model, _image, page, _context):
        return {
            "state": "card_contract",
            "confidence": 0.99,
            "kind": "adhesion",
            "regions": [{"page": page["page_number"], "rect": [0, 0, 1, 0.94]}],
        }

    def anonymize(_model, _image, _cache, **_kwargs):
        return {
            "had_sensitive_data": True,
            "status": "automatic_checks_passed",
            "checks": {},
            "masks": [
                {"rect": [0.04, 0.034, 0.5, 0.065], "category": "cpf", "origin": "automatic"}
            ],
        }

    monkeypatch.setattr("datajud_scraper.card_automation.classify_text", classify)
    monkeypatch.setattr("datajud_scraper.card_automation.locate_regions", locate)
    monkeypatch.setattr("datajud_scraper.card_automation.anonymize_page", anonymize)
    before = (root / "source.pdf").read_bytes()
    result = run_document(root, "doc", Model())
    assert result["status"] == "automatic_checks_passed"
    assert len(result["contracts"]) == 1
    assert result["contracts"][0]["regions"][0]["page"] == 2
    output = Path(result["contracts"][0]["output"]["path"])
    with pymupdf.open(output) as pdf:
        assert pdf.page_count == 1 and not pdf[0].get_text()
    assert (root / "source.pdf").read_bytes() == before
    assert db.execute("SELECT COUNT(*) FROM contract_page_reviews").fetchone()[0] == 0
    row = db.execute("SELECT * FROM documents WHERE document_id='doc'").fetchone()
    assert row["has_card_contract"] == 1 and row["card_contract_count"] == 1
    assert row["has_contract"] is None and row["source_disposition"] == "retained"
    assert run_document(root, "doc", Model()) == json.loads(json.dumps(result))
    output.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="modificada"):
        run_document(root, "doc", Model())


def test_failed_inference_does_not_turn_into_negative_or_purge(auto_source, monkeypatch):
    root, db = auto_source

    def fail(*_):
        raise ValueError("incomplete response")

    monkeypatch.setattr("datajud_scraper.card_automation.classify_text", fail)
    with pytest.raises(ValueError, match="incomplete"):
        run_document(root, "doc", Model())
    row = db.execute("SELECT * FROM documents WHERE document_id='doc'").fetchone()
    assert row["has_card_contract"] is None and row["card_automation_status"] == "error"
    assert (root / "source.pdf").exists()


def test_configuration_captures_scope_model_and_implementation():
    config = configuration(Model(), 240)
    assert config["scope"] == "credit_card_contractual_documents_only"
    assert config["model_digest"] == Model.digest
    assert len(config["implementation_sha256"]) == 64
    assert "manual" not in json.dumps(config)
