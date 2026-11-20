# Makefile: contract pipeline

The whole workflow runs from the project root with `make`. There are two kinds of task:

- **Single tasks:** do one step (`make filter`, `make render`, ...).
- **Pipelines:** chain tasks in order (`make pipeline-full`, `make pipeline-standardize`).

Parameters are passed on the command line: `make <task> VARIABLE=value`.

## Quick start

```bash
make help                                            # list of tasks
make filter YEAR=2024                                # filter the dataset by year
make extract-process-numbers                         # generate procesos.csv
make anonymize BATCH=<batch_id>                      # anonymize an already extracted batch
make pipeline-standardize CONFIRM=1                  # transcribe, build PDFs, split duplicates
make pipeline-full CONFIRM=1 BATCH=<batch_id>        # full workflow, in order
```

## Parameters

| Variable | Default | Purpose |
|---|---|---|
| `YEAR` | `2024` | Cutoff year for `filter` (cases filed up to that year). |
| `DATASET` | `data/output/datasets/dataset_hasta2024.jsonl` | Source dataset. |
| `FILTERED` | `data/output/datasets/dataset_hasta$(YEAR).jsonl` | Filtered dataset used by `extract-contracts`. |
| `BATCH` | (empty) | Batch identifier for `anonymize`. Required there. |
| `MODEL` | `qwen3-vl:8b-instruct-q8_0` | Local model (Ollama) for extraction and anonymization. |
| `CONTEXT` | `24576` | Context tokens for the local model. |
| `TRANS_DIR` | `data/output/transcripciones` | Where transcriptions are stored. |
| `CONTRATOS` | `data/Contratos` | Root of the final structure. |
| `DUPLICADOS` | `data/_duplicated_` | Duplicates folder. |
| `CONFIRM` | (empty) | Tasks that spend plan tokens require `CONFIRM=1`. |

## Single tasks

| Task | What it does | Script | Spends tokens |
|---|---|---|---|
| `filter` | Keeps only the cases up to `YEAR`. | `scripts/filter_dataset.py` | No |
| `extract-process-numbers` | Generates `procesos.csv`: one case per row, with its contracts, defenses (contestações), decisions and initial petition. | `scripts/generate_process_csv.py` | No |
| `extract-contracts` | Extracts the contracts of the filtered cases, without anonymizing. Uses the scraper in `extract` mode. | `datajud-scraper scrape-dataset` | No (uses the local model) |
| `anonymize` | Anonymizes an already extracted batch and writes `data/output_anonim/`. | `scripts/reanonymize.py`, `scripts/output_anonymized.py` | No (uses the local model) |
| `transcribe` | Transcribes each contract with Claude, in the standard format. | `scripts/contract_transcription/transcribe_batch.py` | **Yes** (`CONFIRM=1`) |
| `render` | Converts each transcription into a PDF in `Contratos/Processed/`. | `scripts/contract_transcription/render_pdf.py` | No |
| `dedupe` | Moves PDFs with the same text into `_duplicated_/`. | `scripts/contract_transcription/find_duplicates.py` | No |

## Pipelines

| Pipeline | Tasks, in order | Spends tokens |
|---|---|---|
| `pipeline-standardize` | `transcribe` → `render` → `dedupe` | Yes (`CONFIRM=1`) |
| `pipeline-full` | `filter` → `extract-process-numbers` → `extract-contracts` → `anonymize` → `pipeline-standardize` | Yes (`CONFIRM=1`) |

`make pipeline-full` no longer includes summaries: that script was removed.

## Output structure

Everything lives inside these five folders:

```
data/
├── _duplicated_/            duplicates by text (PDFs)
├── Contratos/               final structure
│   ├── Contratos/           original contracts (scanned inputs)
│   ├── Processed/           standardized PDFs
│   ├── Peticao/             initial petitions
│   ├── Contestacao/         defenses (contestações)
│   ├── Decision/            decisions
│   ├── Resumenes/           summaries (Contratos/, Casos/, Peticao/, Contestacao/, Decision/)
│   ├── procesos.csv
│   └── resumen_procesamiento.pdf
├── dataset-contracts/       PDFs of the downloaded cases
├── output/                  extraction, transcriptions and working datasets
└── output_anonim/           anonymized contracts (reportN/)
```

## Pending

1. **`transcribe` needs the pages of each contract** in `data/recreacion_v2/src/` (or the folder passed in). That page-extraction step was done with loose code and is not in a script; it has to be added before the transcription can be reproduced from scratch.
2. **`anonymize` depends on the batch path.** It expects `data/output/runs/<BATCH>/anonimizacion.json`; confirm that `reanonymize.py` writes there.
3. **The `filter` default (`YEAR=2024`) is an assumption.** Confirm the year you want.

## Notes

- Tasks that spend tokens stop unless you pass `CONFIRM=1`, so they are not launched by accident.
- To see what a task would do without running it: `make -n <task>`.
