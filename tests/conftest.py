from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfWriter

PROCESS_NUMBER = "0003714-33.2025.8.05.0274"
PROCESS_DIGITS = "00037143320258050274"
PUBLIC_URL = "https://projudi.tjba.jus.br/projudi/AcessoPublico?codigoHash=22f20646"
DOWNLOAD_URL = (
    "https://projudi.tjba.jus.br/projudi/acoes/DownloadProcesso"
    "?numeroProcesso=6020251285346"
)


def case_html(
    *,
    secret: str = "NÃO",
    include_subject: bool = True,
    include_distribution: bool = True,
    include_download: bool = True,
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
        '<a href="javascript:chamaDownloadProcesso(6020251285346)">Download do Processo</a>'
        if include_download
        else ""
    )
    html = f"""
    <html><head><meta charset="iso-8859-1"></head><body>
      <h1>Processo nº {PROCESS_NUMBER}</h1>
      <table>
        {subject_row}
        <tr><td>Segredo de Justiça</td><td>{secret}</td></tr>
        {distribution_row}
      </table>
      {download_link}
    </body></html>
    """
    return html.encode("iso-8859-1")


@pytest.fixture
def valid_pdf() -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()
