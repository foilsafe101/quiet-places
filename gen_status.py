#!/usr/bin/env python3
"""
gen_status.py — Snapshot the worker's progress to docs/status.json.

Run after each batch. Computes:
  - current slot count and remaining
  - recent rate (from rolling history)
  - ETA based on rate
  - current "in flight" slot/region (best-effort, parsed from log tail)

Output is read by docs/status.html.
"""

import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT          = Path(__file__).parent
PROGRESS_FILE = ROOT / "query_progress_paths.json"
STATUS_FILE   = ROOT / "docs" / "status.json"
LOG_FILE      = Path("/tmp/shhh-worker.log")

# Schedule constants — must match query_opensky_paths.py
SAMPLE_INTERVAL_MINUTES = 12
REGIONS                 = 30
WEEKS                   = 12
DAYS_PER_WEEK           = 7
SLOTS_PER_HOUR          = 60 // SAMPLE_INTERVAL_MINUTES
SLOTS_PER_DAY           = 24 * SLOTS_PER_HOUR
TOTAL_SLOTS             = WEEKS * DAYS_PER_WEEK * SLOTS_PER_DAY * REGIONS

HISTORY_KEEP = 24  # last 24 entries → ~24 batches of context for rate calc


def load_progress():
    if not PROGRESS_FILE.exists():
        return 0
    with open(PROGRESS_FILE) as f:
        return len(json.load(f).get("completed", []))


def parse_current_from_log():
    """Best-effort: read last log line, extract current slot timestamp + region."""
    if not LOG_FILE.exists():
        return None, None
    try:
        with open(LOG_FILE) as f:
            tail = f.readlines()[-30:]
        for line in reversed(tail):
            m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC\s+([^.]+?)\.\.\.", line)
            if m:
                return m.group(1), m.group(2).strip()
    except Exception:
        pass
    return None, None


def main():
    completed = load_progress()
    remaining = max(0, TOTAL_SLOTS - completed)
    pct = (completed / TOTAL_SLOTS) * 100 if TOTAL_SLOTS else 0
    now = datetime.now(timezone.utc)

    # Load existing status to preserve history
    history = []
    if STATUS_FILE.exists():
        try:
            existing = json.loads(STATUS_FILE.read_text())
            history = existing.get("history", [])
        except Exception:
            pass

    history.append({"ts": now.isoformat(), "count": completed})
    history = history[-HISTORY_KEEP:]

    # Compute rate from oldest vs newest entry in history
    rate_per_hour = None
    eta = None
    if len(history) >= 2:
        first = datetime.fromisoformat(history[0]["ts"])
        delta_hours = (now - first).total_seconds() / 3600
        delta_count = completed - history[0]["count"]
        if delta_hours > 0 and delta_count > 0:
            rate_per_hour = delta_count / delta_hours
            hours_remaining = remaining / rate_per_hour if rate_per_hour else None
            eta = (now + timedelta(hours=hours_remaining)).isoformat() if hours_remaining else None

    current_slot, current_region = parse_current_from_log()

    status = {
        "config": {
            "total_slots":            TOTAL_SLOTS,
            "sample_interval_minutes": SAMPLE_INTERVAL_MINUTES,
            "regions":                REGIONS,
            "weeks":                  WEEKS,
        },
        "progress": {
            "completed": completed,
            "remaining": remaining,
            "pct":       round(pct, 2),
        },
        "rate": {
            "per_hour": round(rate_per_hour, 1) if rate_per_hour else None,
            "per_day":  round(rate_per_hour * 24, 0) if rate_per_hour else None,
            "window_hours": round((now - datetime.fromisoformat(history[0]["ts"])).total_seconds() / 3600, 1) if len(history) >= 2 else 0,
        },
        "eta": {
            "days_remaining":   round((remaining / (rate_per_hour * 24)), 1) if rate_per_hour else None,
            "estimated_finish": eta,
        },
        "current": {
            "slot":   current_slot,
            "region": current_region,
        },
        "last_update": now.isoformat(),
        "history": history,
    }

    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(status, indent=2))
    print(f"Wrote {STATUS_FILE} — {completed:,}/{TOTAL_SLOTS:,} ({pct:.1f}%)")


if __name__ == "__main__":
    main()
