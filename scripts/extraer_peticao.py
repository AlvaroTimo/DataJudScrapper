"""Extrae la petição inicial de cada contrato a partir del expediente completo.

Para cada contrato en data/Contratos/<carpeta>/NNN_<proceso>_pA-B.pdf busca su
expediente en data/dataset-contracts (cruce por número de proceso) y toma el documento
de tipo "Petição Inicial" del índice. Si hay más de uno (enmiendas o tipos mal cargados,
p. ej. una impugnação marcada como inicial), toma el más antiguo. Si no hay ninguno, el
cliente suele haber ido al Juizado sin abogado: la reclamación es el termo de queixa
(atermação), registrado como "Tomada de Termo" u otro tipo; se toma ese documento, o uno
con "taq/termo/queixa/inicial" en el nombre, presentado el día de la distribución. Las
páginas salen del pie "Id. <id> - Pág. <n>" (ver extraer_contestacao.py).

La petição es una por proceso: en cada carpeta se guarda una sola vez aunque el proceso
tenga varios contratos (se cruzan por el número de proceso). Antes de escribir se borran
las petições anteriores de la carpeta.

Salida:
  data/Contratos/<carpeta>/Peticao/NNN_<proceso>__peticao_inicial_<id>_pX-Y.pdf
  data/Contratos/manifiesto_peticao.jsonl   (una línea por contrato)

usage: .venv/bin/python scripts/extraer_peticao.py
"""

import argparse
import json
import os
import re
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

from extraer_contestacao import (CARPETAS, CONTRATO, CONTRATOS, EXPEDIENTES, fitz, leer_indice,
                                 paginas_por_documento, rangos, sin_acentos)

SUBCARPETA = "Peticao"
MANIFIESTO = os.path.join(CONTRATOS, "manifiesto_peticao.jsonl")


NOMBRE_INICIAL = re.compile(r"(^|[^a-z])(taq|termo|queixa|inicial)([^a-z]|$)")


def fecha(fila):
    return datetime.strptime(fila["fecha"], "%d/%m/%Y %H:%M")


def elegir(filas, paginas):
    """(fila, método) de la petição inicial, o (None, None)."""
    filas = [f for f in filas if f["id"] in paginas]
    iniciales = [f for f in filas if f["tipo"] == "Petição Inicial"]
    if iniciales:
        return min(iniciales, key=fecha), "tipo"
    if not filas:
        return None, None
    # atermação: documentos del día de la distribución
    dia = min(fecha(f) for f in filas).date()
    primer_dia = [f for f in filas if fecha(f).date() == dia]
    termos = [f for f in primer_dia if f["tipo"] == "Tomada de Termo"]
    if termos:
        return termos[0], "tomada_de_termo"
    por_nombre = [f for f in primer_dia if NOMBRE_INICIAL.search(sin_acentos(f["nombre"]))]
    if por_nombre:
        return por_nombre[0], "nombre"
    return None, None


def procesar_expediente(args):
    ruta, contratos = args
    if not os.path.exists(ruta):
        return [dict(c, estado="sin_expediente", salidas=[]) for c in contratos]
    filas = leer_indice(ruta)
    doc = fitz.open(ruta)
    _, paginas = paginas_por_documento(doc)
    iniciales = [f for f in filas if f["tipo"] == "Petição Inicial" and f["id"] in paginas]
    elegida, metodo = elegir(filas, paginas)

    resultados, escritos = [], {}
    for c in sorted(contratos, key=lambda c: c["contrato"]):
        r = dict(c, estado="ok" if elegida else "sin_peticao_inicial",
                 total_peticoes_iniciais=len(iniciales), salidas=[])
        if elegida:
            pags = paginas[elegida["id"]]
            if c["carpeta"] not in escritos:
                prefijo = c["contrato"].split("_")[0]
                nombre = (f"{prefijo}_{c['proceso']}__peticao_inicial_{elegida['id']}"
                          f"_p{pags[0]}-{pags[-1]}.pdf")
                destino = os.path.join(CONTRATOS, c["carpeta"], SUBCARPETA, nombre)
                nuevo = fitz.open()
                for a, b in rangos(pags):
                    nuevo.insert_pdf(doc, from_page=a - 1, to_page=b - 1)
                nuevo.save(destino, garbage=3, deflate=True)
                nuevo.close()
                escritos[c["carpeta"]] = destino
            destino = escritos[c["carpeta"]]
            r["salidas"].append({"archivo": destino, "tipo": elegida["tipo"], "id": elegida["id"],
                                 "fecha": elegida["fecha"], "nombre_original": elegida["nombre"],
                                 "metodo": metodo, "paginas": rangos(pags)})
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
        salida = os.path.join(CONTRATOS, carpeta, SUBCARPETA)
        os.makedirs(salida, exist_ok=True)
        for f in os.listdir(salida):
            if "__peticao_inicial_" in f and f.endswith(".pdf"):
                os.remove(os.path.join(salida, f))
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
    with open(MANIFIESTO, "w") as out:
        for r in resultados:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"{'carpeta':22} {'contratos':>9} {'con petição':>12} {'sin':>5} {'archivos':>9}")
    for carpeta in CARPETAS:
        rs = [r for r in resultados if r["carpeta"] == carpeta]
        ok = sum(r["estado"] == "ok" for r in rs)
        archivos = len({s["archivo"] for r in rs for s in r["salidas"]})
        print(f"{carpeta:22} {len(rs):9} {ok:12} {len(rs) - ok:5} {archivos:9}")
    print("estados:", dict(Counter(r["estado"] for r in resultados)))
    procesos = {r["proceso"]: r["estado"] for r in resultados}
    print("procesos por estado:", dict(Counter(procesos.values())))
    metodos = {r["proceso"]: r["salidas"][0]["metodo"] for r in resultados if r["salidas"]}
    print("procesos por método:", dict(Counter(metodos.values())))


if __name__ == "__main__":
    main()
