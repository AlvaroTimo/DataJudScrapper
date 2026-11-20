# Makefile: pipeline de contratos

Todo el flujo se lanza desde la raíz del proyecto con `make`. Hay dos tipos de tarea:

- **Tareas únicas:** hacen un solo paso (`make filter`, `make render`…).
- **Pipelines:** encadenan tareas en orden (`make pipeline`, `make standardize`).

Los parámetros se pasan desde la línea de comandos: `make <tarea> VARIABLE=valor`.

## Uso rápido

```bash
make help                                  # lista de tareas
make filter YEAR=2024                      # filtra el dataset por año
make extract-process-numbers               # genera procesos.csv
make anonymize BATCH=<batch_id>            # anonimiza un lote ya extraído
make standardize CONFIRM=1                 # transcribe, genera PDFs y separa duplicados
make pipeline CONFIRM=1 BATCH=<batch_id>   # flujo completo, en orden
```

## Parámetros

| Variable | Valor por defecto | Para qué |
|---|---|---|
| `YEAR` | `2024` | Año límite para `filter` (expedientes presentados hasta ese año). |
| `DATASET` | `data/output/datasets/dataset_hasta2024.jsonl` | Dataset de origen. |
| `FILTERED` | `data/output/datasets/dataset_hasta$(YEAR).jsonl` | Dataset filtrado que usan `extract-contracts`. |
| `BATCH` | (vacío) | Identificador del lote para `anonymize`. Obligatorio ahí. |
| `MODEL` | `qwen3-vl:8b-instruct-q8_0` | Modelo local (Ollama) para extracción y anonimización. |
| `CONTEXT` | `24576` | Tokens de contexto del modelo local. |
| `TRANS_DIR` | `data/output/transcripciones` | Dónde se guardan las transcripciones. |
| `CONTRATOS` | `data/Contratos` | Raíz de la estructura final. |
| `DUPLICADOS` | `data/_duplicated_` | Carpeta de duplicados. |
| `CONFIRM` | (vacío) | Tareas que gastan tokens del plan piden `CONFIRM=1`. |

## Tareas únicas

| Tarea | Qué hace | Script | Gasta tokens |
|---|---|---|---|
| `filter` | Deja solo los expedientes hasta `YEAR`. | `scripts/filter_dataset.py` | No |
| `extract-process-numbers` | Genera `procesos.csv`: un proceso por fila, con sus contratos, contestações, decisiones y petição. | `scripts/generate_process_csv.py` | No |
| `extract-contracts` | Extrae los contratos de los expedientes filtrados, sin anonimizar. Usa el scraper en modo `extract`. | `datajud-scraper scrape-dataset` | No (usa el modelo local) |
| `anonymize` | Anonimiza un lote ya extraído y genera `data/output_anonim/`. | `scripts/reanonymize.py`, `scripts/output_anonymized.py` | No (usa el modelo local) |
| `transcribe` | Transcribe cada contrato con Claude, en el formato estándar. | `scripts/contract_transcription/transcribe_batch.py` | **Sí** (`CONFIRM=1`) |
| `render` | Convierte cada transcripción en un PDF en `Contratos/Processed/`. | `scripts/contract_transcription/render_pdf.py` | No |
| `dedupe` | Separa los PDFs con el mismo texto en `_duplicated_/`. | `scripts/contract_transcription/find_duplicates.py` | No |
| `summarize` | Resúmenes de contrato y de caso. | — | **Sí** |

## Pipelines

| Pipeline | Tareas, en orden | Gasta tokens |
|---|---|---|
| `standardize` | `transcribe` → `render` → `dedupe` | Sí (`CONFIRM=1`) |
| `pipeline` | `filter` → `extract-process-numbers` → `extract-contracts` → `anonymize` → `standardize` | Sí (`CONFIRM=1`) |

`make pipeline` **no** incluye `summarize`, porque todavía no está disponible (ver pendientes).

## Estructura de salida

Todo queda dentro de estas cinco carpetas:

```
data/
├── _duplicated_/            duplicados por texto (PDFs)
├── Contratos/               estructura final
│   ├── Contratos/           contratos originales (escaneos de entrada)
│   ├── Processed/           PDFs estandarizados
│   ├── Peticao/             petições iniciais
│   ├── Contestacao/         contestações
│   ├── Decision/            decisiones
│   ├── Resumenes/           resúmenes (Contratos/, Casos/, Peticao/, Contestacao/, Decision/)
│   ├── procesos.csv
│   └── resumen_procesamiento.pdf
├── dataset-contracts/       PDFs de los expedientes descargados
├── output/                  extracción, transcripciones y datasets de trabajo
└── output_anonim/           contratos anonimizados (reportN/)
```

## Pendientes

1. **`transcribe` necesita las páginas de cada contrato** en `data/recreacion_v2/src/` (o la carpeta que se indique). Ese paso de extracción de páginas se hizo con código suelto y no está en un script; hay que añadirlo antes de poder reproducir la transcripción desde cero.
2. **`summarize` no está disponible.** El script `resumir.py` está en `scripts/_muertos/` y depende de `data/backup/Resumenes_v1/`, que ya no existe. Hay que restaurarlo y cambiar esa dependencia, si se van a generar resúmenes nuevos.
3. **`anonymize` depende de la ruta del lote.** Espera `data/output/runs/<BATCH>/anonimizacion.json`; hay que confirmar que `reanonymize.py` escribe ahí.
4. **Los filtros de `filter` por defecto** (`YEAR=2024`) son una suposición: confirma el año que quieres.

## Notas

- Las tareas que gastan tokens se detienen si no pasas `CONFIRM=1`, para no lanzarlas por accidente.
- Para ver qué haría una tarea sin ejecutarla: `make -n <tarea>`.
