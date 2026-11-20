#!/usr/bin/env bash
# Extrae todo el dataset y luego anonimiza los contratos extraídos:
#   data/output/reportN          contratos extraídos, separados por método
#   data/output_anonim/reportN   los mismos contratos anonimizados, con resumen.md
#   LIMIT=1000 DATASET=data/output/datasets/dataset_hasta2024.jsonl \
#     setsid nohup scripts/run_full_pipeline.sh >/dev/null 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/.."

MODEL="${MODEL:-qwen3-vl:8b-instruct-q8_0}"
CONTEXT="${CONTEXT:-24576}"
MODE="${MODE:-both}"
DATASET="${DATASET:-data/output/datasets/dataset_hasta2024.jsonl}"
RUN_DIR="data/output/runs/completo-$(date +%Y%m%d-%H%M%S)/"
LOG="${RUN_DIR}ejecucion.log"
mkdir -p "$RUN_DIR"
log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

log "Inicio: dataset completo · modelo $MODEL · contexto $CONTEXT · modo $MODE · dataset $DATASET"
log "Commit: $(git rev-parse --short HEAD) · registro: $LOG"
if ! curl -sf http://127.0.0.1:11434/api/version >/dev/null; then
  log "Ollama no responde; iniciándolo con setup_local_model.py"
  .venv/bin/python scripts/setup_local_model.py --model "$MODEL" 2>&1 | tee -a "$LOG"
fi

COMMAND=(
  .venv/bin/datajud-scraper scrape-dataset "$DATASET"
  --metadata "${DATASET%.jsonl}.metadata.json"
  --all --contract-mode "$MODE" --contract-retention keep
  --local-model "$MODEL" --local-context-tokens "$CONTEXT"
)
if systemd-inhibit --what=sleep:idle --who=datajud-scraper --why=check true 2>/dev/null; then
  COMMAND=(systemd-inhibit --what=sleep:idle --who=datajud-scraper --why="dataset completo" "${COMMAND[@]}")
else
  log "Sin systemd-inhibit: el equipo podría suspenderse durante la ejecución"
fi
log "Comando: ${COMMAND[*]}"
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
  log "No se creó ningún lote; revisar el registro."
  exit "$CODE"
fi
echo "$BATCH" >"${RUN_DIR}batch_id.txt"
log "Lote: $BATCH"
if [ "$CODE" = 3 ] || [ "$CODE" = 130 ]; then
  log "Para continuar: .venv/bin/datajud-scraper resume $BATCH --contract-mode $MODE --contract-retention keep --local-model $MODEL --local-context-tokens $CONTEXT"
fi
if [ "$CODE" = 1 ]; then
  log "Para reintentar los fallidos: .venv/bin/datajud-scraper resume $BATCH --contract-mode $MODE --contract-retention keep --local-model $MODEL --local-context-tokens $CONTEXT --retry-failed"
fi

REPORT=$(basename "$(ls -td data/output/report*/ 2>/dev/null | head -1)")
log "Anonimización del lote $BATCH → data/output_anonim/$REPORT"
INHIBIT=()
if systemd-inhibit --what=sleep:idle --who=datajud-scraper --why=check true 2>/dev/null; then
  INHIBIT=(systemd-inhibit --what=sleep:idle --who=datajud-scraper --why="anonimizacion $REPORT")
fi
"${INHIBIT[@]}" .venv/bin/python scripts/reanonymize.py "$BATCH" \
  --output "${RUN_DIR}anonimizacion.json" \
  --local-model "$MODEL" --local-context-tokens "$CONTEXT" 2>&1 | tee -a "$LOG"
.venv/bin/python scripts/output_anonymized.py "${RUN_DIR}anonimizacion.json" "$BATCH" \
  --report "$REPORT" 2>&1 | grep -v "MuPDF error" | tee -a "$LOG"
log "Fin: data/output/$REPORT y data/output_anonim/$REPORT"
exit "$CODE"
