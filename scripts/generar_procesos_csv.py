"""Genera data/Contratos/procesos.csv: una fila por número de proceso.

Cruza los contratos de data/Contratos/<carpeta>/ con su petição inicial, contestação y
decisões usando los manifiestos de extraer_peticao.py, extraer_contestacao.py y
extraer_decisiones.py. Las columnas con varios valores son arrays JSON de strings.
"contratos" lleva cada contrato con su carpeta ("indices/NNN_..._pA-B.pdf"); los demás
archivos van sin ruta y están en data/Contratos/<carpeta>/<Subcarpeta>/.

usage: python3 scripts/generar_procesos_csv.py
"""

import csv
import json
import os

CONTRATOS = "data/Contratos"
SALIDA = os.path.join(CONTRATOS, "procesos.csv")
MANIFIESTOS = {
    "peticao": os.path.join(CONTRATOS, "manifiesto_peticao.jsonl"),
    "contestacao": "data/Contestacao/manifiesto.jsonl",
    "decision": "data/Decision/manifiesto.jsonl",
}
COLUMNAS = ["proceso", "carpetas", "n_contratos", "contratos", "peticao", "contestacao",
            "n_decisiones", "decisiones", "resultados", "estado_decision", "resultado_final"]


def leer(ruta):
    return [json.loads(linea) for linea in open(ruta)]


def unicos(salidas):
    """Un archivo por documento (mismo Id = misma pieza copiada junto a varios contratos)."""
    vistos = {}
    for s in salidas:
        vistos.setdefault(s["id"], s)
    return list(vistos.values())


def main():
    procesos = {}
    for clave, ruta in MANIFIESTOS.items():
        for r in leer(ruta):
            p = procesos.setdefault(r["proceso"], {"carpetas": set(), "contratos": set(),
                                                   "peticao": [], "contestacao": [], "decision": []})
            p["carpetas"].add(r["carpeta"])
            p["contratos"].add(f'{r["carpeta"]}/{r["contrato"]}')
            p[clave] += r.get("salidas") or r.get("decisiones") or []
            if clave == "decision":
                p["estado_decision"] = r["estado"]
                p["resultado_final"] = r["resultado_final"] or ""

    with open(SALIDA, "w", newline="") as out:
        w = csv.DictWriter(out, fieldnames=COLUMNAS)
        w.writeheader()
        for proceso in sorted(procesos):
            p = procesos[proceso]
            peticao = unicos(p["peticao"])
            decisiones = unicos(p["decision"])
            w.writerow({
                "proceso": proceso,
                "carpetas": json.dumps(sorted(p["carpetas"])),
                "n_contratos": len(p["contratos"]),
                "contratos": json.dumps(sorted(p["contratos"])),
                "peticao": os.path.basename(peticao[0]["archivo"]) if peticao else "",
                "contestacao": json.dumps([os.path.basename(s["archivo"])
                                           for s in unicos(p["contestacao"])]),
                "n_decisiones": len(decisiones),
                "decisiones": json.dumps([os.path.basename(s["archivo"]) for s in decisiones]),
                "resultados": json.dumps([s["resultado"] for s in decisiones]),
                "estado_decision": p["estado_decision"],
                "resultado_final": p["resultado_final"],
            })
    print(f"escrito {SALIDA}: {len(procesos)} procesos")


if __name__ == "__main__":
    main()
