from __future__ import annotations

import random
import sqlite3
from pathlib import Path

import pytest

from datajud_scraper.adhesion.archive import archive, restore, verify_archive
from datajud_scraper.adhesion.common import read_json
from datajud_scraper.adhesion.evaluation import score_document
from datajud_scraper.adhesion.inventory import words_normalized
from datajud_scraper.adhesion.prepare import freeze_split
from datajud_scraper.adhesion.privacy import (
    build_regions,
    in_rect,
    protect_masks,
    protected_cells,
    selected_masks,
)
from datajud_scraper.adhesion.vision import (
    assemble_roles,
    candidate_numbers,
    instrument_evidence,
    repair_numbered_continuations,
)


def page(number, text):
    return {
        "page_number": number,
        "width": 100,
        "height": 100,
        "words": [[5, 5, 90, 10, text]],
        "text_method": "native",
    }


def test_uniform_independent_sampling_is_frozen_before_labels():
    rows = [
        {"document_id": str(n), "case_id": str(n), "sha256": str(n), "available": True}
        for n in range(100)
    ]
    test, development = freeze_split(rows, {"0", "1"})
    expected = random.Random(20260920).sample(sorted(rows[2:], key=lambda r: r["document_id"]), 25)
    assert test == expected
    assert len(development) == 30
    assert not {r["document_id"] for r in test} & {r["document_id"] for r in development}
    assert all(r["document_id"] not in ("0", "1") for r in test + development)


def test_insufficient_independent_sources_does_not_shrink_holdout():
    with pytest.raises(ValueError, match="not enough"):
        freeze_split([], set())


def test_separate_documents_in_one_bundle_and_repeated_header():
    pages = [page(n, "TERMO DE ADESAO CARTAO PAN") for n in range(1, 11)]
    result = assemble_roles(
        pages, {1: "A", 2: "C", 3: "E", 4: "N", 5: "N", 6: "F", 7: "A", 8: "C", 9: "E", 10: "N"}
    )
    assert len(result["instruments"]) == 1
    assert result["instruments"][0]["occurrences"] == [[1, 2, 3], [7, 8, 9]]
    assert result["fragments"] == [6]
    assert not result["unresolved"]


def test_incomplete_or_orphan_pages_are_not_exported_as_complete():
    result = assemble_roles(
        [page(n, "clausula cartao") for n in range(1, 5)], {1: "C", 2: "A", 3: "U", 4: "N"}
    )
    assert not result["instruments"]
    assert len(result["unresolved"]) == 3


def test_distinct_adhesions_in_one_annex_remain_separate_while_copies_merge():
    first = ["TERMO DE ADESAO CARTAO BMG proposta 111", "Clausulas limite R$ 500 assinatura"]
    second = ["TERMO DE ADESAO CARTAO BMG proposta 222", "Clausulas limite R$ 900 assinatura"]
    contents = first + ["Consentimento independente"] + second + ["Comprovante"] + first
    result = assemble_roles(
        [page(n, text) for n, text in enumerate(contents, 1)],
        {1: "A", 2: "E", 3: "N", 4: "A", 5: "E", 6: "N", 7: "A", 8: "E"},
    )
    assert [i["occurrences"] for i in result["instruments"]] == [
        [[1, 2], [7, 8]],
        [[4, 5]],
    ]
    assert not result["unresolved"]


@pytest.mark.parametrize(
    "title",
    [
        "TERMO DE ADESAO AO CARTAO",
        "PROPOSTA DE EMISSAO DE CARTAO",
        "CONTRATO DE ADESAO CARTAO BENEFICIO",
        "SOLICITO A EMISSAO DO CARTAO DE CREDITO",
        "O TITULAR ADERE AO REGULAMENTO DO CARTAO",
    ],
)
def test_equivalent_titles_route_even_with_unhelpful_attachment_name(title):
    assert candidate_numbers(
        [page(1, title)], [{"start_page": 1, "end_page": 1, "title": "001.pdf"}]
    ) == [1]


def test_personal_block_stops_before_declaration_and_economic_terms():
    words = [
        [0.05, 0.10, 0.4, 0.12, "DADOS PESSOAIS"],
        [0.05, 0.14, 0.7, 0.16, "Nome: Pessoa Sintetica"],
        [0.05, 0.18, 0.7, 0.20, "CPF: 111.222.333-44"],
        [0.05, 0.23, 0.95, 0.25, "Declaro aceitar as condicoes do cartao"],
        [0.05, 0.28, 0.70, 0.30, "Taxa de juros: 2,00%"],
    ]
    regions = build_regions(words)
    block = next(r for r in regions if r["kind"] == "block")
    assert block["rect"][3] < 0.23
    masks = protect_masks(
        [{"rect": [0, 0, 1, 1], "category": "personal_block", "origin": "automatic"}],
        protected_cells(regions),
    )
    assert not any(in_rect(words[-1], m["rect"]) for m in masks)
    assert any(in_rect(words[1], m["rect"]) for m in masks)


def test_corporate_address_and_execution_statement_are_protected():
    regions = build_regions(
        [
            [0.1, 0.1, 0.8, 0.12, "EMPRESA COMERCIAL LTDA"],
            [0.1, 0.14, 0.8, 0.16, "Endereco: Rua Comercial 100"],
            [0.1, 0.18, 0.8, 0.20, "CNPJ: 11.222.333/0001-44"],
            [0.1, 0.3, 0.9, 0.32, "Este documento foi assinado eletronicamente em 01/01/2025"],
        ]
    )
    protected = protected_cells(regions)
    assert any("Endereco" in r["text"] for r in protected)
    assert any("CNPJ" in r["text"] for r in protected)
    assert any("assinado" in r["text"] for r in protected)


def test_invented_region_ids_are_rejected():
    with pytest.raises(ValueError, match="nonexistent"):
        selected_masks([999], [])


@pytest.fixture
def legacy_root(tmp_path):
    (tmp_path / "state").mkdir()
    with sqlite3.connect(tmp_path / "state/scraper.sqlite3") as connection:
        connection.execute("CREATE TABLE provenance (value TEXT)")
        connection.execute("INSERT INTO provenance VALUES ('source preserved')")
    for relative in ("contracts", "card-work", "pdfs"):
        (tmp_path / relative).mkdir()
        (tmp_path / relative / "fixture.bin").write_bytes(relative.encode())
    return tmp_path


def test_verified_backup_restores_without_touching_originals(legacy_root):
    result = archive(legacy_root)
    backup = Path(result["path"])
    assert result["status"] == "verified"
    assert not (legacy_root / "contracts").exists()
    assert (legacy_root / "pdfs/fixture.bin").read_bytes() == b"pdfs"
    assert verify_archive(backup)["database_sha256"]
    restore(legacy_root, backup)
    assert (legacy_root / "contracts/fixture.bin").read_bytes() == b"contracts"
    with sqlite3.connect(legacy_root / "state/scraper.sqlite3") as connection:
        assert (
            connection.execute("SELECT value FROM provenance").fetchone()[0] == "source preserved"
        )


def test_archive_detects_tampering_and_restore_refuses_overwrite(legacy_root):
    result = archive(legacy_root)
    (legacy_root / "contracts").mkdir()
    with pytest.raises(ValueError, match="overwrite"):
        restore(legacy_root, result["path"])
    (Path(result["path"]) / "contracts/fixture.bin").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        verify_archive(result["path"])


def test_interrupted_archive_resumes_from_durable_journal(legacy_root, monkeypatch):
    rename = Path.rename
    attempts = 0

    def interrupt(path, destination):
        nonlocal attempts
        if path.name == "card-work" and attempts == 0:
            attempts += 1
            raise InterruptedError("simulated process interruption")
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", interrupt)
    destination = legacy_root / "backups/frozen"
    with pytest.raises(InterruptedError):
        archive(legacy_root, destination=destination)
    assert read_json(destination / "archive.json")["status"] == "moving"
    result = archive(legacy_root)
    assert result["path"] == str(destination)
    assert result["status"] == "verified"
    assert not (legacy_root / "contracts").exists()


def test_quarantine_and_missing_terms_reduce_useful_coverage():
    gold = {"instruments": [{"pages": [1]}, {"pages": [2]}, {"pages": [3]}]}
    run = {
        "status": "needs_review",
        "instruments": [
            {"contract_id": "a", "pages": [1], "status": "completed"},
            {"contract_id": "b", "pages": [2], "status": "needs_review"},
            {"contract_id": "c", "pages": [8], "status": "completed"},
        ],
    }
    review = {
        "instruments": [
            {"contract_id": k, "residual_pii_pages": [], "damaged_content_pages": []} for k in "abc"
        ]
    }
    result = score_document(gold, run, review)
    assert result["gold"] == 3 and result["useful"] == 1
    assert result["delivered"] == 2 and result["delivered_good"] == 1
    assert result["false_outputs"] == 1 and result["missed_or_incomplete"] == 1


def test_negative_processes_do_not_inflate_contract_denominators():
    result = score_document(
        {"instruments": []}, {"status": "no_target", "instruments": []}, {"instruments": []}
    )
    assert result["gold"] == result["useful"] == result["delivered"] == 0


def test_inconclusive_visual_review_never_counts_as_a_success():
    result = score_document(
        {"instruments": [{"pages": [1]}]},
        {
            "status": "completed",
            "instruments": [{"contract_id": "x", "pages": [1], "status": "completed"}],
        },
        {
            "instruments": [
                {
                    "contract_id": "x",
                    "residual_pii_pages": [],
                    "damaged_content_pages": [],
                    "uncertain": True,
                }
            ]
        },
    )
    assert result["useful"] == result["clean_outputs"] == result["preserved_outputs"] == 0
    assert result["delivered"] == result["uncertain_outputs"] == 1


def test_judicial_text_on_a_matching_contract_page_prevents_useful_success():
    result = score_document(
        {"instruments": [{"pages": [1]}]},
        {
            "status": "completed",
            "instruments": [{"contract_id": "x", "pages": [1], "status": "completed"}],
        },
        {
            "instruments": [
                {
                    "contract_id": "x",
                    "residual_pii_pages": [],
                    "damaged_content_pages": [],
                    "foreign_content_pages": [1],
                }
            ]
        },
    )
    assert result["exact_extractions"] == 1
    assert result["useful"] == 0


def test_economic_value_below_heading_and_execution_date_survive_personal_mask():
    words = [
        [0.7, 0.30, 0.83, 0.32, "Margem(%):"],
        [0.7, 0.33, 0.76, 0.35, "10,00"],
        [0.1, 0.6, 0.8, 0.63, "Local e Data: Cidade 01/01/2025"],
    ]
    protected = protected_cells(build_regions(words))
    masks = protect_masks(
        [{"rect": [0, 0, 1, 1], "origin": "automatic", "category": "personal_block"}], protected
    )
    assert not any(in_rect(words[1], m["rect"]) for m in masks)
    assert not any(in_rect(words[2], m["rect"]) for m in masks)


def test_pan_last_contact_page_is_retained_but_next_consent_is_excluded():
    pages = []
    for n in range(1, 8):
        heading = "TERMO DE ADESAO AO CARTAO CONSIGNADO PAN" if n < 7 else "TERMO DE CONSENTIMENTO"
        p = page(n, heading)
        p["words"].append([70, 90, 90, 93, f"Pagina {n} de 15"])
        if n == 6:
            p["words"].append([10, 30, 70, 33, "SAC Ouvidoria"])
        pages.append(p)
    roles = {1: "A", 2: "C", 3: "C", 4: "C", 5: "E", 6: "N", 7: "N"}
    repair_numbered_continuations(pages, roles)
    assert assemble_roles(pages, roles)["instruments"][0]["pages"] == list(range(1, 7))
    assert roles[7] == "N"


def test_native_text_boxes_follow_pdf_rotation():
    p = {"width": 200, "height": 100, "rotation": 90, "words": [[10, 20, 30, 40, "CPF"]]}
    assert words_normalized(p)[0][:4] == [0.8, 0.1, 0.9, 0.3]


@pytest.mark.parametrize("output_uncertain", [True, False])
def test_initial_doubt_requires_conclusive_output_checks(tmp_path, monkeypatch, output_uncertain):
    from PIL import Image

    from datajud_scraper.adhesion import privacy

    class Model:
        def ask(self, task, *args):
            if task == "adhesion_blocks_v1":
                return {"remove": [], "uncertain": True}
            if task == "adhesion_cleaned_only_v2":
                return {"pii_remaining": False, "uncertain": output_uncertain, "repair_ids": []}
            return {"content_preserved": True, "uncertain": False}

    monkeypatch.setattr(privacy, "cached_ocr", lambda *args: [])
    result = privacy.anonymize(
        Model(), Image.new("RGB", (100, 100), "white"), page(1, "Clausula contratual"), tmp_path
    )
    assert result["status"] == ("needs_review" if output_uncertain else "completed")
    assert result["checks"]["source_uncertainty_resolved_by_output_checks"] is not output_uncertain


def test_deskew_maps_ocr_boxes_back_to_original_pixels(monkeypatch):
    import cv2
    import numpy as np
    from PIL import Image

    from datajud_scraper.adhesion import privacy

    monkeypatch.setattr(cv2, "HoughLinesP", lambda *args, **kwargs: np.array([[[0, 10, 100, 15]]]))
    monkeypatch.setattr(privacy, "neural_words", lambda *args: [[0.2, 0.3, 0.5, 0.4, "CPF"]])
    words, geometry = privacy.deskew_words(Image.new("RGB", (100, 100), "white"))
    assert 2 < geometry["rotation_degrees"] < 4
    assert words[0][:4] != [0.2, 0.3, 0.5, 0.4]
    assert 0 <= words[0][0] < words[0][2] <= 1


def test_document_resume_reuses_completed_pages_after_interruption(tmp_path, monkeypatch):
    import pymupdf

    from datajud_scraper.adhesion import pipeline
    from datajud_scraper.adhesion.common import write_json
    from datajud_scraper.pdf_validation import hash_file

    original = tmp_path / "pdfs/original.pdf"
    original.parent.mkdir()
    with pymupdf.open() as pdf:
        for _ in range(2):
            pdf.new_page(width=100, height=100).insert_text((5, 30), "Credit terms", fontsize=8)
        pdf.save(original)
    source = {
        "document_id": "synthetic",
        "relative_path": "pdfs/original.pdf",
        "sha256": hash_file(original),
        "page_count": 2,
    }
    write_json(tmp_path / "adhesion-v1/holdout.json", {"documents": []})
    pages = [page(n, "Termo cartao") for n in (1, 2)]
    monkeypatch.setattr(pipeline, "load_inventory", lambda *args: (pages, []))
    monkeypatch.setattr(
        pipeline,
        "detect_with_model",
        lambda *args: {
            "instruments": [{"pages": [1, 2], "family": "generic"}],
            "unresolved": [],
        },
    )
    calls = []

    def clean(model, image, current, folder):
        calls.append(current["page_number"])
        if calls == [1, 2]:
            raise InterruptedError("simulated interruption")
        result = {"status": "completed", "masks": []}
        write_json(folder / "privacy.json", result)
        return result

    monkeypatch.setattr(pipeline, "anonymize", clean)
    with pytest.raises(InterruptedError):
        pipeline.run_document(tmp_path, source, None, {"version": "test"})
    with pipeline.state_connection(tmp_path) as connection:
        assert connection.execute("SELECT status FROM runs").fetchone()[0] == "error"
    result = pipeline.run_document(tmp_path, source, None, {"version": "test"})
    assert calls == [1, 2, 2]
    assert result["status"] == "completed"
    assert result["instruments"][0]["output"]["page_count"] == 2
    pipeline.run_document(tmp_path, source, None, {"version": "test"})
    assert calls == [1, 2, 2]
    assert hash_file(original) == source["sha256"]


def test_one_repair_can_cover_a_region_missing_in_source_ocr(tmp_path, monkeypatch):
    from PIL import Image

    from datajud_scraper.adhesion import privacy

    class Model:
        audits = 0

        def ask(self, task, *args):
            if task == "adhesion_blocks_v1":
                return {"remove": [], "uncertain": False}
            if task == "adhesion_cleaned_only_v2":
                self.audits += 1
                return {
                    "pii_remaining": self.audits == 1,
                    "uncertain": False,
                    "repair_ids": [0] if self.audits == 1 else [],
                }
            return {"content_preserved": True, "uncertain": False}

    reads = iter([[[0.3, 0.5, 0.7, 0.53, "Pessoa Sintetica"]], []])
    monkeypatch.setattr(privacy, "cached_ocr", lambda *args: next(reads))
    result = privacy.anonymize(
        Model(), Image.new("RGB", (100, 100), "white"), page(1, "Clausula contratual"), tmp_path
    )
    assert result["status"] == "completed"
    assert result["checks"]["repair_count"] == 1
    assert result["masks"][0]["region_stage"] == "output_ocr_0"


def test_correspondent_company_is_preserved_but_individual_agent_is_removable():
    regions = build_regions(
        [
            [0.1, 0.10, 0.8, 0.12, "Correspondente no Pais"],
            [0.1, 0.14, 0.8, 0.16, "Empresa: Empresa Sintetica LTDA"],
            [0.1, 0.18, 0.8, 0.20, "Endereco: Rua Comercial 100"],
            [0.1, 0.22, 0.3, 0.24, "Agente:"],
            [0.4, 0.22, 0.8, 0.24, "Pessoa Sintetica"],
            [0.1, 0.3, 0.8, 0.32, "Dados Titular"],
            [0.1, 0.34, 0.8, 0.36, "Endereco Comercial: Rua Privada 100"],
        ]
    )
    protected = protected_cells(regions)
    assert any("Empresa Sintetica" in r["text"] for r in protected)
    assert any("Rua Comercial" in r["text"] for r in protected)
    assert not any("Pessoa Sintetica" in r["text"] for r in protected)
    assert not any("Rua Privada" in r["text"] for r in protected)


def test_embedded_scan_strips_are_not_offered_as_photo_masks():
    from PIL import Image

    from datajud_scraper.adhesion.privacy import add_graphics

    scanned = {"width": 100, "height": 100, "image_rects": [[3, 0, 98, 26], [70, 60, 85, 80]]}
    regions = add_graphics([], Image.new("RGB", (100, 100), "white"), scanned)
    assert len(regions) == 1
    assert regions[0]["rect"][0] > 0.69


def test_generic_regulation_and_bank_account_forms_cannot_become_card_adhesion():
    assert instrument_evidence(page(1, "CONTRATO CARTAO DE CREDITO PESSOA FISICA")) is None
    assert (
        instrument_evidence(page(1, "Proposta de abertura de conta e adesao a produtos cartao"))
        is None
    )
    assert instrument_evidence(page(1, "Termo de contratacao de pacote de servicos cartao")) is None
    assert (
        instrument_evidence(page(1, "Termo de adesao ao cartao consignado"))
        == "card_adhesion_heading"
    )


def test_barcode_region_exists_even_when_ocr_cannot_read_digits():
    from PIL import Image, ImageDraw

    from datajud_scraper.adhesion.privacy import barcode_rectangles

    image = Image.new("RGB", (1000, 1400), "white")
    draw = ImageDraw.Draw(image)
    for x in range(750, 850, 5):
        draw.rectangle((x, 100, x + 1, 160), fill="black")
    boxes = barcode_rectangles(image)
    assert len(boxes) == 1
    assert boxes[0][0] <= 0.75 and boxes[0][2] >= 0.845


def test_personal_bank_section_stops_before_an_unknown_numbered_section():
    regions = build_regions(
        [
            [0.1, 0.1, 0.8, 0.12, "III - DADOS BANCARIOS DO TITULAR"],
            [0.1, 0.14, 0.8, 0.16, "Banco / Agencia / Conta: Banco Exemplo / 123 / 456"],
            [0.1, 0.18, 0.8, 0.20, "IV - SERVICO ADICIONAL"],
            [0.1, 0.22, 0.8, 0.24, "Valor por cartao: R$ 2,00"],
        ]
    )
    block = next(r for r in regions if r["kind"] == "block")
    assert block["recommended"]
    assert block["rect"][3] < 0.18


def test_written_month_signing_date_survives_but_neighboring_name_is_removed():
    words = [
        [0.1, 0.5, 0.3, 0.52, "Data e hora"],
        [0.5, 0.5, 0.9, 0.52, "14 de Julho de 2022 / 17:59:33"],
        [0.1, 0.55, 0.9, 0.57, "Nome do cliente: Pessoa Sintetica"],
    ]
    masks = protect_masks(
        [{"rect": [0, 0, 1, 1], "origin": "automatic", "category": "personal_block"}],
        protected_cells(build_regions(words)),
    )
    assert not any(in_rect(words[1], m["rect"]) for m in masks)
    assert any(in_rect(words[2], m["rect"]) for m in masks)
