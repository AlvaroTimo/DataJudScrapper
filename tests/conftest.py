from __future__ import annotations

import json
from dataclasses import replace
from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from datajud_scraper.config import ScraperConfig

PROCESS_NUMBER = "0003714-33.2025.8.05.0274"
PROCESS_DIGITS = "00037143320258050274"
INTERNAL_ID = "6020251285346"
PUBLIC_URL = "https://projudi.tjba.jus.br/projudi/AcessoPublico?codigoHash=22f20646"
SOURCE_URL = (
    f"https://projudi.tjba.jus.br/projudi/listagens/DadosProcesso?numeroProcesso={INTERNAL_ID}"
)
DOWNLOAD_URL = (
    f"https://projudi.tjba.jus.br/projudi/acoes/DownloadProcesso?numeroProcesso={INTERNAL_ID}"
)
QUERY_URL = "https://projudi.tjba.jus.br/projudi/buscas/ProcessosParte"
EXPIRED = b"<html><title>Sistema CNJ - A sess&atilde;o expirou.</title></html>"


def case_html(
    *,
    secret="NÃO",
    include_subject=True,
    include_distribution=True,
    include_download=True,
    include_header=True,
    cnj=PROCESS_NUMBER,
    internal_id=INTERNAL_ID,
) -> bytes:
    subject_row = (
        "<tr><td>Assunto:</td><td>Indenização por Dano Material « DIREITO DO CONSUMIDOR</td></tr>"
        if include_subject
        else ""
    )
    distribution_row = (
        "<tr><td>Data de Distribuição</td><td>3 de Abril de 2025 às 09:02:17 h</td></tr>"
        if include_distribution
        else ""
    )
    download_link = (
        f'<a href="javascript:chamaDownloadProcesso({internal_id})">Download do Processo</a>'
        if include_download
        else ""
    )
    identity = (
        f'<a href="/projudi/listagens/DadosProcesso?numeroProcesso={internal_id}">{cnj}</a>'
        if include_header
        else cnj
    )
    return f"""
    <html><head><meta charset="iso-8859-1"></head><body>
      <h1>Processo nº {identity}</h1><table>
        {subject_row}<tr><td>Segredo de Justiça</td><td>{secret}</td></tr>{distribution_row}
      </table>{download_link}
    </body></html>
    """.encode("iso-8859-1")


def pdf_bytes(cnj=PROCESS_NUMBER, pages=1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=595, height=842)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 30 800 Td (Processo n: {cnj}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def record_data(cnj=PROCESS_NUMBER, internal_id=INTERNAL_ID, **extra):
    return {
        "url_download": DOWNLOAD_URL.replace(INTERNAL_ID, cnj),
        "process_number": cnj,
        "process_number_digits": "".join(c for c in cnj if c.isdigit()),
        "process_year": int(cnj[11:15]),
        "source_url": SOURCE_URL.replace(INTERNAL_ID, internal_id),
        "codigo_hash": None,
        "projudi_internal_id": internal_id,
        "distribution_at": "2025-04-03T12:02:17Z",
        "subject": "Assunto original",
        "is_secret": False,
        "classe": "Classe",
        "datajud_subjects": ["Assunto DataJud"],
        "orgao_julgador": "Juzgado",
        "data_ajuizamento": "2025-04-03",
        "arquivos": {"links_detectados": 0, "restricoes_detectadas": 14},
        "avisos": ["Advertencia historica"],
        **extra,
    }


def numbered_record(index):
    serial = f"{index:07}"
    check = 98 - int(serial + "2025805027400") % 97
    return record_data(cnj=f"{serial}-{check:02}.2025.8.05.0274", internal_id=str(index))


@pytest.fixture
def dataset_factory(tmp_path):
    def create(records=None, metadata_extra=None):
        records = records if records is not None else [record_data()]
        folder = tmp_path / "inputs"
        folder.mkdir(exist_ok=True)
        path, metadata = folder / "dataset.jsonl", folder / "metadata.json"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
        metadata.write_text(
            json.dumps(
                {
                    "version_esquema": 2,
                    "total_registros": len(records),
                    "extra": {"lineage": ["complete"]},
                    **(metadata_extra or {}),
                }
            )
        )
        return path, metadata

    return create


@pytest.fixture
def test_config(tmp_path):
    return replace(
        ScraperConfig(storage_root=tmp_path / "data"),
        min_request_interval_seconds=0,
        max_request_jitter_seconds=0,
        min_free_bytes=0,
        lock_timeout_seconds=0,
    ).normalized()


@pytest.fixture
def valid_pdf():
    return pdf_bytes()
