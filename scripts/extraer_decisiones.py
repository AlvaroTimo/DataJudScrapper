"""Extrae la decisión judicial (sentencia / acórdão) de cada contrato.

Para cada contrato en data/Contratos/<carpeta>/NNN_<proceso>_pA-B.pdf busca su
expediente completo en data/dataset-contracts (cruce por número de proceso) y
localiza los documentos del juez con el índice y el pie "Id. <id> - Pág. <n>"
(ver extraer_contestacao.py).

En PROJUDI las sentencias quedan casi siempre como tipo "Conclusão"; también se
consideran "Sentença", "Acórdão", "Decisão" y "Despacho". Un documento cuenta como
decisión si el juez decide en primera persona ("julgo improcedentes", "nego
provimento", "homologo o acordo"...). El resultado sale de la ÚLTIMA de esas frases,
que es el dispositivo; así no se confunde con la jurisprudencia citada. Se ignoran
las decisiones de embargos de declaração y los avisos "sob pena de extinção".
Los procesos sin decisión que el juez suspendió (temas repetitivos 1.328 y 1.414 del
STJ, IRDR 20 del TJBA, sobre cartão RMC/RCC) quedan con estado "suspendido".

Salida, con la misma estructura de carpetas que data/Contratos:
  data/Decision/<carpeta>/<contrato>__decision_<instancia>_<resultado>_<id>_pX-Y.pdf
  data/Decision/manifiesto.jsonl   (una línea por contrato)

usage: .venv/bin/python scripts/extraer_decisiones.py
"""

import argparse
import json
import os
import re
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

from extraer_contestacao import (CARPETAS, CONTRATO, CONTRATOS, EXPEDIENTES, fitz, leer_indice,
                                paginas_por_documento, rangos, sin_acentos)

SALIDA = "data/Decision"
TIPOS_JUEZ = {"Conclusão", "Sentença", "Acórdão", "Decisão", "Despacho"}

# "julgo ..." o "extingo o processo com resolução do mérito, para julgar ..."
JULGO = r"(julgo|merito\W+(\w+\W+){0,3}para\s+julgar)\s+"
# frases del juez en primera persona -> resultado (el orden importa: parcial antes que procedente)
RESULTADOS = [
    ("recurso_parcialmente_provido", r"(dou|dar)\s+(parcial\s+provimento|provimento\s+parcial)"),
    ("recurso_provido", r"(dou|dar)\s+provimento"),
    ("recurso_no_provido", r"(nego|negar)\s+provimento"),
    ("parcialmente_procedente", JULGO + r"(parcialmente\s+procedentes?|procedentes?\s+em\s+parte)"
                                r"|acolho\s+(em\s+parte|parcialmente)\s+os\s+pedidos"),
    ("improcedente", JULGO + r"(totalmente\s+)?improcedentes?"),
    ("procedente", JULGO + r"(totalmente\s+)?procedentes?|acolho\s+(integralmente\s+)?os\s+pedidos"),
    ("extinto_sin_merito", r"(julgo|declaro)\s+extint\w*\s+o\s+(processo|feito)\W+sem\s+resolucao"
                           r"|extingo\s+o\s+(processo|feito)\W+sem\s+resolucao"),
    ("acuerdo_homologado", r"homologo\W+(\w+\W+){0,10}acordo"),
    ("desistencia_homologada", r"homologo\W+(\w+\W+){0,10}desistencia"),
]
DECISION = re.compile("|".join(f"(?P<{k}>{v})" for k, v in RESULTADOS))
EMBARGOS = re.compile(r"\W+(\w+\W+){0,3}embargos")
SUSPENSION = re.compile(r"(suspens|sobrest)\w*\W+(\w+\W+){0,40}tema|tema\w*\s+(repetitiv\w+\s+)?(n\.?o?\s*)?1\.?(328|414)\b")
TEMAS = {"STJ 1.328": re.compile(r"\b1\.?328\b"), "STJ 1.414": re.compile(r"\b1\.?414\b"),
         "IRDR 20 TJBA": re.compile(r"irdr\W+(\w+\W+){0,3}20\b|tema\s+(irdr\s+)?(n\.?o?\s*)?20\b")}
TRANSITO = re.compile(r"transitou\s+em\s+julgado|certifico\W+(\w+\W+){0,15}transito\s+em\s+julgado")


def texto(doc, pags):
    return re.sub(r"\s+", " ", sin_acentos(" ".join(doc[p - 1].get_text() for p in pags)))


def clasificar(t):
    """(resultado, fragmento del dispositivo) o (None, None)."""
    ultimo = None
    for m in DECISION.finditer(t):
        if m.lastgroup.startswith("recurso") and EMBARGOS.match(t, m.end()):
            continue
        ultimo = m
    if not ultimo:
        return None, None
    return ultimo.lastgroup, t[max(0, ultimo.start() - 150): ultimo.end() + 250].strip()


def procesar_expediente(args):
    ruta, contratos = args
    if not os.path.exists(ruta):
        return [dict(c, estado="sin_expediente", decisiones=[]) for c in contratos]
    filas = leer_indice(ruta)
    doc = fitz.open(ruta)
    _, paginas = paginas_por_documento(doc)

    decisiones, transito, temas = [], False, set()
    for f in filas:
        if f["id"] not in paginas:
            continue
        if f["tipo"] == "Certidão":
            transito = transito or bool(TRANSITO.search(texto(doc, paginas[f["id"]])))
            continue
        if f["tipo"] not in TIPOS_JUEZ:
            continue
        t = texto(doc, paginas[f["id"]])
        if SUSPENSION.search(t):
            temas |= {k for k, v in TEMAS.items() if v.search(t)} or {"otro"}
        resultado, fragmento = clasificar(t)
        # "Sentença" a veces es una orden de suspensión o resuelve embargos: no es el resultado
        if (resultado is None and f["tipo"] in ("Sentença", "Acórdão")
                and not SUSPENSION.search(t) and "embargos" not in t):
            resultado = "no_identificado"
        if resultado is None:
            continue
        recursal = resultado.startswith("recurso") or "turma recursal" in t[:600]
        instancia = "turma_recursal" if recursal else "primer_grado"
        decisiones.append(dict(f, paginas=paginas[f["id"]], resultado=resultado,
                               instancia=instancia, fragmento=fragmento))

    resultados = []
    for c in contratos:
        estado = "ok" if decisiones else "suspendido" if temas else "sin_decision"
        r = dict(c, estado=estado, suspension_temas=sorted(temas), transito_em_julgado=transito,
                 resultado_final=decisiones[-1]["resultado"] if decisiones else None, decisiones=[])
        for x in decisiones:
            pags = x["paginas"]
            nombre = (f"{c['contrato'][:-4]}__decision_{x['instancia']}_{x['resultado']}"
                      f"_{x['id']}_p{pags[0]}-{pags[-1]}.pdf")
            destino = os.path.join(SALIDA, c["carpeta"], nombre)
            nuevo = fitz.open()
            for a, b in rangos(pags):
                nuevo.insert_pdf(doc, from_page=a - 1, to_page=b - 1)
            nuevo.save(destino, garbage=3, deflate=True)
            nuevo.close()
            r["decisiones"].append({"archivo": destino, "tipo": x["tipo"], "id": x["id"],
                                    "fecha": x["fecha"], "instancia": x["instancia"],
                                    "resultado": x["resultado"], "paginas": rangos(pags),
                                    "fragmento": x["fragmento"]})
        resultados.append(r)
    doc.close()
    return resultados


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    args = ap.parse_args()

    expedientes = {}
    for f in os.listdir(EXPEDIENTES):
        m = re.match(r"^(\d+)_(.+)\.pdf$", f)
        if m:
            expedientes.setdefault(m.group(2), {})[m.group(1)] = os.path.join(EXPEDIENTES, f)
    grupos = {}
    for carpeta in CARPETAS:
        os.makedirs(os.path.join(SALIDA, carpeta), exist_ok=True)
        for f in sorted(os.listdir(os.path.join(CONTRATOS, carpeta))):
            m = CONTRATO.match(f)
            if not m:
                continue
            candidatos = expedientes.get(m.group(2), {})
            ruta = candidatos.get(m.group(1)) or next(iter(candidatos.values()), "")
            grupos.setdefault(ruta, []).append({
                "carpeta": carpeta, "contrato": f, "proceso": m.group(2), "expediente": ruta,
            })
    print(f"{sum(map(len, grupos.values()))} contratos en {len(grupos)} expedientes")

    resultados = []
    with ProcessPoolExecutor(args.workers) as ex:
        for res in ex.map(procesar_expediente, list(grupos.items())):
            resultados += res
    resultados.sort(key=lambda r: (r["carpeta"], r["contrato"]))
    with open(os.path.join(SALIDA, "manifiesto.jsonl"), "w") as out:
        for r in resultados:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"{'carpeta':22} {'contratos':>9} {'con decisión':>13} {'sin':>5}")
    for carpeta in CARPETAS:
        rs = [r for r in resultados if r["carpeta"] == carpeta]
        ok = sum(r["estado"] == "ok" for r in rs)
        print(f"{carpeta:22} {len(rs):9} {ok:13} {len(rs) - ok:5}")
    print("estados:", dict(Counter(r["estado"] for r in resultados)))
    print("resultado final:", dict(Counter(r["resultado_final"] for r in resultados)))
    print("con trânsito em julgado:", sum(r["transito_em_julgado"] for r in resultados))


if __name__ == "__main__":
    main()
