"""Extrae la contestación de cada contrato a partir del expediente completo.

Para cada contrato en data/Contratos/<carpeta>/NNN_<proceso>_pA-B.pdf busca el
expediente data/dataset-contracts/<n>_<proceso>.pdf (cruce por número de proceso) y localiza la contestación con:
  - el "Índice de Documentos" de las primeras páginas (Id -> Tipo), y
  - el pie de cada página "Id. <id> - Pág. <n>" (página -> Id).
Si el proceso tiene varias, elige la presentada junto con el contrato (misma fecha
y hora en el índice); si no, la última anterior al contrato; si el contrato vino con
la petición inicial, la primera posterior. Puntúa nombre del archivo, texto de la primera
página y tipo del índice para separar la pieza de defensa de sus anexos, y descarta
réplicas/impugnaciones.

Salida, con la misma estructura de carpetas que data/Contratos:
  data/Contestacao/<carpeta>/<contrato>__contestacao_<id>_pX-Y.pdf
  data/Contestacao/manifiesto.jsonl   (una línea por contrato)

usage: .venv/bin/python scripts/extraer_contestacao.py [--tipos "Contestação,Sentença"]
"""

import argparse
import json
import os
import re
import subprocess
import unicodedata
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

import pymupdf as fitz

fitz.TOOLS.mupdf_display_errors(False)

CONTRATOS = "data/Contratos"
EXPEDIENTES = "data/dataset-contracts"
SALIDA = "data/Contestacao"
CARPETAS = ["buscar_texto", "contratos_genericos", "indices", "primera_pagina"]

CONTRATO = re.compile(r"^(\d{3})_(\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4})_p(\d+)(?:-(\d+))?\.pdf$")
FILA = re.compile(r"^\s*(\d{6,})\s+(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2})\s+(.*\S)\s*$")
PIE = re.compile(r"Id\.\s*(\d{6,})\s*-\s*P[áa]g\.\s*(\d+)")
TIPOS_CONOCIDOS = [
    "Petição Inicial", "Contestação", "Sentença", "Acórdão", "Decisão em Pedido", "Decisão",
    "Despacho", "Termo de Audiência", "Petição", "Procuração", "Substabelecimento",
    "Comprovante Residência", "Comprovante Intimação", "Comprovante Citação",
    "Comprovante Depósito", "Comprovante", "Conclusão", "Citação", "Certidão", "Intimação",
    "Documentos", "Ato Ordinatório", "Mandado", "Cálculos", "Ofício", "Tomada de Termo",
    "Email", "Carta Precatória", "Carta de Preposição", "Cumprimento Genérico", "Parecer",
    "Alvará", "Outros",
]


def sin_acentos(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def leer_indice(ruta):
    """Filas del índice: [{id, fecha, nombre, tipo}] en orden."""
    txt = subprocess.run(["pdftotext", "-layout", "-l", "30", ruta, "-"],
                         capture_output=True, text=True, timeout=300).stdout
    filas = []
    for linea in txt.splitlines():
        m = FILA.match(linea)
        if not m:
            continue
        resto = m.group(3)
        tipo = next((t for t in TIPOS_CONOCIDOS if resto.endswith(t)), None)
        if tipo is None:
            tipo = re.split(r"\s{2,}", resto)[-1]
        nombre = resto[: len(resto) - len(tipo)].strip()
        filas.append({"id": m.group(1), "fecha": m.group(2), "nombre": nombre, "tipo": tipo})
    return filas


def paginas_por_documento(doc):
    """Id de documento -> lista de páginas (1-based), usando el pie de página."""
    ids = []
    for page in doc:
        r = page.rect
        txt = page.get_text(clip=fitz.Rect(0, r.height * 0.8, r.width, r.height))
        m = PIE.findall(txt) or PIE.findall(page.get_text())
        ids.append(m[-1][0] if m else None)
    # páginas sin pie entre dos páginas del mismo documento
    for i in range(1, len(ids) - 1):
        if ids[i] is None and ids[i - 1] and ids[i - 1] == ids[i + 1]:
            ids[i] = ids[i - 1]
    paginas = {}
    for i, d in enumerate(ids, 1):
        if d:
            paginas.setdefault(d, []).append(i)
    return ids, paginas


# réplicas de la parte demandante que mencionan la contestación
NO_CONTESTACION = re.compile(r"(impugna|replica|manifesta|resposta|sobre|juntada)\w*\W+(\w+\W+){0,5}contesta")


def puntaje_contestacion(fila, doc, paginas):
    """Señales de que el documento es la pieza de contestación (no un anexo).

    nombre del archivo con "contesta" = 4, primera página con "contestação" = 2,
    tipo "Contestação" en el índice = 1. Los bancos a veces marcan todos los
    anexos como Contestação, por eso el tipo solo es la señal más débil.
    """
    nombre = sin_acentos(fila["nombre"])
    if NO_CONTESTACION.search(nombre) or "replica" in nombre or fila["id"] not in paginas:
        return 0, ""
    if fila["tipo"] not in ("Contestação", "Petição", "Outros"):
        return 0, ""
    if re.search(r"\.(mp3|mp4|ogg|opus|wav|m4a|avi|mov)\b", nombre):
        return 0, ""
    inicio = sin_acentos(doc[paginas[fila["id"]][0] - 1].get_text()[:800])
    senales = []
    if "contesta" in nombre:
        senales.append("nombre")
    if (fila["tipo"] in ("Contestação", "Petição") and "juntada" not in nombre and re.search(r"\bcontestacao\b", inicio)
            and not NO_CONTESTACION.search(inicio)):
        senales.append("texto")
    if fila["tipo"] == "Contestação":
        senales.append("tipo")
    puntaje = 4 * ("nombre" in senales) + 2 * ("texto" in senales) + ("tipo" in senales)
    return puntaje, "+".join(senales)


def mejores(candidatas):
    """Se queda con las de mayor puntaje (la pieza de defensa frente a sus anexos)."""
    if not candidatas:
        return []
    p = max(x["puntaje"] for x in candidatas)
    return [x for x in candidatas if x["puntaje"] == p]


def rangos(pags):
    """[89, 90, 91, 95] -> [(89, 91), (95, 95)]"""
    out = []
    for p in pags:
        if out and p == out[-1][1] + 1:
            out[-1][1] = p
        else:
            out.append([p, p])
    return [tuple(r) for r in out]


def procesar_expediente(args):
    """Procesa todos los contratos de un mismo expediente."""
    ruta, contratos, tipos_extra = args
    resultados = []
    if not os.path.exists(ruta):
        return [dict(c, estado="sin_expediente", vinculo=None, salidas=[]) for c in contratos]
    filas = leer_indice(ruta)
    doc = fitz.open(ruta)
    ids, paginas = paginas_por_documento(doc)
    por_id = {f["id"]: f for f in filas}
    contestaciones = []
    for f in filas:
        puntaje, metodo = puntaje_contestacion(f, doc, paginas)
        if puntaje:
            contestaciones.append(dict(f, puntaje=puntaje, metodo=metodo, paginas=paginas[f["id"]]))
    extras = [dict(f, paginas=paginas[f["id"]]) for f in filas
              if f["tipo"] in tipos_extra and f["id"] in paginas]

    for c in contratos:
        r = dict(c, total_contestaciones=len(contestaciones), salidas=[])
        id_contrato = ids[c["pagina_inicio"] - 1] if c["pagina_inicio"] <= len(ids) else None
        fila_contrato = por_id.get(id_contrato)
        candidatas = [x for x in contestaciones if x["id"] != id_contrato]
        elegidas, vinculo = [], None
        if fila_contrato:
            elegidas = mejores([x for x in candidatas if x["fecha"] == fila_contrato["fecha"]])
            vinculo = "misma_presentacion" if elegidas else None
            if len(elegidas) > 1:
                # la misma pieza repetida: la que precede al contrato (es su anexo)
                previas = [x for x in elegidas if x["paginas"][0] < c["pagina_inicio"]]
                elegidas = [previas[-1] if previas else elegidas[0]]
        if not elegidas:
            previas = mejores([x for x in candidatas if x["paginas"][0] < c["pagina_inicio"]])
            if previas:
                elegidas, vinculo = [previas[-1]], "anterior_al_contrato"
        if not elegidas and candidatas:
            # el contrato vino con la petición inicial: contestaciones posteriores
            elegidas, vinculo = mejores(candidatas)[:1], "posterior_al_contrato"
        r["vinculo"] = vinculo
        r["estado"] = "ok" if elegidas else "sin_contestacion"
        for x in elegidas + extras:
            etiqueta = "contestacao" if x in elegidas else sin_acentos(x["tipo"]).replace(" ", "_")
            pags = x["paginas"]
            nombre = f"{c['contrato'][:-4]}__{etiqueta}_{x['id']}_p{pags[0]}-{pags[-1]}.pdf"
            destino = os.path.join(SALIDA, c["carpeta"], nombre)
            nuevo = fitz.open()
            for a, b in rangos(pags):
                nuevo.insert_pdf(doc, from_page=a - 1, to_page=b - 1)
            nuevo.save(destino, garbage=3, deflate=True)
            nuevo.close()
            r["salidas"].append({"archivo": destino, "tipo": x["tipo"], "id": x["id"],
                                 "fecha": x["fecha"], "nombre_original": x["nombre"],
                                 "metodo": x.get("metodo"), "puntaje": x.get("puntaje"), "paginas": rangos(pags)})
        resultados.append(r)
    doc.close()
    return resultados


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tipos", default="Contestação",
                    help="tipos a extraer, separados por coma; Contestação siempre se incluye")
    ap.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    args = ap.parse_args()
    tipos_extra = {t.strip() for t in args.tipos.split(",") if t.strip()} - {"Contestação"}

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
                "pagina_inicio": int(m.group(3)), "pagina_fin": int(m.group(4) or m.group(3)),
            })
    print(f"{sum(map(len, grupos.values()))} contratos en {len(grupos)} expedientes")

    trabajos = [(ruta, cs, tipos_extra) for ruta, cs in grupos.items()]
    resultados = []
    with ProcessPoolExecutor(args.workers) as ex:
        for res in ex.map(procesar_expediente, trabajos):
            resultados += res
    resultados.sort(key=lambda r: (r["carpeta"], r["contrato"]))
    with open(os.path.join(SALIDA, "manifiesto.jsonl"), "w") as out:
        for r in resultados:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"{'carpeta':22} {'contratos':>9} {'con contestación':>17} {'sin':>5}")
    for carpeta in CARPETAS:
        rs = [r for r in resultados if r["carpeta"] == carpeta]
        ok = sum(r["estado"] == "ok" for r in rs)
        print(f"{carpeta:22} {len(rs):9} {ok:17} {len(rs) - ok:5}")
    print("estados:", dict(Counter(r["estado"] for r in resultados)))
    print("vínculo:", dict(Counter(r["vinculo"] for r in resultados)))


if __name__ == "__main__":
    main()
