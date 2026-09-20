from datajud_scraper.card_assembly import (
    assemble_occurrences,
    ground_excerpt_regions,
    printed_page_counter,
)


def test_document_numbering_keeps_ccb_conditions_with_their_first_pages():
    pages = [
        {
            "page_number": n,
            "attachment_position": 1,
            "width": 600,
            "height": 800,
            "words": [[500, 720, 540, 730, f"{n}/4"]],
        }
        for n in range(1, 5)
    ]
    predictions = {
        n: {
            "state": "card_contract",
            "kind": "card_operation" if n < 3 else "general_conditions",
            "start": n in (1, 3),
            "regions": [{"page": n, "rect": [0, 0, 1, 0.95]}],
        }
        for n in range(1, 5)
    }
    groups = assemble_occurrences(predictions, pages)
    assert len(groups) == 1
    assert groups[0]["kind"] == "card_operation"
    assert [r["page"] for r in groups[0]["regions"]] == [1, 2, 3, 4]


def test_distinct_instruments_on_one_page_are_not_merged():
    pages = [{"page_number": 4, "attachment_position": 1}]
    regions = [
        {"page": 4, "rect": [0.1, y, 0.9, y + 0.1], "document": group}
        for y, group in [(0.1, 1), (0.25, 1), (0.5, 2), (0.65, 2)]
    ]
    groups = assemble_occurrences(
        {
            4: {
                "state": "card_excerpt",
                "kind": "card_excerpt",
                "regions": regions,
            }
        },
        pages,
    )
    assert len(groups) == 2
    assert [len(g["regions"]) for g in groups] == [2, 2]


def test_integral_conditions_do_not_split_a_contract_without_a_new_start():
    pages = [
        {"page_number": n, "attachment_position": 1, "width": 600, "height": 800} for n in (1, 2)
    ]
    predictions = {
        n: {
            "state": "card_contract",
            "kind": "adhesion" if n == 1 else "general_conditions",
            "start": n == 1,
            "regions": [{"page": n, "rect": [0, 0, 1, 0.95]}],
        }
        for n in (1, 2)
    }
    result = assemble_occurrences(predictions, pages)
    assert len(result) == 1
    assert [r["page"] for r in result[0]["regions"]] == [1, 2]


def test_ungrounded_visual_counter_cannot_merge_distinct_instruments():
    pages = [
        {"page_number": n, "attachment_position": 1, "width": 600, "height": 800} for n in (1, 2)
    ]
    predictions = {
        n: {
            "state": "card_contract",
            "kind": "adhesion",
            "start": True,
            "printed_counter": [n, 2],
            "regions": [{"page": n, "rect": [0, 0, 1, 0.95]}],
        }
        for n in (1, 2)
    }
    assert len(assemble_occurrences(predictions, pages)) == 2


def test_embedded_images_separate_contract_parts_from_pleading_captions():
    first = [0.25, 0.2, 0.8, 0.3]
    second = [0.25, 0.35, 0.8, 0.45]
    prediction = {
        "state": "card_excerpt",
        "regions": [{"page": 4, "rect": [0.2, 0.18, 0.85, 0.48], "document": 1}],
    }
    images = [
        [0.24, 0.19, 0.81, 0.31],
        first,
        [0.24, 0.34, 0.81, 0.46],
        second,
        [0.2, 0.05, 0.3, 0.1],  # unrelated pleading logo
    ]
    regions = ground_excerpt_regions(prediction, images)["regions"]
    assert [r["rect"] for r in regions] == [first, second]
    assert all(r["document"] == 1 for r in regions)


def test_new_printed_first_page_starts_another_adhesion_even_if_model_misses_start():
    pages = [
        {
            "page_number": n,
            "attachment_position": 1,
            "width": 600,
            "height": 800,
            "words": [[500, 720, 540, 730, "1/1"]],
        }
        for n in (1, 2)
    ]
    predictions = {
        n: {
            "state": "card_contract",
            "kind": "adhesion",
            "start": False,
            "regions": [{"page": n, "rect": [0, 0, 1, 0.95]}],
        }
        for n in (1, 2)
    }
    assert len(assemble_occurrences(predictions, pages)) == 2


def test_dates_and_ratios_in_contract_body_are_not_page_counters():
    page = {
        "width": 600,
        "height": 800,
        "words": [
            [500, 720, 580, 730, "04/04/2025"],
            [500, 100, 540, 130, "1/2"],
            [40, 750, 90, 770, "20/04"],
        ],
    }
    assert printed_page_counter(page) is None


def test_embedded_object_pass_recovers_missing_signature_fragment_without_manual_boxes():
    from PIL import Image

    from datajud_scraper.card_detection import complete_excerpt_images

    first, signature, logo = [0.2, 0.2, 0.8, 0.3], [0.2, 0.4, 0.8, 0.5], [0.1, 0.05, 0.3, 0.1]

    class Model:
        def ask(self, _task, _prompt, _content, schema, images):
            assert len(images) == 3  # Only the actual PDF objects, no index-shifting context.
            assert schema["properties"]["images"]["minItems"] == 3
            assert schema["properties"]["images"]["items"]["properties"]["image"]["enum"] == [
                1,
                2,
                3,
            ]
            return {
                "images": [
                    {"image": 1, "include": True, "document": 1, "rotation": 0},
                    {"image": 2, "include": True, "document": 1, "rotation": 0},
                    {"image": 3, "include": False, "document": 0, "rotation": 0},
                ],
                "uncertain": False,
            }

    prediction = {
        "state": "card_excerpt",
        "confidence": 0.95,
        "regions": [{"page": 1, "rect": first, "document": 1}],
    }
    result = complete_excerpt_images(
        Model(),
        Image.new("RGB", (600, 800)),
        prediction,
        [first, signature, logo],
        {"page_number": 1, "height": 800, "words": []},
    )
    assert [r["rect"] for r in result["regions"]] == [first, signature]
    assert {r["document"] for r in result["regions"]} == {1}
    assert not result["excerpt_objects_unresolved"]


def test_incomplete_embedded_object_response_cannot_pass_as_confident_extraction():
    from PIL import Image

    from datajud_scraper.card_detection import complete_excerpt_images

    class Model:
        def ask(self, *_):
            return {"images": [], "uncertain": False}

    prediction = {
        "state": "card_excerpt",
        "confidence": 0.95,
        "regions": [{"page": 1, "rect": [0.2, 0.2, 0.8, 0.3], "document": 1}],
    }
    result = complete_excerpt_images(
        Model(),
        Image.new("RGB", (600, 800)),
        prediction,
        [[0.2, 0.4, 0.8, 0.5]],
        {"page_number": 1, "height": 800, "words": []},
    )
    assert result["confidence"] == 0.0 and result["excerpt_objects_unresolved"]
    assert result["regions"] == prediction["regions"]


def test_embedded_contract_signature_is_recovered_from_a_full_page_negative():
    from PIL import Image

    from datajud_scraper.card_detection import complete_excerpt_images

    class Model:
        def ask(self, *_):
            return {
                "images": [{"image": 1, "include": True, "document": 1, "rotation": 0}],
                "uncertain": False,
            }

    prediction = {"state": "noncontract", "kind": "not_applicable", "confidence": 0.98}
    page = {
        "page_number": 7,
        "height": 800,
        "words": [],
        "text": "A parte autora assinou a cedula de credito do cartao, reproduzida abaixo.",
    }
    result = complete_excerpt_images(
        Model(), Image.new("RGB", (600, 800)), prediction, [[0.2, 0.2, 0.8, 0.3]], page
    )
    assert result["state"] == "card_excerpt"
    assert result["regions"][0]["page"] == 7


def test_ccb_general_conditions_continue_the_previous_card_operation():
    from datajud_scraper.card_detection import contract_continuations

    pages = [
        {
            "page_number": n,
            "attachment_position": 1,
            "height": 800,
            "width": 600,
            "words": [],
            "text": text,
        }
        for n, text in [
            (1, "Cédula de crédito bancário - saque do cartão"),
            (2, "CONDIÇÕES GERAIS DA CÉDULA DE CRÉDITO BANCÁRIO"),
        ]
    ]
    predictions = {
        n: {
            "state": "card_contract",
            "kind": "card_operation",
            "start": True,
            "regions": [{"page": n, "rect": [0, 0, 1, 0.95]}],
        }
        for n in (1, 2)
    }
    contract_continuations(predictions, pages)
    assert predictions[2]["integral_annex"]
    assert len(assemble_occurrences(predictions, pages)) == 1


def test_short_contact_continuation_is_kept_without_promoting_a_transfer_receipt():
    from datajud_scraper.card_detection import contract_continuations

    pages = [
        {
            "page_number": n,
            "attachment_position": 1,
            "height": 800,
            "width": 600,
            "words": [],
            "text": text,
        }
        for n, text in [
            (1, "Cédula de crédito bancário - saque através do cartão"),
            (2, "Se sua reclamação não foi satisfatória, procure a OUVIDORIA 0800 123 4567"),
            (3, "COMPROVANTE DE TED. SAC 0800 123 4567"),
        ]
    ]
    predictions = {
        1: {
            "state": "card_contract",
            "kind": "card_operation",
            "start": True,
            "regions": [{"page": 1, "rect": [0, 0, 1, 0.95]}],
        },
        2: {"state": "noncontract", "kind": "not_applicable", "regions": []},
        3: {"state": "noncontract", "kind": "not_applicable", "regions": []},
    }
    contract_continuations(predictions, pages)
    assert predictions[2]["state"] == "card_contract"
    assert predictions[3]["state"] == "noncontract"
    assert [r["page"] for r in assemble_occurrences(predictions, pages)[0]["regions"]] == [1, 2]


def test_native_contract_excerpt_uses_pdf_lines_and_excludes_surrounding_pleading():
    import json

    import pymupdf

    from datajud_scraper.card_detection import ground_native_excerpt_regions

    class Model:
        def ask(self, task, _prompt, content, _schema):
            assert task == "native_excerpt_lines"
            lines = json.loads(content)["lines"]
            return {
                "selections": [{"line_ids": [v["id"] for v in lines[:3]], "document": 1}],
                "uncertain": False,
            }

    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        for y, text in [
            (100, "Atendente: Contratacao do cartao sem anuidade."),
            (125, "Atendente: Confirma essas condicoes?"),
            (150, "Cliente: Confirmo."),
            (190, "20. A prova demonstra o alegado na contestacao."),
        ]:
            page.insert_text((150, y), text)
        prediction = {
            "state": "card_excerpt",
            "confidence": 0.95,
            "regions": [{"page": 5, "rect": [0.2, 0.13, 0.65, 0.25], "document": 1}],
        }
        result = ground_native_excerpt_regions(Model(), prediction, page)
        assert not result["native_excerpt_unresolved"]
        region = result["regions"][0]
        rect = pymupdf.Rect(
            [v * (600 if i % 2 == 0 else 800) for i, v in enumerate(region["rect"])]
        )
        extracted = page.get_text(clip=rect)
        assert "Atendente: Contratacao do cartao sem anuidade." in extracted
        assert "Cliente: Confirmo." in extracted
        assert "contestacao" not in extracted
        assert region["boundary_basis"] == "pdf_text_lines"


def test_native_excerpt_rejects_invented_line_ids():
    import pymupdf

    from datajud_scraper.card_detection import ground_native_excerpt_regions

    class Model:
        def ask(self, *_):
            return {"selections": [{"line_ids": [99], "document": 1}], "uncertain": False}

    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 50), "Atendente: Confirma o cartao?")
        prediction = {
            "state": "card_excerpt",
            "confidence": 0.95,
            "regions": [{"page": 1, "rect": [0, 0, 1, 0.1]}],
        }
        result = ground_native_excerpt_regions(Model(), prediction, page)
    assert result["native_excerpt_unresolved"]
    assert result["confidence"] == 0
    assert result["regions"] == prediction["regions"]
