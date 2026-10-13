#!/usr/bin/env bash
# Runs the 100-case evaluation sample with the 8B model and keeps everything in data/runs/.
#
#   scripts/run_muestra_100.sh                      foreground
#   setsid nohup scripts/run_muestra_100.sh >/dev/null 2>&1 &
#                                                   survives closing the terminal
#   LIMIT=5 scripts/run_muestra_100.sh              smaller dry run
#   MODE=extract scripts/run_muestra_100.sh         extraction only (no anonymization)
#   DATASET=data/datasets/dataset_hasta2024.jsonl   another dataset (scripts/filtrar_dataset.py)
#
# Below 245 cases (the dataset's strata) "stratified" falls back to a seeded uniform sample:
# its first 5 cases are the pilot already processed, which are reused from cache.
set -uo pipefail
cd "$(dirname "$0")/.."

MODEL="${MODEL:-qwen3-vl:8b-instruct-q8_0}"
CONTEXT="${CONTEXT:-24576}"
LIMIT="${LIMIT:-100}"
SEED="${SEED:-20261009}"
MODE="${MODE:-both}"
DATASET="${DATASET:-external/dataset/dataset.jsonl}"
# Input PDFs are shared by every report of the same sample (same dataset, seed and size).
TAG="$(basename "$DATASET" .jsonl)"
if [ "$TAG" = dataset ] && [ "$SEED" = 20261009 ]; then INPUTS="${LIMIT}-pdfs"
else INPUTS="${LIMIT}-pdfs-${TAG#dataset_}-seed${SEED}"; fi
RUN_DIR="data/runs/muestra-${LIMIT}-$(date +%Y%m%d-%H%M%S)"
LOG="$RUN_DIR/ejecucion.log"
mkdir -p "$RUN_DIR"
touch "$RUN_DIR/.inicio"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

log "Inicio: $LIMIT expedientes · modelo $MODEL · contexto $CONTEXT · semilla $SEED · modo $MODE · dataset $DATASET"
log "Commit: $(git rev-parse --short HEAD) · registro: $LOG"
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader >>"$LOG" 2>&1

if ! curl -sf http://127.0.0.1:11434/api/version >/dev/null; then
  log "Ollama no responde; iniciándolo con setup_local_model.py"
  .venv/bin/python scripts/setup_local_model.py --model "$MODEL" 2>&1 | tee -a "$LOG"
fi

COMMAND=(
  .venv/bin/datajud-scraper scrape-dataset "$DATASET"
  --metadata "${DATASET%.jsonl}.metadata.json"
  --limit "$LIMIT" --sample stratified --seed "$SEED"
  --contract-mode "$MODE" --contract-retention keep
  --local-model "$MODEL" --local-context-tokens "$CONTEXT"
)
log "Comando: ${COMMAND[*]}"

# Keep the machine awake: an overnight run must not stop on suspend. Over SSH polkit may
# refuse the inhibitor; then run without it rather than not at all.
if systemd-inhibit --what=sleep:idle --who=datajud-scraper --why=check true 2>/dev/null; then
  COMMAND=(systemd-inhibit --what=sleep:idle --who=datajud-scraper
    --why="muestra de $LIMIT expedientes" "${COMMAND[@]}")
else
  log "Sin systemd-inhibit: el equipo podría suspenderse durante la ejecución"
fi
"${COMMAND[@]}" 2>&1 | tee -a "$LOG"
CODE=${PIPESTATUS[0]}

case $CODE in
  0) MEANING="lote completado" ;;
  1) MEANING="completado con fallos o casos en revisión" ;;
  2) MEANING="entrada o configuración inválida" ;;
  3) MEANING="lote en pausa" ;;
  5) MEANING="fallo de almacenamiento" ;;
  130) MEANING="interrumpido (Ctrl-C)" ;;
  *) MEANING="error inesperado" ;;
esac
log "Fin: código $CODE ($MEANING)"

BATCH=$(grep -o '"batch_id": "[0-9a-f-]*"' "$LOG" | tail -n 1 | cut -d'"' -f4)
if [ -z "$BATCH" ]; then
  # Errors print no summary; take the batch folder created during this run, if any.
  BATCH=$(find data/reports -mindepth 1 -maxdepth 1 -type d -newer "$RUN_DIR/.inicio" \
    -printf '%T@ %f\n' | sort -n | tail -n 1 | cut -d' ' -f2)
fi
if [ -z "$BATCH" ]; then
  log "No se creó ningún lote; revisar el registro."
  exit "$CODE"
fi
echo "$BATCH" >"$RUN_DIR/batch_id.txt"
log "Lote: $BATCH"

RESUME=".venv/bin/datajud-scraper resume $BATCH --contract-mode $MODE --contract-retention keep"
RESUME+=" --local-model $MODEL --local-context-tokens $CONTEXT"
if [ "$CODE" = 3 ] || [ "$CODE" = 130 ]; then
  log "Para continuar: $RESUME"
fi
if [ "$CODE" = 1 ]; then
  log "Para reintentar los fallidos: $RESUME --retry-failed"
fi

if .venv/bin/python scripts/sample_report.py "$BATCH" 2>&1 | tee -a "$LOG"; then
  for file in report.md summary.json evaluacion/resumen.md evaluacion/revision.csv; do
    cp "data/reports/$BATCH/$file" "$RUN_DIR/" 2>/dev/null
  done
  log "Reporte: data/reports/$BATCH/evaluacion/resumen.md (copia en $RUN_DIR)"
else
  log "Falló el reporte; ejecutar: .venv/bin/python scripts/sample_report.py $BATCH"
fi
if OUTPUT=$(.venv/bin/python scripts/output_report.py "$BATCH" --inputs "$INPUTS" 2>&1); then
  log "Salida simple: $OUTPUT"
else
  log "Falló la salida simple; ejecutar: .venv/bin/python scripts/output_report.py $BATCH --inputs $INPUTS"
fi
exit "$CODE"
