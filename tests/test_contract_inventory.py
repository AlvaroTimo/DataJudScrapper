from __future__ import annotations

import json
from pathlib import Path

import pytest

from datajud_scraper.contract_catalog import (
    initialize_contract_schema,
    require_release_reviews,
    require_source_review,
)
from datajud_scraper.contract_inventory import (
    DEFAULT_TESSDATA,
    contract_evidence,
    describe_attachments,
    inventory_document,
    recover_inventory_jobs,
    reserve_inventory_job,
    save_inventory,
)
from datajud_scraper.pdf_validation import hash_file
from datajud_scraper.runtime import Database

pymupdf = pytest.importorskip("pymupdf")


@pytest.fixture
def source_pdf(tmp_path):
    source = tmp_path / "source.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((40, 50), "Indice de documentos")
        document.new_page().insert_text(
            (40, 50),
            "TERMO DE ADESAO AO CARTAO DE CREDITO\nTaxa de juros: 2,00% ao mes. Prazo: 24 meses.",
        )
        with pymupdf.open() as scan:
            scan.new_page().insert_text(
                (40, 50),
                "CEDULA DE CREDITO BANCARIO\n"
                "EMPRESTIMO CONSIGNADO\n"
                "Valor do credito: R$ 1.500,00. Prazo: 12 meses.",
                fontsize=16,
            )
            pixmap = scan[0].get_pixmap(dpi=160)
            page = document.new_page()
            page.insert_image(page.rect, stream=pixmap.tobytes("png"))
        document.new_page().insert_text((40, 50), "CERTIDAO\nIntimacao das partes.")
        document.set_toc(
            [
                [1, "Id. 101 - Pag. 1", 2],
                [1, "Id. 102 - Pag. 2", 3],
                [1, "Id. 103 - Pag. 3", 4],
            ]
        )
        document.save(source)
    return source


@pytest.fixture
def catalog(tmp_path, source_pdf):
    db = Database(tmp_path / "catalog.sqlite3")
    with db.connection:
        db.connection.execute(
            "INSERT INTO cases(case_id,process_number,process_number_digits,first_seen_at) "
            "VALUES ('case','0000001-59.2026.8.05.0001','00000015920268050001','now')"
        )
        db.connection.execute(
            """INSERT INTO documents(document_id,case_id,retrieved_at,relative_path,
            sha256,size_bytes,mime_type,page_count) VALUES ('doc','case','now',?,?,?,?,4)""",
            (source_pdf.name, hash_file(source_pdf), source_pdf.stat().st_size, "application/pdf"),
        )
    initialize_contract_schema(db.connection)
    yield db.connection
    db.close()


def test_extension_preserves_existing_catalog_and_unknown_states(catalog, source_pdf):
    initialize_contract_schema(catalog)
    row = catalog.execute("SELECT * FROM documents WHERE document_id='doc'").fetchone()
    assert row["sha256"] == hash_file(source_pdf)
    assert row["has_contract"] is None and row["contract_count"] is None
    assert row["contracts_had_sensitive_data"] is None
    assert row["source_disposition"] == "retained"
    assert catalog.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 1
    assert catalog.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(ValueError, match="inventario incompleto"):
        require_release_reviews(catalog, "doc")


def test_attachments_cover_every_source_page_without_title_dependency(source_pdf):
    with pymupdf.open(source_pdf) as document:
        attachments = describe_attachments(document)
    covered = [p for a in attachments for p in range(a["start_page"], a["end_page"] + 1)]
    assert covered == [1, 2, 3, 4]
    assert attachments[0]["index_prefix"]
    assert not attachments[1]["title_evidence"]
    assert contract_evidence("CÉDULA DE CRÉDITO BANCÁRIO") == ["credit_note"]
    assert "adhesion" in contract_evidence("TERMO DE ADESÃO")
    assert "loan_agreement" in contract_evidence("CONTRATO DE EMPRÉSTIMO CONSIGNADO")


def test_index_link_labels_do_not_include_adjacent_rows_or_type_column():
    with pymupdf.open() as doc:
        doc.new_page()
        doc.new_page()
        doc.new_page()
        page = doc[0]
        for y, target, text in ((300, 1, "CONTRATO.pdf"), (313, 2, "CERTIDAO.pdf")):
            page.insert_text((165, y), text, fontsize=10)
            page.insert_text((460, y), "Outros", fontsize=10)
            page.insert_link(
                {
                    "kind": pymupdf.LINK_GOTO,
                    "page": target,
                    "from": pymupdf.Rect(164, y - 8, 260, y + 1),
                }
            )
            page.insert_link(
                {
                    "kind": pymupdf.LINK_GOTO,
                    "page": target,
                    "from": pymupdf.Rect(460, y - 8, 490, y + 1),
                }
            )
        doc.set_toc([[1, "Id. 1", 2], [1, "Id. 2", 3]])
        with pymupdf.open(stream=doc.tobytes(), filetype="pdf") as reopened:
            attachments = describe_attachments(reopened)
    assert attachments[1]["title"] == "CONTRATO.pdf"
    assert attachments[2]["title"] == "CERTIDAO.pdf"


def test_inventory_reads_scanned_contract_and_does_not_autoapprove(source_pdf, catalog, tmp_path):
    if not (DEFAULT_TESSDATA / "por.traineddata").exists():
        pytest.skip("requiere modelos OCR locales; ejecutar setup de modelos")
    before = source_pdf.read_bytes()
    result = inventory_document(
        {
            "source_path": str(source_pdf),
            "folder": str(tmp_path / "inventory"),
            "document_id": "doc",
            "source_sha256": hash_file(source_pdf),
            "page_count": 4,
            "tessdata": str(DEFAULT_TESSDATA),
        }
    )
    assert result["ocr_pages"] == 1 and result["ocr_failures"] == 0
    assert result["attachments"][1]["candidate"]
    assert result["attachments"][2]["candidate"]
    assert not result["attachments"][3]["candidate"]
    save_inventory(catalog, result)
    save_inventory(catalog, result)
    assert catalog.execute("SELECT COUNT(*) FROM contract_pages").fetchone()[0] == 4
    assert catalog.execute("SELECT COUNT(*) FROM contracts").fetchone()[0] == 0
    assert catalog.execute("SELECT has_contract FROM documents").fetchone()[0] is None
    assert catalog.execute("SELECT COUNT(*) FROM contract_page_reviews").fetchone()[0] == 0
    assert source_pdf.read_bytes() == before
    with pytest.raises(ValueError, match="revisar manualmente"):
        require_source_review(catalog, "doc")
    stored = json.loads((Path(result["pages_file"]).parent / "inventory.json").read_text())
    assert stored["review_status"] == "unreviewed"


def test_ocr_failure_remains_unresolved(source_pdf, catalog, tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("OCR unavailable")

    monkeypatch.setattr(pymupdf.Page, "get_textpage_ocr", fail)
    result = inventory_document(
        {
            "source_path": str(source_pdf),
            "folder": str(tmp_path / "failed-ocr"),
            "document_id": "doc",
            "source_sha256": hash_file(source_pdf),
            "page_count": 4,
            "tessdata": str(DEFAULT_TESSDATA),
        }
    )
    assert result["ocr_failures"] == 1
    save_inventory(catalog, result)
    with pytest.raises(ValueError, match="errores de OCR"):
        require_source_review(catalog, "doc")
    assert catalog.execute("SELECT has_contract FROM documents").fetchone()[0] is None


def test_live_inventory_reservation_is_not_reclaimed_on_observation_failure(catalog, monkeypatch):
    row = catalog.execute("SELECT * FROM documents WHERE document_id='doc'").fetchone()
    assert reserve_inventory_job(catalog, row)
    assert not reserve_inventory_job(catalog, row)
    monkeypatch.setattr("datajud_scraper.contract_inventory.process_start_time", lambda pid: None)
    recover_inventory_jobs(catalog)
    assert catalog.execute("SELECT status FROM contract_sources").fetchone()[0] == "processing"

    def missing_process(pid, signal):
        raise ProcessLookupError

    monkeypatch.setattr("datajud_scraper.contract_inventory.os.kill", missing_process)
    recover_inventory_jobs(catalog)
    assert catalog.execute("SELECT status FROM contract_sources").fetchone()[0] == "pending"


def test_index_rebuild_reuses_complete_ocr_cache(source_pdf, tmp_path, monkeypatch):
    if not (DEFAULT_TESSDATA / "por.traineddata").exists():
        pytest.skip("requiere modelos OCR locales")
    task = {
        "source_path": str(source_pdf),
        "folder": str(tmp_path / "reusable-cache"),
        "document_id": "doc",
        "source_sha256": hash_file(source_pdf),
        "page_count": 4,
        "tessdata": str(DEFAULT_TESSDATA),
    }
    first = inventory_document(task)

    def unexpected_ocr(*args, **kwargs):
        raise AssertionError("no debe repetir OCR completo")

    monkeypatch.setattr("datajud_scraper.contract_inventory.page_inventory", unexpected_ocr)
    second = inventory_document(task)
    assert second["pages"] == first["pages"]
    assert second["ocr_pages"] == 1
