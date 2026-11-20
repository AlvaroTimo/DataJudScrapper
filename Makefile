# Pipeline de contratos. Ver Makefile.md para el detalle de cada tarea.
#   make help
#   make <tarea> [VARIABLE=valor ...]

PY        ?= .venv/bin/python
SCRAPER   ?= .venv/bin/datajud-scraper
YEAR      ?= 2024
DATASET   ?= data/output/datasets/dataset_hasta2024.jsonl
FILTERED  ?= data/output/datasets/dataset_hasta$(YEAR).jsonl
MODEL     ?= qwen3-vl:8b-instruct-q8_0
CONTEXT   ?= 24576
BATCH     ?=
CONFIRM   ?=

TRANS_DIR  ?= data/output/transcripciones
CONTRATOS  ?= data/Contratos
DUPLICADOS ?= data/_duplicated_

.DEFAULT_GOAL := help
.PHONY: help filter extract-process-numbers extract-contracts anonymize \
        transcribe render dedupe pipeline-standardize pipeline-full check-confirm

help: ## Muestra las tareas disponibles
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-24s %s\n", $$1, $$2}'

# --- Tareas únicas -----------------------------------------------------------

filter: ## Filtra el dataset por año (YEAR=2024)
	$(PY) scripts/filter_dataset.py --hasta-anio $(YEAR) --dataset $(DATASET) --output-dir $(dir $(FILTERED))

extract-process-numbers: ## Genera procesos.csv con los números de proceso y sus documentos
	$(PY) scripts/generate_process_csv.py

extract-contracts: ## Extrae los contratos de los expedientes (sin anonimizar)
	$(SCRAPER) scrape-dataset $(FILTERED) --metadata $(basename $(FILTERED)).metadata.json \
	  --all --contract-mode extract --contract-retention keep \
	  --local-model $(MODEL) --local-context-tokens $(CONTEXT)

anonymize: check-confirm ## Anonimiza un lote extraído (BATCH=<batch_id>)
	@test -n "$(BATCH)" || (echo "Falta BATCH=<batch_id>"; exit 1)
	$(PY) scripts/reanonymize.py $(BATCH) --local-model $(MODEL) --local-context-tokens $(CONTEXT)
	$(PY) scripts/output_anonymized.py data/output/runs/$(BATCH)/anonimizacion.json $(BATCH)

transcribe: check-confirm ## Transcribe contratos con Claude (gasta tokens del plan)
	$(PY) scripts/contract_transcription/transcribe_batch.py data/recreacion_v2/src data/recreacion_v2/trans $(TRANS_DIR) --brief scripts/contract_transcription/transcription_brief.md

render: ## Convierte la transcripción en PDF estandarizado
	$(PY) -c "import glob,os,sys; sys.path.insert(0,'scripts/contract_transcription'); import render_pdf as R; [R.build(p, os.path.join('$(CONTRATOS)/Processed', os.path.basename(p)[:-4]+'.pdf')) for p in glob.glob('$(TRANS_DIR)/*.txt')]"

dedupe: ## Separa los PDFs con el mismo texto en _duplicated_
	$(PY) scripts/contract_transcription/find_duplicates.py $(CONTRATOS)


check-confirm:
	@test -n "$(CONFIRM)" || (echo "Esta tarea gasta tokens del plan. Repite con CONFIRM=1."; exit 1)

# --- Pipelines ---------------------------------------------------------------

pipeline-standardize: check-confirm transcribe render dedupe ## Transcribe, genera PDFs y separa duplicados

pipeline-full: check-confirm filter extract-process-numbers extract-contracts anonymize pipeline-standardize ## Todo el flujo, en orden (CONFIRM=1)
