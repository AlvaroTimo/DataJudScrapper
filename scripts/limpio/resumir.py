import argparse
import concurrent.futures as cf
import csv
import glob
import json
import os
import subprocess
import threading
import time

ROOT = "data"
RES = f"{ROOT}/Contratos/Resumenes"
OLD = f"{ROOT}/backup/Resumenes_v1"
TRANS = f"{ROOT}/recreacion_v2/trans"
STATE = f"{ROOT}/recreacion_v2/resumenes_state"
SUBS = ["buscar_texto", "contratos_genericos", "indices", "primera_pagina"]

COMMON = """Você reescreve resumos em português do Brasil para um painel jurídico (legalXplain), em Markdown.
Regras gerais:
- Siga EXATAMENTE a estrutura do RESUMO ANTERIOR (mesmos títulos de seção, mesma tabela, mesmos campos de
  "Detalhes", mesmo estilo), mas com o conteúdo atualizado a partir das fontes novas.
- NÃO inclua a seção "## Observações" nem nenhum rodapé ("---", "Resumo gerado automaticamente…").
- Não inclua dados pessoais: nomes de pessoas físicas, CPF, RG, endereços, telefones, e-mails, números de
  conta, de benefício, de contrato ou de proposta. Nas fontes eles aparecem como "[…]": não tente adivinhá-los.
  Valores, taxas, CET, tarifas, limites, margem, saque e parcelas NÃO são dados pessoais: informe-os.
- Não invente nada que não esteja nas fontes. Responda só com o Markdown do resumo, sem cercas de código.
"""

PROMPT_CONTRATO = COMMON + """
Tarefa: resumo de UM contrato bancário. A fonte principal é a NOVA TRANSCRIÇÃO do contrato (marcação de
linhas: @title título, @sec seção, @cl/@clp cláusula, @sub/@subp subcláusula, @i1 item, @f campo, @p
parágrafo; **negrito**; {{original||corrigido}} = correção tipográfica; […] = dado pessoal ocultado ou trecho
ilegível). A transcrição nova é muito mais completa que a antiga: atualize data, valores, taxas, CET,
tarifas, forma de assinatura, saque etc. sempre que agora aparecerem. "Qualidade do texto": "completa" se o
texto do contrato está todo legível, "parcial" se há trechos ilegíveis relevantes.
Citações de página "*(p. N)*": mantenha as do resumo anterior quando o conteúdo citado for o mesmo; para
informação nova sem página conhecida, não ponha página.
A seção final deve ser exatamente:

- `Contratos/{name}.pdf`
- `Processed/{name}.pdf`
"""

PROMPT_CASO = COMMON + """
Tarefa: resumo de UM processo (caso) inteiro. Fontes: os resumos atuais de cada documento do processo
(contratos — já atualizados a partir da transcrição completa —, petição inicial, contestações, decisões).
Atualize tudo o que dependa do contrato (valores, taxas, CET, data, forma de assinatura, prova do contrato).
Mantenha os mesmos doc_ids (`doc_…`) do resumo anterior nas citações e em "Documentos-chave".
A seção final deve ser exatamente a seção "## Documentos do caso" abaixo, sem alterações:
{docs}
"""

def find_trans(name):
    for s in SUBS:
        p = f"{TRANS}/{s}/{name}.txt"
        if os.path.exists(p):
            return s, p
    return None, None

def contrato_job(name):
    sub, tp = find_trans(name)
    old = f"{OLD}/{sub}/{name}.md"
    src = open(tp, encoding="utf-8").read()
    prev = open(old, encoding="utf-8").read() if os.path.exists(old) else "(não há)"
    system = PROMPT_CONTRATO.replace("{name}", name)
    user = f"RESUMO ANTERIOR (estrutura de referência):\n\n{prev}\n\n=====\nNOVA TRANSCRIÇÃO DO CONTRATO {name}:\n\n{src}"
    return system, user, f"{RES}/Contratos/{name}.md"

def caso_job(row):
    proc = row["proceso"]
    old = open(f"{OLD}/Casos/{proc}.md", encoding="utf-8").read()
    docs = old.split("## Documentos do caso", 1)[1].split("\n---\n")[0].strip() if "## Documentos do caso" in old else ""
    from limpiar_resumenes import clean  # same path rewrite as the local cleanup
    docs = "## Documentos do caso\n\n" + clean(docs).strip()
    parts = []
    for line in docs.splitlines():
        if "`Resumenes/" in line:
            rel = line.split("`Resumenes/", 1)[1].split("`", 1)[0]
            p = f"{RES}/{rel}"
            if os.path.exists(p):
                parts.append(f"### {line.strip('- ').split(':')[0]} — {rel}\n\n" + open(p, encoding="utf-8").read())
    system = PROMPT_CASO.replace("{docs}", docs)
    user = f"RESUMO ANTERIOR DO CASO (estrutura de referência):\n\n{old}\n\n=====\nRESUMOS ATUAIS DOS DOCUMENTOS:\n\n" + "\n\n".join(parts)
    return system, user, f"{RES}/Casos/{proc}.md"

def call(system, user, model):
    cmd = ["claude", "-p", "--model", model, "--tools", "", "--strict-mcp-config", "--system-prompt", system,
           "--output-format", "json", "--no-session-persistence", "--setting-sources", ""]
    r = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=1800, cwd=os.path.dirname(os.path.abspath(__file__)))
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        return False, (r.stdout + r.stderr)[-300:]
    if out.get("is_error"):
        return False, str(out.get("result"))[-300:]
    return True, out.get("result", "")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["contratos", "casos"])
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--pause", type=float, default=10)
    a = ap.parse_args()
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    state = f"{STATE}/{a.kind}"
    os.makedirs(state, exist_ok=True)
    if a.kind == "contratos":
        keys = sorted(os.path.basename(p)[:-4] for p in glob.glob(f"{TRANS}/*/*.txt") if not p.endswith(".raw.txt"))
        make = contrato_job
        items = keys
    else:
        rows = list(csv.DictReader(open(f"{ROOT}/Contratos/procesos.csv", encoding="utf-8")))
        rows = [r for r in rows if os.path.exists(f"{OLD}/Casos/{r['proceso']}.md")]
        keys = [r["proceso"] for r in rows]
        make = caso_job
        items = rows
    todo = [(k, it) for k, it in zip(keys, items) if not os.path.exists(f"{state}/{k}.done")]
    if a.limit:
        todo = todo[:a.limit]
    print(f"{time.strftime('%H:%M')} {len(keys)} {a.kind}, {len(todo)} por hacer", flush=True)
    paused, probe = threading.Event(), threading.Lock()

    def limited(msg):
        return any(k in msg.lower() for k in ("limit", "rate_limit", "overloaded", "429", "529"))

    def one(job):
        key, it = job
        system, user, out = make(it)
        while True:
            while paused.is_set():
                if probe.acquire(blocking=False):
                    try:
                        time.sleep(a.pause * 60)
                        ok, res = call(system, user, a.model)
                        if ok or not limited(res):
                            paused.clear()
                            print(f"{time.strftime('%H:%M')} resumed", flush=True)
                            break
                    finally:
                        probe.release()
                else:
                    time.sleep(20)
            else:
                ok, res = call(system, user, a.model)
            if not ok and limited(res):
                if not paused.is_set():
                    print(f"{time.strftime('%H:%M')} usage limit reached — pausing", flush=True)
                paused.set()
                continue
            break
        text = res.strip()
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[1] if "\n" in text else text
        if not ok or not text.startswith("# ") or "## Resumo" not in text:
            return key, False, (res or "")[:200]
        open(out, "w", encoding="utf-8").write(text.rstrip() + "\n")
        open(f"{state}/{key}.done", "w").write(time.strftime("%F %T"))
        return key, True, ""

    fails = 0
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for key, ok, msg in ex.map(one, todo):
            print(f"{time.strftime('%H:%M')} {'OK  ' if ok else 'FAIL'} {key} {msg}", flush=True)
            fails += not ok
    print(f"{time.strftime('%H:%M')} done, {fails} failed", flush=True)

if __name__ == "__main__":
    main()
