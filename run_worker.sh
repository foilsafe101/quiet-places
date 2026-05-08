#!/bin/bash
# Single-process supervisor for the OpenSky query worker.
# Uses a PID file in /tmp/shhh-worker.pid so any session can check status.
# Replaces run_batches.sh (which left zombie bash loops across sessions).

set -e
cd /Users/Shared/shhh
VENV=.venv/bin/python
BATCH=200
PIDFILE=/tmp/shhh-worker.pid
LOG=/tmp/shhh-worker.log

# Refuse to start if another instance is already running
if [ -f "$PIDFILE" ]; then
  OLD_PID=$(cat "$PIDFILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "ERROR: worker already running as PID $OLD_PID. Stop it first with:"
    echo "  kill \$(cat $PIDFILE)"
    exit 1
  fi
  rm -f "$PIDFILE"
fi

# Belt-and-suspenders: refuse if any python query worker is alive,
# even if the PID file is missing or stale (avoids overlap with OpenSky)
if pgrep -f "query_opensky_paths" >/dev/null; then
  echo "ERROR: a python query worker is already running. PIDs:"
  pgrep -fl "query_opensky_paths"
  echo "Stop it first."
  exit 1
fi

echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"; exit' INT TERM EXIT

while true; do
  echo "=== $(date -u '+%Y-%m-%d %H:%M UTC') Starting batch ===" | tee -a "$LOG"

  $VENV query_opensky_paths.py --batch $BATCH 2>&1 | tee -a "$LOG"

  if grep -q "All done!" "$LOG"; then
    echo "All slots complete." | tee -a "$LOG"
    break
  fi

  $VENV generate_quiet_overlay.py 2>&1 | tee -a "$LOG" || true
  $VENV gen_status.py 2>&1 | tee -a "$LOG" || true

  git add docs/flights_*.json docs/quiet_overlay.png docs/status.json docs/status.html query_progress_paths*.json 2>/dev/null || true
  SLOTS=$($VENV -c "
import json, glob
total = 0
for f in glob.glob('query_progress_paths*.json'):
    if 'legacy' in f: continue
    total += len(json.load(open(f)).get('completed', []))
print(total)
")
  git commit -m "Add paths batch (${SLOTS} slots done)" 2>&1 | tee -a "$LOG" || true
  git push 2>&1 | tee -a "$LOG" || true

  echo "=== Batch done. Sleeping 5s. ===" | tee -a "$LOG"
  sleep 5
done
