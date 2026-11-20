import glob
import re
import sys

SUBS = r"(?:buscar_texto|contratos_genericos|indices|primera_pagina)"

def clean(text):
    text = re.sub(r"\n## Observações\n.*?(?=\n## |\n---\n|\Z)", "\n", text, flags=re.S)
    text = re.sub(r"\n---\n\*Resumo gerado automaticamente.*\Z", "\n", text, flags=re.S)
    text = re.sub(rf"Resumenes/{SUBS}/(Peticao|Contestacao|Decision)/", r"Resumenes/\1/", text)
    text = re.sub(rf"Resumenes/{SUBS}/", "Resumenes/Contratos/", text)
    text = re.sub(rf"(?<![\w/]){SUBS}/(Peticao|Contestacao|Decision)/", r"\1/", text)
    text = re.sub(rf"(?<![\w/]){SUBS}/proc+essed/", "Processed/", text)
    text = re.sub(rf"(?<![\w/]){SUBS}/(?=[\w.-]+\.pdf)", "Contratos/", text)
    return re.sub(r"\n{3,}", "\n\n", text).rstrip() + "\n"

if __name__ == "__main__":
    n = 0
    for p in glob.glob(f"{sys.argv[1]}/**/*.md", recursive=True):
        if p.endswith("LEEME.md"):
            continue
        old = open(p, encoding="utf-8").read()
        new = clean(old)
        if new != old:
            open(p, "w", encoding="utf-8").write(new)
            n += 1
    print("resúmenes actualizados:", n)
