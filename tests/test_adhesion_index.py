from __future__ import annotations

import gzip
import json
from contextlib import contextmanager

import pytest

from datajud_scraper.adhesion.boundaries import (
    independent_title,
    instrument_evidence,
    printed_counter,
    resolve_roles,
)
from datajud_scraper.adhesion.common import read_json, workspace, write_json
from datajud_scraper.adhesion.inventory import open_inventory
from datajud_scraper.adhesion.pdf import region_inventory, render_contract_page
from datajud_scraper.adhesion.routing import candidate_numbers, plan_candidates, title_signals
from datajud_scraper.adhesion.vision import classify_pages, detect_with_model
from datajud_scraper.pdf_validation import hash_file

pymupdf = pytest.importorskip("pymupdf")


def page(number, text, counter=None):
    result = {
        "page_number": number,
        "width": 120,
        "height": 100,
        "rotation": 0,
        "words": [[5, 5, 115, 10, text]],
        "text_method": "native",
        "image_rects": [],
        "image_fraction": 0,
        "ocr_error": None,
    }
    if counter:
        result["words"].append([80, 90, 110, 94, counter])
    return result


def attachment(start, end, title="001.pdf", **kwargs):
    return {"start_page": start, "end_page": end, "title": title, **kwargs}


@contextmanager
def source_pdf(pages):
    with pymupdf.open() as pdf:
        for current in pages:
            pdf.new_page(width=120, height=100).insert_text(
                (5, 20), current["words"][0][4], fontsize=5
            )
        yield pdf


class Model:
    def __init__(self, roles, *, kind="personalized", revised=None):
        self.roles, self.kind, self.revised = roles, kind, revised or roles
        self.calls = []

    def ask(self, task, system, content, schema, images=()):
        self.calls.append((task, content))
        if task in ("adhesion_candidate", "adhesion_boundary_recheck_v1"):
            data = json.loads(content)
            roles = self.revised if task == "adhesion_boundary_recheck_v1" else self.roles
            return {"roles": [roles[p["page"]] for p in data]}
        if task == "adhesion_instrument_scope_v1":
            return {
                "kind": self.kind,
                "card_adhesion": self.kind != "not_target",
                "complete_start": self.kind not in ("not_target", "uncertain"),
                "foreign_content": False,
            }
        if task == "adhesion_instrument_end_v1":
            return {"complete_end": True, "foreign_content": False, "uncertain": False}
        if task == "adhesion_duplicate_identity_v1":
            return {"number": ""}
        if task == "adhesion_region_selection_v1":
            return {
                "region_id": 1,
                "contains_complete_page": True,
                "foreign_content": False,
                "uncertain": False,
            }
        if task == "adhesion_crop_verification_v1":
            return {"preserved": True, "clean_region": True, "uncertain": False}
        if task == "adhesion_cropped_page_role_v1":
            return {"role": "B"}
        raise AssertionError(task)


def test_title_normalization_avoids_bank_substring_and_handles_compact_titles():
    assert title_signals("Documentos_Bradesco.pdf")["score"] < 4
    assert title_signals("TermoDeAdesaoAoCartaoBMG.pdf")["score"] >= 4
    assert title_signals("ALTERACAO CONTRATUAL - CONTRATO SOCIAL.pdf")["score"] < 4
    assert title_signals("CONTRATO + BIOMETRIA.pdf")["score"] >= 4
    assert title_signals("RELATORIO DE BIOMETRIA.pdf")["score"] < 4


def test_probe_access_is_limited_to_two_pages_per_attachment():
    class Pages:
        visited = []

        def __len__(self):
            return 100

        def __getitem__(self, index):
            self.visited.append(index + 1)
            return page(index + 1, "Certidao")

    pages = Pages()
    plan = plan_candidates(pages, [attachment(1, 50), attachment(51, 100, "CONTRATO.pdf")])
    assert plan["probe_pages"] == [1, 2, 51, 52]
    assert pages.visited == [1, 2, 51, 52]
    assert [a["start_page"] for a in plan["groups"]] == [51]


def test_invalid_index_does_not_hide_uncovered_pages():
    pages = [page(n, "Certidao") for n in range(1, 6)]
    plan = plan_candidates(pages, [attachment(2, 4, "CONTRATO.pdf")])
    assert not plan["index_valid"]
    assert plan["attachments"] == [attachment(1, 5, "", boundary_ambiguity=True)]


def test_source_without_bookmarks_or_links_uses_content_fallback():
    from datajud_scraper.adhesion.text import describe_attachments

    pages = [page(n, "Certidao") for n in range(1, 4)]
    pages[2] = page(3, "TERMO DE ADESAO AO CARTAO")
    with source_pdf(pages) as pdf:
        attachments = describe_attachments(pdf)
    plan = plan_candidates(pages, attachments)
    assert not plan["index_valid"]
    assert not plan["groups"]
    assert candidate_numbers(pages, plan["fallback_attachments"]) == [1, 2, 3]


def test_links_without_bookmarks_identify_the_index_prefix():
    from datajud_scraper.adhesion.text import describe_attachments

    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.new_page()
        pdf.new_page()
        pdf[0].insert_text((165, 300), "CONTRATO.pdf", fontsize=10)
        pdf[0].insert_link(
            {"kind": pymupdf.LINK_GOTO, "page": 1, "from": pymupdf.Rect(164, 292, 260, 301)}
        )
        with pymupdf.open(stream=pdf.tobytes(), filetype="pdf") as reopened:
            attachments = describe_attachments(reopened)
    assert attachments[0]["index_prefix"]
    assert attachments[1]["title"] == "CONTRATO.pdf"
    assert attachments[1]["start_page"] == 2


def test_index_prefix_is_excluded_without_collapsing_residual_attachment_ranges():
    pages = [page(n, "Certidao") for n in range(1, 9)]
    pages[0] = page(1, "TERMO DE ADESAO AO CARTAO listado no indice")
    pages[5] = page(6, "TERMO DE ADESAO AO CARTAO")
    attachments = [
        attachment(1, 2, "Indice", index_prefix=True),
        attachment(3, 4, "Peticao"),
        attachment(5, 8, "Documentos"),
    ]
    plan = plan_candidates(pages, attachments)
    assert plan["index_valid"]
    assert plan["probe_pages"] == [3, 4, 5, 6]
    assert candidate_numbers(pages, plan["fallback_attachments"]) == [5, 6, 7, 8]


@pytest.mark.parametrize("same,uncertain", [(True, False), (False, False), (True, True)])
def test_cross_annex_join_requires_explicit_visual_confirmation(same, uncertain):
    pages = [page(1, "TERMO DE ADESAO AO CARTAO", "1/2"), page(2, "Clausulas finais", "2/2")]

    class CrossModel(Model):
        def ask(self, task, system, content, schema, images=()):
            if task == "adhesion_cross_annex_v1":
                return {"same_instrument": same, "uncertain": uncertain, "roles": ["A", "E"]}
            return super().ask(task, system, content, schema, images)

    with source_pdf(pages) as pdf:
        result = detect_with_model(
            CrossModel({1: "A", 2: "E"}),
            pdf,
            pages,
            [attachment(1, 1, "CONTRATO parte 1"), attachment(2, 2, "CONTRATO parte 2")],
        )
    if same and not uncertain:
        assert result["instruments"][0]["pages"] == [1, 2]
        assert result["verified_crossings"] == [2]
        assert not result["unresolved"]
    else:
        assert not result["instruments"]
        assert result["unresolved"]


def test_scope_reviews_first_and_last_individually_and_rejects_malformed_reply():
    pages = [page(1, "TERMO DE ADESAO AO CARTAO"), page(2, "Clausulas finais e SAC")]

    class Malformed(Model):
        def ask(self, task, system, content, schema, images=()):
            answer = super().ask(task, system, content, schema, images)
            if task.startswith("adhesion_instrument_"):
                assert len(images) == 1
            if task == "adhesion_instrument_scope_v1":
                return {**answer, "complete_start": "true"}
            return answer

    with source_pdf(pages) as pdf, pytest.raises(ValueError, match="scope confirmation"):
        detect_with_model(Malformed({1: "A", 2: "E"}), pdf, pages, [attachment(1, 2)])


def test_fallback_discovers_second_term_inside_opaque_attachment_after_success():
    pages = [page(n, "Certidao") for n in range(1, 9)]
    pages[0] = page(1, "TERMO DE ADESAO AO CARTAO credito proposta 111")
    pages[5] = page(6, "TERMO DE ADESAO AO CARTAO credito proposta 222")
    model = Model({1: "B", 2: "N", 3: "N", 4: "N", 5: "N", 6: "B", 7: "N", 8: "N"})
    with source_pdf(pages) as pdf:
        result = detect_with_model(
            model, pdf, pages, [attachment(1, 2, "Termo de adesao"), attachment(3, 8)]
        )
    assert [i["pages"] for i in result["instruments"]] == [[1], [6]]
    assert result["primary_candidate_pages"] == 2
    assert result["fallback_candidate_pages"] == 6
    calls = [json.loads(content) for task, content in model.calls if task == "adhesion_candidate"]
    seen = [p["page"] for batch in calls for p in batch]
    assert len(seen) == len(set(seen)) == 8
    assert result["fallback_complete"]


def test_visual_classification_reads_only_requested_pages_and_neighbours():
    class Pages:
        visited = []

        def __len__(self):
            return 100

        def __getitem__(self, index):
            self.visited.append(index + 1)
            return page(index + 1, "Certidao")

    pages = Pages()
    with source_pdf([page(n, "Certidao") for n in range(1, 101)]) as pdf:
        classify_pages(Model({50: "N"}), pdf, pages, [50])
    assert set(pages.visited) == {49, 50, 51}


def test_card_payment_account_is_allowed_but_generic_account_package_is_not():
    assert instrument_evidence(
        page(1, "TERMO DE ADESAO AO CARTAO E ABERTURA DE CONTA DE PAGAMENTO")
    )
    assert (
        instrument_evidence(page(1, "PROPOSTA DE ABERTURA DE CONTA E ADESAO A PRODUTOS CARTAO"))
        is None
    )


def test_credit_note_reference_in_clause_does_not_override_continuation():
    pages = [
        page(1, "TERMO DE ADESAO AO CARTAO"),
        page(2, "2. O limite podera ser representado por cedula de credito bancario"),
        page(3, "Assinatura e SAC"),
    ]
    assert not independent_title(pages[1])
    assert independent_title(page(1, "CEDULA DE CREDITO BANCARIO"))
    roles, _ = resolve_roles(pages, {1: "A", 2: "C", 3: "E"})
    assert roles[2] == "C"


def test_boundary_recheck_keeps_integral_benefits_after_inconsistent_counter():
    pages = [page(n, "TERMO DE ADESAO AO CARTAO BENEFICIO", f"{n}/4") for n in range(1, 7)]
    pages[4] = page(5, "ANEXO DE BENEFICIOS DO CARTAO", "5/4")
    pages[5] = page(6, "BENEFICIOS DO CARTAO", "6/4")
    assert printed_counter(pages[5]) == (6, 4)
    model = Model(
        {1: "A", 2: "C", 3: "C", 4: "E", 5: "N", 6: "N"},
        revised={1: "A", 2: "C", 3: "C", 4: "C", 5: "C", 6: "E"},
    )
    with source_pdf(pages) as pdf:
        result = detect_with_model(model, pdf, pages, [attachment(1, 6, "CONTRATO")])
    assert result["instruments"][0]["pages"] == list(range(1, 7))
    assert result["raw_page_roles"][4] == "E"
    assert any(
        c["reason"] == "adjacent_visual_boundary_recheck" for c in result["boundary_changes"]
    )


@pytest.mark.parametrize("kind", ["personalized", "blank_template", "not_target", "uncertain"])
def test_structured_scope_distinguishes_templates_negatives_and_uncertainty(kind):
    pages = [page(1, "TERMO DE ADESAO AO CARTAO")]
    with source_pdf(pages) as pdf:
        result = detect_with_model(Model({1: "B"}, kind=kind), pdf, pages, [attachment(1, 1)])
    assert bool(result["instruments"]) == (kind in ("personalized", "blank_template"))
    assert bool(result["unresolved"]) == (kind == "uncertain")
    assert bool(result["rejected"]) == (kind == "not_target")
    if result["instruments"]:
        assert result["instruments"][0]["kind"] == kind


def test_complete_embedded_form_gets_measured_region_and_preserves_source_role():
    pages = [page(1, "TERMO DE ADESAO AO CARTAO")]
    with source_pdf(pages) as pdf:
        pdf[0].draw_rect((5, 3, 115, 65))
        result = detect_with_model(Model({1: "F"}), pdf, pages, [attachment(1, 1)])
    assert result["raw_page_roles"][1] == "F"
    assert result["instruments"][0]["regions"][0]["rect"] != [0, 0, 1, 1]
    assert not result["unresolved"]


def test_contract_with_a_smaller_separate_id_image_requires_a_crop():
    pages = [page(1, "TERMO DE ADESAO AO CARTAO")]
    pages[0]["image_rects"] = [[5, 3, 115, 65], [5, 67, 55, 85]]
    with source_pdf(pages) as pdf:
        result = detect_with_model(Model({1: "B"}), pdf, pages, [attachment(1, 1)])
    assert result["instruments"][0]["regions"][0]["rect"] == pytest.approx(
        [5 / 120, 3 / 100, 115 / 120, 65 / 100]
    )


def test_duplicate_occurrences_keep_each_regions_provenance():
    pages = [page(n, "TERMO DE ADESAO AO CARTAO") for n in (1, 2)]
    with source_pdf(pages) as pdf:
        result = detect_with_model(Model({1: "B", 2: "B"}), pdf, pages, [attachment(1, 2)])
    assert len(result["instruments"]) == 1
    target = result["instruments"][0]
    assert target["occurrences"] == [[1], [2]]
    assert len(target["occurrence_regions"]) == 2
    assert [r[0]["page"] for r in target["occurrence_regions"]] == [1, 2]


def pdf_source(root, count=4):
    path = root / "pdfs/source.pdf"
    path.parent.mkdir()
    with source_pdf([page(n, "TERMO DE ADESAO AO CARTAO") for n in range(1, count + 1)]) as pdf:
        pdf.save(path)
    return {
        "document_id": "synthetic",
        "relative_path": "pdfs/source.pdf",
        "sha256": hash_file(path),
        "page_count": count,
    }


def test_lazy_inventory_resumes_only_requested_pages_without_archive(tmp_path, monkeypatch):
    from datajud_scraper.adhesion import inventory

    source = pdf_source(tmp_path)
    real = inventory.page_inventory
    calls = []

    def extract(current, *args, **kwargs):
        calls.append(current.number + 1)
        return real(current, *args, **kwargs)

    monkeypatch.setattr(inventory, "page_inventory", extract)
    with open_inventory(tmp_path, source) as pages:
        assert pages[2]["page_number"] == 3
        assert pages[2]["text_method"] == "native"
        assert calls == [3]
    with open_inventory(tmp_path, source) as pages:
        assert pages[2]["page_number"] == 3
        assert pages.stats["cache_hits"] == 1
        assert calls == [3]
        assert pages[0]["page_number"] == 1
    assert calls == [3, 1]


def test_retry_keeps_existing_text_when_alternative_is_bad(tmp_path, monkeypatch):
    from datajud_scraper.adhesion import inventory

    source = pdf_source(tmp_path)
    with open_inventory(tmp_path, source) as pages:
        original = pages[0]
        monkeypatch.setattr(
            inventory,
            "page_inventory",
            lambda *args, **kwargs: {
                **original,
                "text_method": "ocr_failed",
                "words": [],
                "ocr_error": "failed",
            },
        )
        assert pages.retry(1)["ocr_error"] == "failed"
        assert pages[0] == original
        assert pages.stats["ocr_retries"] == 1


@pytest.mark.parametrize("compatible", [True, False])
def test_legacy_ocr_requires_verified_model_and_runtime_configuration(
    tmp_path, monkeypatch, compatible
):
    from datajud_scraper.adhesion import inventory
    from datajud_scraper.adhesion.text import DEFAULT_TESSDATA

    source = pdf_source(tmp_path, count=1)
    folder = workspace(tmp_path) / "inventory" / source["document_id"]
    profile = inventory.extraction_configuration()
    write_json(
        folder / "inventory.json",
        {
            "source_sha256": source["sha256"],
            "page_count": 1,
            "inventory_version": "2",
            "text_engine_version": "1",
            "tessdata": str(DEFAULT_TESSDATA),
            "ocr_configuration": {k: profile[k] for k in ("dpi", "pymupdf", "models")}
            if compatible
            else None,
        },
    )
    old = {**page(1, "ARCHIVED OCR"), "text": "ARCHIVED OCR", "text_method": "ocr"}
    with gzip.open(folder / "pages.jsonl.gz", "wt", encoding="utf8") as stream:
        stream.write(json.dumps(old) + "\n")

    def extract(current, tessdata, *, recognize=True, **kwargs):
        text = "FRESH OCR" if recognize else "native"
        return {
            **page(1, text),
            "text": text,
            "needs_ocr": True,
            "native_quality": [],
            "text_quality": [],
            "text_method": "ocr" if recognize else "native",
        }

    monkeypatch.setattr(inventory, "page_inventory", extract)
    with open_inventory(tmp_path, source) as pages:
        assert pages[0]["text"] == ("ARCHIVED OCR" if compatible else "FRESH OCR")
        assert pages.stats["ocr_pages"] == (0 if compatible else 1)


def test_page_cache_detects_wrong_source_binding(tmp_path):
    source = pdf_source(tmp_path)
    with open_inventory(tmp_path, source) as pages:
        pages[0]
        path = pages.folder / "00001-recognized.json"
    value = read_json(path)
    write_json(path, {**value, "source_sha256": "different"})
    with (
        open_inventory(tmp_path, source) as pages,
        pytest.raises(ValueError, match="source/configuration"),
    ):
        pages[0]


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_cropped_inventory_coordinates_match_rendered_mask_space(rotation):
    current = page(1, "TERMO DE ADESAO AO CARTAO")
    current["words"] = [[20, 30, 40, 40, "CPF"], [5, 80, 20, 90, "outside"]]
    region = {"page": 1, "rect": [0.1, 0.2, 0.8, 0.7], "rotation": rotation}
    with source_pdf([current]) as pdf:
        image, geometry = render_contract_page(pdf, region, dpi=180, with_geometry=True)
    cropped = region_inventory(current, region, geometry)
    assert cropped["width"] == image.width and cropped["height"] == image.height
    assert [w[4] for w in cropped["words"]] == ["CPF"]
    assert cropped["rotation"] == 0
    expected = {
        0: [20, 25, 70, 50],
        90: [75, 20, 100, 70],
        180: [140, 75, 190, 100],
        270: [25, 140, 50, 190],
    }
    assert cropped["words"][0][:4] == pytest.approx(expected[rotation])
    assert all(
        0 <= w[0] < w[2] <= image.width and 0 <= w[1] < w[3] <= image.height
        for w in cropped["words"]
    )


def test_rotated_source_image_bounds_are_kept_in_source_coordinate_space():
    from PIL import Image

    from datajud_scraper.adhesion.text import DEFAULT_TESSDATA, page_inventory

    with pymupdf.open() as pdf:
        current = pdf.new_page(width=100, height=200)
        import io

        stream = io.BytesIO()
        Image.new("RGB", (100, 200), "white").save(stream, format="PNG")
        current.insert_image(current.rect, stream=stream.getvalue())
        current.set_rotation(90)
        result = page_inventory(current, DEFAULT_TESSDATA, recognize=False)
    assert result["width"] == 200 and result["height"] == 100
    assert result["image_rects"] == [[0, 0, 100, 200]]
    assert result["image_fraction"] == 1.0


def test_pipeline_cleans_the_crop_in_the_same_coordinate_space(tmp_path, monkeypatch):
    from datajud_scraper.adhesion import pipeline
    from datajud_scraper.adhesion.inventory import words_normalized
    from datajud_scraper.adhesion.pdf import pixel_box

    source = pdf_source(tmp_path, count=1)
    with pymupdf.open(tmp_path / source["relative_path"]) as original:
        pdf = pymupdf.open()
        pdf.insert_pdf(original)
    pdf[0].insert_text((30, 45), "CPFTEST", fontsize=6)
    pdf[0].insert_text((5, 90), "OUTSIDE", fontsize=6)
    pdf.save(tmp_path / "pdfs/crop.pdf")
    pdf.close()
    source = {
        **source,
        "relative_path": "pdfs/crop.pdf",
        "sha256": hash_file(tmp_path / "pdfs/crop.pdf"),
    }
    write_json(workspace(tmp_path) / "holdout.json", {"documents": []})
    region = {"page": 1, "rect": [0.1, 0.3, 0.8, 0.7], "rotation": 90}
    monkeypatch.setattr(
        pipeline,
        "detect_with_model",
        lambda *args: {
            "instruments": [{"pages": [1], "family": "generic", "regions": [region]}],
            "unresolved": [],
            "fallback_complete": True,
        },
    )
    captured = []

    def clean(model, image, current, folder):
        assert current["width"] == image.width and current["height"] == image.height
        words = words_normalized(current)
        assert [w[4] for w in words] == ["CPFTEST"]
        mask = {"rect": words[0][:4], "category": "personal", "origin": "automatic"}
        captured.append(pixel_box(mask["rect"], image.width, image.height))
        return {"status": "completed", "masks": [mask]}

    monkeypatch.setattr(pipeline, "anonymize", clean)
    result = pipeline.run_document(tmp_path, source, None, {"version": "cropped-test"})
    record = result["instruments"][0]
    assert record["privacy"][0]["source_region"] == region
    assert tuple(record["output"]["pages"][0]["mask_pixel_boxes"][0]) == captured[0]
    with pymupdf.open(record["output"]["path"]) as output:
        pix = pymupdf.Pixmap(output, output[0].get_images()[0][0])
        from PIL import Image

        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        assert image.crop(captured[0]).getextrema() == ((255, 255), (255, 255), (255, 255))
    assert hash_file(tmp_path / source["relative_path"]) == source["sha256"]
