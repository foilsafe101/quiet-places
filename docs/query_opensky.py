#!/usr/bin/env python3
"""
query_opensky.py — Quiet Places Project
Queries OpenSky Network's Trino historical database for global flight positions
and appends them to docs/flights.json.

BEFORE RUNNING:
    pip install pyopensky tqdm

USAGE:
    python query_opensky.py                  # run all pending queries
    python query_opensky.py --dry-run        # print schedule without querying
    python query_opensky.py --start 2022-01-01  # start from a specific date
    python query_opensky.py --batch 50       # run only N queries then stop

DESIGN:
    - Samples one hour every 3 days, covering 24 hours per sampled day
    - Each hour uses a rotating minute offset to avoid schedule bias
    - Queries are run sequentially with a pause between each
    - Progress is saved after every query so runs can be resumed
    - Strictly follows OpenSky's performance guidelines (always filters on
      the 'hour' partition column, one query at a time)
"""

import argparse
import json
import math
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OUTPUT_FILE    = Path(__file__).parent / "docs" / "flights.json"
PROGRESS_FILE  = Path(__file__).parent / "query_progress.json"

START_DATE     = datetime(2019, 1, 7, tzinfo=timezone.utc)   # first Monday of 2019
SAMPLE_EVERY_N_DAYS = 3       # query one full day every N days
HOURS_PER_DAY  = 24           # query all 24 hours of each sampled day
ROWS_PER_HOUR  = 2000         # max aircraft positions per hour query
PAUSE_SECONDS  = 8            # pause between queries (be polite)
MIN_ALT_M      = 300          # minimum altitude in metres (~1000ft)

# 24 rotating minute offsets — irregular to avoid schedule bias.
# These cycle across sampled days so each part of each hour gets sampled.
MINUTE_OFFSETS = [
     7, 34, 51, 18, 42,  3, 28, 55,
    12, 39,  6, 47, 22, 58, 15, 33,
    49,  8, 25, 44,  1, 37, 53, 16,
]

# ---------------------------------------------------------------------------
# Schedule generation
# ---------------------------------------------------------------------------

def generate_schedule(start: datetime, end: datetime) -> list[dict]:
    """
    Generate the full list of (hour_ts, minute_offset) pairs to query.
    Each sampled day gets 24 queries, one per hour, with a rotating
    minute offset drawn from MINUTE_OFFSETS.
    """
    schedule = []
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    day_index = 0

    while day <= end:
        for hour in range(HOURS_PER_DAY):
            # Pick the minute offset for this slot, cycling through the list
            offset_index = (day_index * HOURS_PER_DAY + hour) % len(MINUTE_OFFSETS)
            minute_offset = MINUTE_OFFSETS[offset_index]

            # The 'hour' partition value: Unix timestamp of the start of this hour
            hour_dt = day.replace(hour=hour)
            hour_ts = int(hour_dt.timestamp())

            # The target time within that hour
            target_ts = hour_ts + (minute_offset * 60)

            schedule.append({
                "hour_ts":       hour_ts,
                "target_ts":     target_ts,
                "minute_offset": minute_offset,
                "label":         hour_dt.strftime(f"%Y-%m-%d %H:{minute_offset:02d} UTC"),
            })

        day += timedelta(days=SAMPLE_EVERY_N_DAYS)
        day_index += 1

    return schedule

# ---------------------------------------------------------------------------
# Progress tracking
# ---------------------------------------------------------------------------

def load_progress() -> set:
    """Return set of hour_ts values already queried."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return set(json.load(f).get("completed", []))
    return set()

def save_progress(completed: set):
    with open(PROGRESS_FILE, "w") as f:
        json.dump({"completed": sorted(completed)}, f)

# ---------------------------------------------------------------------------
# Data loading / saving
# ---------------------------------------------------------------------------

def load_flights() -> dict:
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE) as f:
            return json.load(f)
    return {"tracks": [], "meta": {}}

def save_flights(data: dict):
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    kb = OUTPUT_FILE.stat().st_size / 1024
    print(f"  Saved {len(data['tracks'])} total positions ({kb:.0f} KB)")

# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def query_hour(opensky, hour_ts: int, target_ts: int, label: str) -> list[dict]:
    """
    Query one hour partition for aircraft positions near the target timestamp.
    Returns a list of position dicts for flights.json.
    Always filters on the 'hour' partition column per OpenSky guidelines.
    """
    # Time window: ±90 seconds around the target minute
    t_start = target_ts - 90
    t_end   = target_ts + 90

    query = f"""
        SELECT icao24, callsign, lat, lon, baroaltitude, time
        FROM state_vectors_data4
        WHERE hour = {hour_ts}
          AND time BETWEEN {t_start} AND {t_end}
          AND lat IS NOT NULL
          AND lon IS NOT NULL
          AND onground = false
          AND baroaltitude > {MIN_ALT_M}
          AND time - lastcontact <= 15
        LIMIT {ROWS_PER_HOUR}
    """

    try:
        df = opensky.rawquery(query)
        if df is None or len(df) == 0:
            return []

        positions = []
        for _, row in df.iterrows():
            positions.append({
                "icao24":   str(row["icao24"]).strip(),
                "callsign": str(row.get("callsign", "") or "").strip(),
                "fetched":  int(row["time"]),
                "path":     [[round(float(row["lat"]), 4),
                               round(float(row["lon"]), 4)]],
            })
        return positions

    except Exception as e:
        print(f"  Query error: {e}")
        return []

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run",  action="store_true",
                        help="Print schedule without querying")
    parser.add_argument("--start",    type=str, default=None,
                        help="Override start date (YYYY-MM-DD)")
    parser.add_argument("--batch",    type=int, default=None,
                        help="Run only N queries then stop")
    args = parser.parse_args()

    # Date range
    start = START_DATE
    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.now(timezone.utc)

    # Generate full schedule
    schedule = generate_schedule(start, end)
    print(f"Total schedule: {len(schedule)} queries "
          f"({len(schedule) // HOURS_PER_DAY} sampled days × {HOURS_PER_DAY} hours)")

    if args.dry_run:
        print("\nFirst 10 queries:")
        for s in schedule[:10]:
            print(f"  hour={s['hour_ts']}  target={s['label']}")
        print(f"\nLast 5 queries:")
        for s in schedule[-5:]:
            print(f"  hour={s['hour_ts']}  target={s['label']}")
        print(f"\nEstimated runtime at {PAUSE_SECONDS}s/query: "
              f"{len(schedule) * PAUSE_SECONDS / 3600:.1f} hours")
        return

    # Load progress
    completed = load_progress()
    pending = [s for s in schedule if s["hour_ts"] not in completed]
    print(f"Already completed: {len(completed)} queries")
    print(f"Remaining:         {len(pending)} queries")

    if not pending:
        print("Nothing to do — all queries complete.")
        return

    if args.batch:
        pending = pending[:args.batch]
        print(f"Running batch of {len(pending)} queries")

    # Connect to OpenSky Trino
    print("\nConnecting to OpenSky Trino (browser window will open for login)...")
    try:
        from pyopensky.trino import Trino
        opensky = Trino()
    except ImportError:
        print("ERROR: pyopensky not installed. Run: pip install pyopensky")
        return

    # Load existing flight data
    data = load_flights()
    print(f"Loaded {len(data['tracks'])} existing positions\n")

    # Run queries
    new_total = 0
    for i, slot in enumerate(pending):
        label = slot["label"]
        hour_ts = slot["hour_ts"]
        target_ts = slot["target_ts"]

        print(f"[{i+1}/{len(pending)}] {label} ... ", end="", flush=True)

        positions = query_hour(opensky, hour_ts, target_ts, label)
        print(f"{len(positions)} aircraft")

        if positions:
            data["tracks"].extend(positions)
            new_total += len(positions)

        completed.add(hour_ts)

        # Save progress and data every 10 queries
        if (i + 1) % 10 == 0 or (i + 1) == len(pending):
            save_progress(completed)
            data["meta"] = {
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "total_tracks": len(data["tracks"]),
                "source":       "opensky-trino",
                "queries_done": len(completed),
            }
            save_flights(data)

        # Polite pause between queries
        if i < len(pending) - 1:
            time.sleep(PAUSE_SECONDS)

    print(f"\nDone. Added {new_total} new positions.")
    print(f"Total positions in flights.json: {len(data['tracks'])}")
    print(f"\nNext step: commit docs/flights.json to GitHub to update the map.")

if __name__ == "__main__":
    main()
