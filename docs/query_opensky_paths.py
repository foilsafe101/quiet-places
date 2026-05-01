#!/usr/bin/env python3
"""
query_opensky_paths.py — Quiet Places Project
Queries OpenSky Trino for real connected flight paths using 3-minute sampling.

Strategy:
- One complete week per month of 2025 (all 7 days)
- 3-minute sampling intervals across each 24-hour period
- 8 land-region bounding boxes (excluding oceans)
- Consecutive positions of same aircraft within a day connected into path segments
- Results saved as multi-point paths in flights.json

Total queries: ~322,000 run sequentially over several weeks.
Martin Strohmeier confirmed: "just go ahead and figure out what works."

Usage:
    python query_opensky_paths.py --dry-run         # preview schedule
    python query_opensky_paths.py --batch 100       # run 100 query-slots
    python query_opensky_paths.py                   # run all pending
    python query_opensky_paths.py --start 2025-03   # start from a month
"""

import argparse
import json
import math
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

OUTPUT_FILE   = Path(__file__).parent / "docs" / "flights.json"
PROGRESS_FILE = Path(__file__).parent / "query_progress_paths.json"

# ---------------------------------------------------------------------------
# Sampling design
# ---------------------------------------------------------------------------

# One representative week per month — pick the 2nd week of each month
# (avoids holiday distortion at start/end of month)
# Each entry: (year, month, day-of-month for Monday of that week)
SAMPLE_WEEKS_2025 = [
    (2025,  1,  6),   # Jan: week of Jan 6
    (2025,  2,  3),   # Feb: week of Feb 3
    (2025,  3,  3),   # Mar: week of Mar 3
    (2025,  4,  7),   # Apr: week of Apr 7
    (2025,  5,  5),   # May: week of May 5
    (2025,  6,  2),   # Jun: week of Jun 2
    (2025,  7,  7),   # Jul: week of Jul 7
    (2025,  8,  4),   # Aug: week of Aug 4
    (2025,  9,  1),   # Sep: week of Sep 1
    (2025, 10,  6),   # Oct: week of Oct 6
    (2025, 11,  3),   # Nov: week of Nov 3
    (2025, 12,  1),   # Dec: week of Dec 1
]

SAMPLE_INTERVAL_MINUTES = 3    # query every 3 minutes
HOURS_PER_DAY           = 24
SLOTS_PER_HOUR          = 60 // SAMPLE_INTERVAL_MINUTES   # 20 slots/hour
SLOTS_PER_DAY           = HOURS_PER_DAY * SLOTS_PER_HOUR  # 480 slots/day

# Land-region bounding boxes — excludes oceans
# Format: (name, min_lat, max_lat, min_lon, max_lon)
LAND_REGIONS = [
    ("North America",    15.0,  72.0, -168.0,  -52.0),
    ("Europe",           34.0,  72.0,  -25.0,   45.0),
    ("Middle East",      12.0,  42.0,   25.0,   65.0),
    ("South Asia",        5.0,  37.0,   60.0,   97.0),
    ("East Asia",        18.0,  53.0,   97.0,  145.0),
    ("Southeast Asia",  -10.0,  28.0,   95.0,  141.0),
    ("Africa",          -35.0,  37.0,  -18.0,   52.0),
    ("South America",   -56.0,  13.0,  -82.0,  -34.0),
    ("Australia",       -44.0,  -9.0,  113.0,  154.0),
]

MIN_ALT_M        = 300     # ~1000ft — exclude ground vehicles
MAX_ROWS_PER_SLOT = 3000   # row limit per query
PAUSE_SECONDS    = 6       # between queries
MAX_PATH_GAP_S   = 600     # 10 min — max gap between points in same path segment

# ---------------------------------------------------------------------------
# Schedule generation
# ---------------------------------------------------------------------------

def generate_schedule():
    """
    Generate the full list of (day_date, slot_index, region_index) to query.
    Each day has SLOTS_PER_DAY × len(LAND_REGIONS) queries.
    Returns list of dicts with all info needed to run each query.
    """
    schedule = []
    for year, month, monday_day in SAMPLE_WEEKS_2025:
        week_start = datetime(year, month, monday_day, tzinfo=timezone.utc)
        for day_offset in range(7):
            day = week_start + timedelta(days=day_offset)
            for slot in range(SLOTS_PER_DAY):
                hour    = slot // SLOTS_PER_HOUR
                minute  = (slot % SLOTS_PER_HOUR) * SAMPLE_INTERVAL_MINUTES
                slot_dt = day.replace(hour=hour, minute=minute)
                hour_ts = int(day.replace(hour=hour).timestamp())
                slot_ts = int(slot_dt.timestamp())

                for r_idx, region in enumerate(LAND_REGIONS):
                    schedule.append({
                        "key":      f"{slot_ts}_{r_idx}",
                        "day":      day.strftime("%Y-%m-%d"),
                        "slot_dt":  slot_dt.strftime("%Y-%m-%d %H:%M UTC"),
                        "hour_ts":  hour_ts,
                        "slot_ts":  slot_ts,
                        "region":   region,
                        "r_idx":    r_idx,
                    })
    return schedule

# ---------------------------------------------------------------------------
# Progress tracking
# ---------------------------------------------------------------------------

def load_progress():
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            data = json.load(f)
            return set(data.get("completed", []))
    return set()

def save_progress(completed):
    with open(PROGRESS_FILE, "w") as f:
        json.dump({"completed": sorted(completed)}, f)

# ---------------------------------------------------------------------------
# Flight data storage
# ---------------------------------------------------------------------------

def load_flights():
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE) as f:
            return json.load(f)
    return {"tracks": [], "meta": {}}

def save_flights(data):
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    kb = OUTPUT_FILE.stat().st_size / 1024
    print(f"  Saved {len(data['tracks'])} tracks ({kb:.0f} KB)")

# ---------------------------------------------------------------------------
# Path accumulator — builds multi-point paths within a day
# ---------------------------------------------------------------------------

class DayAccumulator:
    """
    Accumulates positions for all aircraft seen during one sample day.
    When the day is complete, converts to connected path segments.
    """
    def __init__(self):
        self.by_aircraft = {}   # icao24 -> sorted list of {ts, lat, lon}

    def add(self, icao24, ts, lat, lon):
        if icao24 not in self.by_aircraft:
            self.by_aircraft[icao24] = []
        self.by_aircraft[icao24].append({"ts": ts, "lat": lat, "lon": lon})

    def to_tracks(self):
        """Convert accumulated positions to track records with multi-point paths."""
        tracks = []
        for icao24, obs in self.by_aircraft.items():
            # Sort by time
            obs.sort(key=lambda x: x["ts"])

            # Split into segments where gap <= MAX_PATH_GAP_S
            segment = [obs[0]]
            for i in range(1, len(obs)):
                gap = obs[i]["ts"] - obs[i-1]["ts"]
                if gap > MAX_PATH_GAP_S:
                    if len(segment) >= 2:
                        tracks.append(self._make_track(icao24, segment))
                    elif len(segment) == 1:
                        tracks.append(self._make_track(icao24, segment))
                    segment = []
                segment.append(obs[i])

            if segment:
                tracks.append(self._make_track(icao24, segment))

        return tracks

    def _make_track(self, icao24, segment):
        return {
            "icao24":  icao24,
            "fetched": segment[-1]["ts"],
            "path":    [[round(p["lat"], 4), round(p["lon"], 4)]
                        for p in segment],
        }

# ---------------------------------------------------------------------------
# OpenSky query
# ---------------------------------------------------------------------------

def query_slot(opensky, slot):
    """Query one 3-minute window for one land region."""
    name, min_lat, max_lat, min_lon, max_lon = slot["region"]
    hour_ts  = slot["hour_ts"]
    slot_ts  = slot["slot_ts"]
    end_ts   = slot_ts + (SAMPLE_INTERVAL_MINUTES * 60) - 1

    sql = f"""
        SELECT DISTINCT ON (icao24)
            icao24,
            lat,
            lon,
            time
        FROM state_vectors_data4
        WHERE hour          = {hour_ts}
          AND time          BETWEEN {slot_ts} AND {end_ts}
          AND lat           BETWEEN {min_lat} AND {max_lat}
          AND lon           BETWEEN {min_lon} AND {max_lon}
          AND lat           IS NOT NULL
          AND lon           IS NOT NULL
          AND onground      = false
          AND baroaltitude  > {MIN_ALT_M}
          AND time - lastcontact <= 15
        LIMIT {MAX_ROWS_PER_SLOT}
    """

    try:
        df = opensky.rawquery(sql)
        if df is None or len(df) == 0:
            return []
        results = []
        for _, row in df.iterrows():
            results.append({
                "icao24": str(row["icao24"]).strip(),
                "ts":     int(row["time"]),
                "lat":    float(row["lat"]),
                "lon":    float(row["lon"]),
            })
        return results
    except Exception as e:
        print(f"    Query error: {e}")
        return []

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--batch",    type=int, default=None,
                        help="Number of query SLOTS to run (each slot = 1 region × 1 time window)")
    parser.add_argument("--start",    type=str, default=None,
                        help="Skip to month (YYYY-MM), e.g. --start 2025-06")
    args = parser.parse_args()

    schedule = generate_schedule()
    print(f"Total schedule: {len(schedule):,} query slots")
    print(f"  = {len(SAMPLE_WEEKS_2025)} months × 7 days × {SLOTS_PER_DAY} slots × {len(LAND_REGIONS)} regions")

    if args.dry_run:
        print(f"\nFirst 5 slots:")
        for s in schedule[:5]:
            print(f"  {s['slot_dt']}  {s['region'][0]}")
        print(f"\nLast 3 slots:")
        for s in schedule[-3:]:
            print(f"  {s['slot_dt']}  {s['region'][0]}")
        est_hrs = len(schedule) * PAUSE_SECONDS / 3600
        print(f"\nEstimated runtime: {est_hrs:.0f} hours at {PAUSE_SECONDS}s/slot")
        return

    # Filter by start month if requested
    if args.start:
        schedule = [s for s in schedule if s["day"] >= args.start + "-01"]
        print(f"Starting from {args.start}: {len(schedule):,} slots remaining")

    # Load progress
    completed = load_progress()
    pending   = [s for s in schedule if s["key"] not in completed]
    print(f"Completed: {len(completed):,}  Remaining: {len(pending):,}")

    if not pending:
        print("All done!")
        return

    if args.batch:
        pending = pending[:args.batch]
        print(f"Running batch of {len(pending):,} slots")

    # Connect to OpenSky
    print("\nConnecting to OpenSky Trino...")
    try:
        from pyopensky.trino import Trino
        opensky = Trino()
    except ImportError:
        print("ERROR: pip install pyopensky")
        return

    # Load existing flight data
    flight_data = load_flights()
    print(f"Loaded {len(flight_data['tracks']):,} existing tracks\n")

    # Process slots — accumulate per day, flush when day changes
    current_day  = None
    accumulator  = None
    new_tracks   = 0

    def flush_day():
        nonlocal new_tracks
        if accumulator and accumulator.by_aircraft:
            tracks = accumulator.to_tracks()
            flight_data["tracks"].extend(tracks)
            new_tracks += len(tracks)
            print(f"  → Day complete: {len(tracks)} path segments")

    for i, slot in enumerate(pending):
        day = slot["day"]

        # New day — flush previous day's accumulator
        if day != current_day:
            flush_day()
            current_day = day
            accumulator = DayAccumulator()
            print(f"\n[{i+1}/{len(pending)}] Day: {day}")

        region_name = slot["region"][0]
        print(f"  {slot['slot_dt']}  {region_name}...", end=" ", flush=True)

        positions = query_slot(opensky, slot)
        print(f"{len(positions)} aircraft")

        for p in positions:
            accumulator.add(p["icao24"], p["ts"], p["lat"], p["lon"])

        completed.add(slot["key"])

        # Save progress every 50 slots
        if (i + 1) % 50 == 0:
            flush_day()
            # Reset accumulator (keep current day's data)
            old_ac = accumulator.by_aircraft.copy()
            accumulator = DayAccumulator()
            accumulator.by_aircraft = old_ac

            save_progress(completed)
            flight_data["meta"] = {
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "total_tracks": len(flight_data["tracks"]),
                "source":       "opensky-trino-paths",
                "slots_done":   len(completed),
            }
            save_flights(flight_data)

        if i < len(pending) - 1:
            time.sleep(PAUSE_SECONDS)

    # Final flush
    flush_day()
    save_progress(completed)
    flight_data["meta"] = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "total_tracks": len(flight_data["tracks"]),
        "source":       "opensky-trino-paths",
        "slots_done":   len(completed),
    }
    save_flights(flight_data)

    print(f"\nDone. Added {new_tracks:,} new path segments.")
    print(f"Total tracks: {len(flight_data['tracks']):,}")

if __name__ == "__main__":
    main()
