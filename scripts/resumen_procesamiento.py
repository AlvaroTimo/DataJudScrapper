"""Genera data/Contratos/resumen_procesamiento.pdf: contratos, contestações e decisões por pasta.

Lee data/Contratos (únicos), data/0.duplicated (duplicados) y los manifiestos de
data/Contestacao y data/Decision (ver extraer_contestacao.py y extraer_decisiones.py).

usage: uv run --no-project --with reportlab python scripts/resumen_procesamiento.py
"""

import json
import os
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

CARPETAS = ["buscar_texto", "contratos_genericos", "indices", "primera_pagina"]
PROCESO = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")
SALIDA = "data/Contratos/resumen_procesamiento.pdf"

TITULO = ParagraphStyle("t", fontName="Times-Bold", fontSize=15, alignment=TA_CENTER, spaceAfter=10)
SECCION = ParagraphStyle("s", fontName="Times-Bold", fontSize=11.5, spaceBefore=14, spaceAfter=4,
                         keepWithNext=1)
TEXTO = ParagraphStyle("p", fontName="Times-Roman", fontSize=10, leading=13, alignment=TA_JUSTIFY,
                       spaceAfter=4)
NOTA = ParagraphStyle("n", fontName="Times-Roman", fontSize=8.5, leading=10.5, spaceBefore=3)
CAB = ParagraphStyle("h", fontName="Times-Bold", fontSize=8.5, leading=10, alignment=TA_CENTER)
CAB_IZQ = ParagraphStyle("hl", parent=CAB, alignment=0)
ITEM = ParagraphStyle("i", parent=TEXTO, leftIndent=14, bulletIndent=4, spaceAfter=2)

TIPOS_DECISION = [
    ("primer_grado", "sentença do juiz do Juizado Especial, a primeira decisão sobre o pedido do cliente."),
    ("turma_recursal", "acórdão da Turma Recursal, que julga o recurso contra a sentença de primeiro grau."),
    ("procedente", "o juiz aceita todo o pedido do cliente: ganha o cliente."),
    ("parcialmente_procedente", "o juiz aceita só parte do pedido do cliente."),
    ("improcedente", "o juiz rejeita o pedido do cliente: ganha o banco."),
    ("extinto_sin_merito", "o processo é encerrado sem julgar o mérito (por exemplo, o autor não "
                           "compareceu à audiência ou faltou um documento)."),
    ("acuerdo_homologado", "as partes fizeram um acordo e o juiz o homologou."),
    ("desistencia_homologada", "a parte desistiu (da ação ou do recurso) e o juiz homologou a desistência."),
    ("recurso_provido", "a Turma Recursal aceita o recurso: a sentença é reformada."),
    ("recurso_parcialmente_provido", "a Turma Recursal aceita o recurso em parte: a sentença muda "
                                     "só em alguns pontos."),
    ("recurso_no_provido", "a Turma Recursal rejeita o recurso: a sentença de primeiro grau é mantida."),
]


def pdfs(carpeta):
    return [f for f in os.listdir(carpeta) if f.endswith(".pdf")] if os.path.isdir(carpeta) else []


def procesos(archivos):
    return {m.group(0) for f in archivos if (m := PROCESO.search(f))}


def manifiesto(ruta):
    return [json.loads(linea) for linea in open(ruta)]


def tabla(grupos, cabecera, filas, anchos):
    """grupos: [(texto, col_inicio, col_fin)]; una línea vertical antes de cada grupo."""
    fila_grupos = [""] * len(cabecera)
    for texto, a, _ in grupos:
        fila_grupos[a] = Paragraph(texto, CAB)
    datos = [fila_grupos, [Paragraph(cabecera[0], CAB_IZQ)] + [Paragraph(c, CAB) for c in cabecera[1:]]]
    datos += [[str(v) for v in f] for f in filas]
    estilo = [
        ("FONT", (0, 2), (-1, -1), "Times-Roman", 9),
        ("FONT", (0, -1), (-1, -1), "Times-Bold", 9),
        ("ALIGN", (1, 2), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("LINEABOVE", (0, 0), (-1, 0), 1, colors.black),
        ("LINEBELOW", (0, 1), (-1, 1), 0.6, colors.black),
        ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.black),
        ("LINEBELOW", (0, -1), (-1, -1), 1, colors.black),
    ]
    for _, a, b in grupos:
        estilo += [("SPAN", (a, 0), (b, 0)), ("LINEBELOW", (a, 0), (b, 0), 0.4, colors.grey),
                   ("LINEBEFORE", (a, 0), (a, -1), 0.6, colors.black)]
    t = Table(datos, colWidths=[w * cm for w in anchos], repeatRows=2)
    t.setStyle(TableStyle(estilo))
    return t


def main():
    unicos = {c: pdfs(f"data/Contratos/{c}") for c in CARPETAS}
    duplicados = {c: pdfs(f"data/0.duplicated/{c}") for c in CARPETAS}
    contestacao = manifiesto("data/Contestacao/manifiesto.jsonl")
    decision = manifiesto("data/Decision/manifiesto.jsonl")
    peticao = manifiesto("data/Contratos/manifiesto_peticao.jsonl")

    def por_carpeta(rs, c):
        return rs if c == "Total" else [r for r in rs if r["carpeta"] == c]

    t1, t2, t3 = [], [], []
    for c in CARPETAS + ["Total"]:
        u = sum(unicos.values(), []) if c == "Total" else unicos[c]
        d = sum(duplicados.values(), []) if c == "Total" else duplicados[c]
        n_proc = len(procesos(u))
        t1.append([c, n_proc, len(u) + len(d), len(u), len(d), 0])

        rs = por_carpeta(contestacao, c)
        docs = [s for r in rs for s in r["salidas"]]
        con = sum(1 for r in rs if r["salidas"])
        ids = {s["id"] for s in docs}
        t2.append([c, n_proc, len(rs), con, len(rs) - con, len(ids)])

        rs = por_carpeta(decision, c)
        docs = [s for r in rs for s in r["decisiones"]]
        con = sum(1 for r in rs if r["decisiones"])
        sus = sum(r["estado"] == "suspendido" for r in rs)
        sin = sum(r["estado"] == "sin_decision" for r in rs)
        ids = {s["id"] for s in docs}
        t3.append([c, n_proc, len(rs), con, sus, sin, sus + sin, len(ids)])

    t4 = [[c, t1[i][1], len({s["id"] for r in por_carpeta(peticao, c) for s in r["salidas"]})]
          for i, c in enumerate(CARPETAS + ["Total"])]
    metodo = {r["proceso"]: r["salidas"][0] for r in peticao if r["salidas"]}
    n_inicial = sum(s["metodo"] == "tipo" for s in metodo.values())
    n_termo = sum(s["metodo"] == "tomada_de_termo" for s in metodo.values())
    n_taq = sum(s["metodo"] == "nombre" and "taq" in s["nombre_original"].lower() for s in metodo.values())
    n_outro = len(metodo) - n_inicial - n_termo - n_taq

    tot2, tot3 = t2[-1], t3[-1]
    story = [
        Paragraph("Detalhes", TITULO),
        Paragraph("Tabela 1: Contratos", SECCION),
        tabla([("Processos", 1, 1), ("Contratos (PDF)", 2, 5)],
              ["Pasta (tipo de documento)", "Números de processo", "Processados (PDF)", "Únicos",
               "Duplicados", "Erros"],
              t1, [4.6, 2.6, 2.4, 2.0, 2.2, 1.6]),

        Paragraph("Tabela 2: Contestação", SECCION),
        Paragraph("A contestação é a peça de defesa que o banco réu apresenta em resposta à petição "
                  "inicial; é nela que o banco costuma juntar o contrato como prova, por isso quase todo "
                  "contrato tem a sua contestação no mesmo processo.", TEXTO),
        Paragraph(f"Os {tot2[4]} contratos sem contestação pertencem a processos em cujos autos não há "
                  "nenhuma contestação (o réu ainda não contestou ou não apresentou defesa).", TEXTO),
        tabla([("Processos", 1, 1), ("Contratos", 2, 4), ("Documentos extraídos", 5, 5)],
              ["Pasta (tipo de documento)", "Números de processo", "Contratos", "Com contestação",
               "Sem contestação", "Únicos"],
              t2, [4.6, 2.6, 2.2, 2.5, 2.5, 3.0]),

        Paragraph("Tabela 3: Decisão", SECCION),
        Paragraph("A decisão é o pronunciamento do juiz que resolve o pedido (sentença) ou o recurso "
                  "(acórdão da Turma Recursal); o resultado é lido do dispositivo: procedente, "
                  "improcedente, parcialmente procedente ou recurso (não) provido.", TEXTO),
        Paragraph(f"Há contratos sem decisão porque o processo ainda está em andamento e o juiz não "
                  f"sentenciou ({tot3[5]}), ou porque o juiz suspendeu o processo até que um tribunal "
                  f"superior decida casos iguais ({tot3[4]}).", TEXTO),
        tabla([("Processos", 1, 1), ("Contratos", 2, 3), ("Contratos sem decisão", 4, 6),
               ("Documentos extraídos", 7, 7)],
              ["Pasta (tipo de documento)", "Números de processo", "Contratos", "Com decisão",
               "Suspensos*", "Sem sentença ainda", "Sem decisão", "Únicos"],
              t3, [3.5, 2.0, 1.9, 1.8, 2.0, 1.9, 1.8, 2.5]),
        Paragraph("* Suspensos: o juiz parou o processo até que um tribunal superior decida casos iguais. "
                  "Sem decisão = Suspensos + Sem sentença ainda.", NOTA),
        Paragraph("Um contrato pode ter mais de uma decisão (sentença e acórdão).", NOTA),
        Paragraph("Tipos de decisão (como aparecem no nome dos arquivos)", SECCION),
    ] + [Paragraph(f"<b>{k}</b>: {v}", ITEM, bulletText="•") for k, v in TIPOS_DECISION] + [
        Paragraph("Tabela 4: Petição", SECCION),
        Paragraph(f"Em {n_inicial} processos a petição inicial aparece no índice como \"Petição Inicial\". "
                  f"Em {n_termo + n_taq}, o cliente entrou no Juizado sem advogado: o servidor registrou "
                  "o pedido por escrito (atermação), e o documento aparece como \"Tomada de Termo\" "
                  f"({n_termo}) ou \"TAQ\", Termo de Atermação ({n_taq})"
                  + (f". Em {n_outro}, a petição foi enviada num só arquivo junto com os documentos e "
                     "aparece como \"Documentos\"." if n_outro else "."), TEXTO),
        tabla([("Processos", 1, 1), ("Petição", 2, 2)],
              ["Pasta (tipo de documento)", "Números de processo", "Petições"],
              t4, [6.0, 4.0, 4.0]),
    ]
    SimpleDocTemplate(SALIDA, pagesize=A4, leftMargin=1.8 * cm, rightMargin=1.8 * cm,
                      topMargin=2 * cm, bottomMargin=2 * cm).build(story)
    print("escrito", SALIDA)


if __name__ == "__main__":
    main()
