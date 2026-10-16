"""Deterministic privacy rules: identifiers, contract wording and unreadable text layers.

All values are synthetic. 529.982.247-25 is a public, checksum-valid test CPF.
"""

import json

from datajud_scraper.adhesion.privacy import (
    ask_in_parts,
    build_regions,
    contract_text_cells,
    identifier_kinds,
    identifier_masks,
    lost_contract_lines,
    parts,
    qr_masks,
    unreadable_native,
)


def words_of(line, y=0.5, x=0.05, step=0.09):
    """One printed line as positioned words."""
    result = []
    for token in line.split():
        result.append([x, y, x + step * 0.9, y + 0.012, token])
        x += step
    return result


def masked_tokens(words, masks):
    return {
        w[4]
        for w in words
        for m in masks
        if m["rect"][0] <= (w[0] + w[2]) / 2 <= m["rect"][2]
        and m["rect"][1] <= (w[1] + w[3]) / 2 <= m["rect"][3]
    }


def test_esignature_trace_masks_hash_and_ip_but_keeps_date_and_time():
    words = words_of(
        "Assinatura Eletrônica: a1b2c3d4e5f60718293a4b5c6d7e8f90 | Data/Hora: 01/07/2021 "
        "15:12 | IP/Terminal: 10.20.30.40",
        step=0.04,
    )
    hidden = masked_tokens(words, identifier_masks(words))
    assert "a1b2c3d4e5f60718293a4b5c6d7e8f90" in hidden
    assert "10.20.30.40" in hidden
    assert not hidden & {"01/07/2021", "15:12", "Assinatura", "Eletrônica:", "IP/Terminal:"}


def test_session_id_on_the_line_below_its_label_is_masked():
    words = words_of("CPF ID da sessão usuário", y=0.40) + words_of("12345678", y=0.415)
    assert "12345678" in masked_tokens(words, identifier_masks(words))


def test_clause_numbering_and_public_channels_are_not_identifiers():
    assert identifier_kinds("conforme cláusula 1.2.3.4 deste termo") == []
    assert identifier_kinds("SAC 0800 775 0500 atendimento 24 horas") == []
    assert identifier_kinds("Lei nº 13.709/2018") == []
    assert identifier_kinds("01/07/2021 15:12:26") == []


def test_personal_identifiers_are_detected():
    assert "cpf" in identifier_kinds("CPF: 529.982.247-25")
    assert "mobile" in identifier_kinds("Celular: (71) 99123-4567")
    assert "email" in identifier_kinds("E-mail: pessoa.sintetica@gmail.com")
    assert identifier_kinds("ouvidoria@banco.com.br") == []
    assert "uuid" in identifier_kinds("b7cdb9e9-bd4d-4db1-85a7-d624bd775237")
    assert "geolocation" in identifier_kinds("-12.9340234, -38.4064798")
    assert "cnj" in identifier_kinds("Processo 0000000-00.2024.8.05.0001")


def test_qr_graphics_are_always_masked():
    regions = [
        {"id": 0, "kind": "graphic", "rect": [0.7, 0.8, 0.8, 0.9], "source": "qr"},
        {"id": 1, "kind": "graphic", "rect": [0.1, 0.1, 0.3, 0.2], "source": "image"},
    ]
    assert [m["region_id"] for m in qr_masks(regions)] == [0]


def test_shifted_or_control_character_text_layers_are_unreadable():
    garbage = words_of("abcde f gf hdidj f kgl mdf hgf ndoidodp f qrf hgf sgtgm " * 4, step=0.01)
    assert unreadable_native(garbage)
    control = words_of("\x01\x02\x03 texto \x04\x05\x06 normal\x07\x08", step=0.05)
    assert unreadable_native(control)
    clause = words_of(
        "o cliente declara estar ciente de que a taxa de juros do cartao e informada na "
        "fatura mensal e que os encargos incidem sobre o saldo devedor nos termos do "
        "regulamento registrado em cartorio conforme a lei vigente",
        step=0.01,
    )
    assert not unreadable_native(clause)


def test_contract_wording_is_protected_but_personal_rows_and_names_are_not():
    regions = build_regions(
        [
            [0.1, 0.10, 0.4, 0.12, "DADOS PESSOAIS"],
            [0.1, 0.14, 0.5, 0.16, "rua das flores sem numero"],
            [0.1, 0.25, 0.6, 0.27, "Cláusulas e condições gerais"],
            [0.1, 0.30, 0.9, 0.32, "e que os encargos incidem sobre o saldo devedor da fatura"],
            [0.1, 0.34, 0.9, 0.36, "TENHO CIÊNCIA QUE ESTOU CONTRATANDO UM CARTÃO"],
            [0.1, 0.38, 0.4, 0.40, "Nome:"],
            [0.5, 0.38, 0.9, 0.40, "Maria das Dores"],
            [0.1, 0.42, 0.9, 0.44, "S. Sim N. Não"],
        ]
    )
    texts = {r["text"] for r in contract_text_cells(regions, reliable_text=True)}
    assert "e que os encargos incidem sobre o saldo devedor da fatura" in texts
    assert "TENHO CIÊNCIA QUE ESTOU CONTRATANDO UM CARTÃO" in texts
    assert "S. Sim N. Não" in texts
    assert "rua das flores sem numero" not in texts
    assert "Maria das Dores" not in texts


def test_masked_clause_is_reported_as_lost_but_masked_name_is_not():
    clause = words_of("e que os encargos incidem sobre o saldo devedor da fatura", y=0.30)
    name = words_of("Maria das Dores Sintetica de Souza", y=0.50)
    masks = [
        {"rect": [0.0, 0.29, 1.0, 0.32], "category": "personal_block", "origin": "automatic"},
        {"rect": [0.0, 0.49, 1.0, 0.52], "category": "personal_block", "origin": "automatic"},
    ]
    lost = lost_contract_lines(clause + name, masks, [])
    assert [line["text"] for line in lost] == [
        "e que os encargos incidem sobre o saldo devedor da fatura"
    ]


def test_masked_court_footer_is_not_reported_as_lost_contract_text():
    stamp = words_of("Código de validação do documento: a ser validado no sítio do PROJUDI", y=0.95)
    signer = words_of("Assinado eletronicamente por: de acordo com o sistema do tribunal", y=0.97)
    masks = [{"rect": [0.0, 0.94, 1.0, 0.99], "category": "footer", "origin": "automatic"}]
    assert lost_contract_lines(stamp + signer, masks, []) == []


def counter_page(number, text, counter):
    return {
        "page_number": number,
        "width": 100,
        "height": 100,
        "words": [[5, 5, 90, 10, text], [80, 90, 95, 93, f"{counter[0]}/{counter[1]}"]],
        "text_method": "native",
    }


def test_complete_printed_run_is_assembled_and_locked():
    from datajud_scraper.adhesion.boundaries import resolve_roles

    pages = [
        counter_page(1, "TERMO DE ADESAO AO CARTAO DE CREDITO CONSIGNADO", (1, 3)),
        counter_page(2, "clausulas gerais do contrato e encargos da fatura", (2, 3)),
        counter_page(3, "assinatura eletronica do aceite do titular", (3, 3)),
    ]
    locked = set()
    roles, changes = resolve_roles(pages, {1: "A", 2: "B", 3: "N"}, locked=locked)
    assert roles == {1: "A", 2: "C", 3: "E"}
    assert {c["reason"] for c in changes} == {"complete_printed_counter_run"}
    assert locked == {1, 2}


def test_continuation_of_a_rejected_document_is_audited_not_a_doubt():
    from datajud_scraper.adhesion.boundaries import assemble_roles

    text = "regulamento geral de utilizacao com clausulas sobre encargos e faturas " * 2
    pages = [counter_page(n, text, (n, 9)) for n in range(1, 4)]
    pages[0]["words"][0][4] = "outro documento qualquer " + text
    result = assemble_roles(pages, {1: "N", 2: "C", 3: "E"})
    assert result["excluded_continuations"] == [2, 3]
    assert not result["unresolved"]


def test_dense_region_payload_is_split_and_a_small_one_keeps_one_request():
    regions = [{"id": n, "text": "x" * 500} for n in range(400)]
    chunks = parts(regions, 60000)
    assert len(chunks) > 1 and [r for c in chunks for r in c] == regions

    class Model:
        def __init__(self):
            self.contents = []

        def ask(self, task, system, content, schema, images):
            self.contents.append(content)
            ids = [r["id"] for r in json.loads(content)["regions"]]
            return {"remove": ids[:1], "uncertain": False}

    model = Model()
    answers = ask_in_parts(model, "t", "s", regions, lambda p: {"regions": p}, {}, [])
    assert len(answers) == len(model.contents) > 1
    assert all(len(c.encode()) < 78000 for c in model.contents)
    small = Model()
    ask_in_parts(small, "t", "s", regions[:3], lambda p: {"family": None, "regions": p}, {}, [])
    assert small.contents == [json.dumps({"family": None, "regions": regions[:3]})]
