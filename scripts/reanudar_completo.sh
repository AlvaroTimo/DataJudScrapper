#!/usr/bin/env bash
# Resume a paused batch started by scripts/run_completo.sh and finish it the same way:
# wait for PROJUDI and resume (again after every new outage, retrying failed cases),
# rewrite data/output/REPORT and
# anonymize into data/output_anonim/REPORT.
#   setsid nohup scripts/reanudar_completo.sh BATCH_ID report4 >/dev/null 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/.."
BATCH=$1
REPORT=$2
MODEL="${MODEL:-qwen3-vl:8b-instruct-q8_0}"
CONTEXT="${CONTEXT:-24576}"
RUN_DIR="$(dirname "$(grep -l "$BATCH" data/runs/*/batch_id.txt | head -1)")/"
LOG="${RUN_DIR}ejecucion.log"
log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
INPUTS=$(grep -o 'entrada en `../[^/`]*/`' "data/output/$REPORT/resumen.md" | sed 's#.*\.\./\(.*\)/`#\1#')

INHIBIT=()
if systemd-inhibit --what=sleep:idle --who=datajud-scraper --why=check true 2>/dev/null; then
  INHIBIT=(systemd-inhibit --what=sleep:idle --who=datajud-scraper --why="reanudar $REPORT")
fi

# Wait for PROJUDI (a court outage answers 503: one HEAD request every 10 minutes) and
# resume; when the court fails again the batch pauses (code 3) and this loop waits again.
CODE=3
for _ in $(seq 20); do
  for _ in $(seq 288); do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 30 -I https://projudi.tjba.jus.br/projudi/)
    case "$STATUS" in 2??|3??) break ;; esac
    log "PROJUDI responde $STATUS; nuevo intento en 10 minutos"
    sleep 600
  done
  log "Reanudando el lote $BATCH (reintenta los fallidos)"
  "${INHIBIT[@]}" .venv/bin/datajud-scraper resume "$BATCH" --contract-mode extract \
    --contract-retention keep --local-model "$MODEL" --local-context-tokens "$CONTEXT" \
    --retry-failed 2>&1 | tee -a "$LOG"
  CODE=${PIPESTATUS[0]}
  log "Fin de la extracción: código $CODE"
  [ "$CODE" = 0 ] || [ "$CODE" = 1 ] && break
  sleep 600
done
if [ "$CODE" != 0 ] && [ "$CODE" != 1 ]; then
  log "El lote sigue en pausa tras varios intentos; lanzar de nuevo este script más tarde."
  exit "$CODE"
fi

.venv/bin/python scripts/sample_report.py "$BATCH" 2>&1 | tee -a "$LOG"
.venv/bin/python scripts/output_report.py "$BATCH" --inputs "$INPUTS" --report "$REPORT" \
  2>&1 | grep -v "MuPDF error" | tee -a "$LOG"
log "Anonimización del lote $BATCH → data/output_anonim/$REPORT"
"${INHIBIT[@]}" .venv/bin/python scripts/reanonimizar.py "$BATCH" \
  --output "${RUN_DIR}anonimizacion.json" --local-model "$MODEL" \
  --local-context-tokens "$CONTEXT" 2>&1 | tee -a "$LOG"
.venv/bin/python scripts/output_anonim.py "${RUN_DIR}anonimizacion.json" "$BATCH" \
  --report "$REPORT" 2>&1 | grep -v "MuPDF error" | tee -a "$LOG"
log "Fin: data/output/$REPORT y data/output_anonim/$REPORT"
