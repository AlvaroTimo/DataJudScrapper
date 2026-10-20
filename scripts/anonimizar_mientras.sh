#!/usr/bin/env bash
# Anonymize a batch while it is still being extracted: every 30 minutes, anonymize the
# contracts extracted since the previous pass (scripts/reanonimizar.py skips the done ones
# and waits on its lock if another pass is running). Stops when the resume script ends.
#   setsid nohup scripts/anonimizar_mientras.sh BATCH_ID RUN_DIR >/dev/null 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/.."
BATCH=$1
RUN_DIR=${2%/}
LOG="$RUN_DIR/anonimizacion-continua.log"
while pgrep -f "reanudar_completo.sh $BATCH|run_completo.sh" >/dev/null; do
  echo "[$(date '+%F %T')] pasada de anonimización" >>"$LOG"
  .venv/bin/python scripts/reanonimizar.py "$BATCH" --output "$RUN_DIR/anonimizacion.json" \
    --local-model "${MODEL:-qwen3-vl:8b-instruct-q8_0}" \
    --local-context-tokens "${CONTEXT:-24576}" >>"$LOG" 2>&1
  sleep 1800
done
echo "[$(date '+%F %T')] fin: la corrida principal terminó" >>"$LOG"
