#!/bin/bash
# Usage: run_batches.sh [worker]
#   worker: 0 (regions 0-4) or 1 (regions 5-8) or omitted (all regions, single process)
# Runs continuous 200-slot batches, commits+pushes after each.

cd /Users/markustribus/quiet-places
VENV=.venv/bin/python
BATCH=200
WORKER=${1:-""}

WORKER_ARG=""
LOG=/tmp/batches.log
if [ -n "$WORKER" ]; then
  WORKER_ARG="--worker $WORKER"
  LOG=/tmp/batches_${WORKER}.log
fi

while true; do
  echo "=== $(date -u '+%Y-%m-%d %H:%M UTC') Starting batch worker=${WORKER:-single} ===" | tee -a $LOG

  $VENV query_opensky_paths.py --batch $BATCH $WORKER_ARG 2>&1 | tee -a $LOG

  if grep -q "All done!" $LOG; then
    echo "All slots complete. Exiting." | tee -a $LOG
    break
  fi

  # Merge worker files into flights.json if running in parallel
  if [ -n "$WORKER" ]; then
    $VENV - << 'PYEOF'
import json
from pathlib import Path

docs = Path("docs")
tracks = []
for i in range(2):
    f = docs / f"flights_{i}.json"
    if f.exists():
        d = json.load(open(f))
        tracks.extend(d.get("tracks", []))

merged = {"tracks": tracks, "meta": {
    "last_updated": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    "total_tracks": len(tracks),
    "source": "opensky-trino-paths",
}}
with open(docs / "flights.json", "w") as f:
    json.dump(merged, f, separators=(",", ":"))
kb = (docs / "flights.json").stat().st_size / 1024
print(f"  Merged {len(tracks)} tracks into flights.json ({kb:.0f} KB)")
PYEOF
  fi

  # Regenerate quiet overlay
  $VENV generate_quiet_overlay.py 2>&1 | tee -a $LOG

  # Commit and push
  git add docs/flights_*.json docs/quiet_overlay.png query_progress_paths*.json 2>/dev/null
  SLOTS=$($VENV -c "
import json, glob
total = 0
for f in glob.glob('query_progress_paths*.json'):
    total += len(json.load(open(f)).get('completed', []))
print(total)
")
  git commit -m "Add paths batch (${SLOTS} slots done)" 2>&1 | tee -a $LOG
  git push 2>&1 | tee -a $LOG

  echo "=== Batch done. Sleeping 5s before next. ===" | tee -a $LOG
  sleep 5
done
