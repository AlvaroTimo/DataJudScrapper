#!/usr/bin/env bash
# Extraction of a sample (scripts/run_muestra_100.sh, MODE=extract) followed by the
# anonymization of the extracted contracts:
#   data/output/reportN          extracted contracts, split by method
#   data/output_anonim/reportN   the same contracts anonymized, with resumen.md
# Example:
#   LIMIT=1000 DATASET=data/datasets/dataset_hasta2024.jsonl \
#     setsid nohup scripts/run_completo.sh >/dev/null 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/.."

MODE=extract scripts/run_muestra_100.sh
CODE=$?
RUN_DIR=$(ls -td data/runs/muestra-"${LIMIT:-100}"-*/ | head -1)
LOG="${RUN_DIR}ejecucion.log"
log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
# 0 = all completed, 1 = completed with cases in review; anything else is unfinished.
if [ "$CODE" != 0 ] && [ "$CODE" != 1 ]; then
  log "La extracción no terminó (código $CODE); no se anonimiza. Reanudar y volver a lanzar."
  exit "$CODE"
fi
BATCH=$(cat "${RUN_DIR}batch_id.txt")
REPORT=$(basename "$(ls -td data/output/report*/ | head -1)")
log "Anonimización del lote $BATCH → data/output_anonim/$REPORT"

INHIBIT=()
if systemd-inhibit --what=sleep:idle --who=datajud-scraper --why=check true 2>/dev/null; then
  INHIBIT=(systemd-inhibit --what=sleep:idle --who=datajud-scraper --why="anonimizacion $REPORT")
fi
"${INHIBIT[@]}" .venv/bin/python scripts/reanonimizar.py "$BATCH" \
  --output "${RUN_DIR}anonimizacion.json" \
  --local-model "${MODEL:-qwen3-vl:8b-instruct-q8_0}" \
  --local-context-tokens "${CONTEXT:-24576}" 2>&1 | tee -a "$LOG"
.venv/bin/python scripts/output_anonim.py "${RUN_DIR}anonimizacion.json" "$BATCH" \
  --report "$REPORT" 2>&1 | grep -v "MuPDF error" | tee -a "$LOG"
log "Fin: data/output/$REPORT y data/output_anonim/$REPORT"
