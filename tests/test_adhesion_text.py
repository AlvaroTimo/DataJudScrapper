import pytest

from datajud_scraper.adhesion.text import contract_evidence, describe_attachments

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
