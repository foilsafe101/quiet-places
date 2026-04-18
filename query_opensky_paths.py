#!/usr/bin/env python3
"""
query_opensky_paths.py - Quiet Places Project
Queries OpenSky Trino for real connected flight paths using 3-minute sampling.

Strategy:
- One complete week per month of 2025 (all 7 days)
- 3-minute sampling intervals across each 24-hour period
- 9 land-region bounding boxes (excluding oceans)
- Consecutive positions of same aircraft within a day connected into path segments
- Results saved as multi-point paths in flights.json

Usage:
    python query_opensky_paths.py --dry-run
    python query_opensky_paths.py --batch 480
    python query_opensky_paths.py
"""

import argparse
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

OUTPUT_FILE   = Path(__file__).parent / "docs" / "flights.json"
PROGRESS_FILE = Path(__file__).parent / "query_progress_paths.json"

SAMPLE_WEEKS_2025 = [
    (2025,  1,  6),
    (2025,  2,  3),
    (2025,  3,  3),
    (2025,  4,  7),
    (2025,  5,  5),
    (2025,  6,  2),
    (2025,  7,  7),
    (2025,  8,  4),
    (2025,  9,  1),
    (2025, 10,  6),
    (2025, 11,  3),
    (2025, 12,  1),
]

SAMPLE_INTERVAL_MINUTES = 3
HOURS_PER_DAY           = 24
SLOTS_PER_HOUR          = 60 // SAMPLE_INTERVAL_MINUTES
SLOTS_PER_DAY           = HOURS_PER_DAY * SLOTS_PER_HOUR

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

MIN_ALT_M         = 300
MAX_ROWS_PER_SLOT = 3000
PAUSE_SECONDS     = 6
MAX_PATH_GAP_S    = 600
RDP_TOLERANCE     = 0.01   # ~1km at equator


def simplify_path(points, tolerance=RDP_TOLERANCE):
    if len(points) <= 2:
        return points
    deduped = [points[0]]
    for p in points[1:]:
        if p != deduped[-1]:
            deduped.append(p)
    if len(deduped) <= 2:
        return deduped

    def rdp(pts, eps):
        if len(pts) < 3:
            return pts
        start, end = pts[0], pts[-1]
        dx, dy = end[0] - start[0], end[1] - start[1]
        max_dist, idx = 0, 0
        for i in range(1, len(pts) - 1):
            if dx == 0 and dy == 0:
                dist = ((pts[i][0]-start[0])**2 + (pts[i][1]-start[1])**2) ** 0.5
            else:
                t = ((pts[i][0]-start[0])*dx + (pts[i][1]-start[1])*dy) / (dx*dx + dy*dy)
                t = max(0.0, min(1.0, t))
                dist = ((pts[i][0]-start[0]-t*dx)**2 + (pts[i][1]-start[1]-t*dy)**2) ** 0.5
            if dist > max_dist:
                max_dist, idx = dist, i
        if max_dist > eps:
            return rdp(pts[:idx+1], eps)[:-1] + rdp(pts[idx:], eps)
        return [start, end]

    return rdp(deduped, tolerance)


def generate_schedule():
    schedule = []
    for year, month, monday_day in SAMPLE_WEEKS_2025:
        week_start = datetime(year, month, monday_day, tzinfo=timezone.utc)
        for day_offset in range(7):
            day = week_start + timedelta(days=day_offset)
            for slot in range(SLOTS_PER_DAY):
                hour   = slot // SLOTS_PER_HOUR
                minute = (slot % SLOTS_PER_HOUR) * SAMPLE_INTERVAL_MINUTES
                slot_dt = day.replace(hour=hour, minute=minute)
                hour_ts = int(day.replace(hour=hour).timestamp())
                slot_ts = int(slot_dt.timestamp())
                for r_idx, region in enumerate(LAND_REGIONS):
                    schedule.append({
                        "key":     f"{slot_ts}_{r_idx}",
                        "day":     day.strftime("%Y-%m-%d"),
                        "slot_dt": slot_dt.strftime("%Y-%m-%d %H:%M UTC"),
                        "hour_ts": hour_ts,
                        "slot_ts": slot_ts,
                        "region":  region,
                        "r_idx":   r_idx,
                    })
    return schedule


def load_progress():
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return set(json.load(f).get("completed", []))
    return set()


def save_progress(completed):
    with open(PROGRESS_FILE, "w") as f:
        json.dump({"completed": sorted(completed)}, f)


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


class DayAccumulator:
    def __init__(self):
        self.by_aircraft = {}

    def add(self, icao24, ts, lat, lon):
        if icao24 not in self.by_aircraft:
            self.by_aircraft[icao24] = []
        self.by_aircraft[icao24].append({"ts": ts, "lat": lat, "lon": lon})

    def to_tracks(self):
        tracks = []
        for icao24, obs in self.by_aircraft.items():
            obs.sort(key=lambda x: x["ts"])
            segment = [obs[0]]
            for i in range(1, len(obs)):
                if obs[i]["ts"] - obs[i-1]["ts"] > MAX_PATH_GAP_S:
                    tracks.append(self._make_track(icao24, segment))
                    segment = []
                segment.append(obs[i])
            if segment:
                tracks.append(self._make_track(icao24, segment))
        return tracks

    def _make_track(self, icao24, segment):
        raw = [[round(p["lat"], 3), round(p["lon"], 3)] for p in segment]
        return {
            "fetched": segment[-1]["ts"],
            "path":    simplify_path(raw),
        }


def query_slot(opensky, slot):
    name, min_lat, max_lat, min_lon, max_lon = slot["region"]
    hour_ts = slot["hour_ts"]
    slot_ts = slot["slot_ts"]
    end_ts  = slot_ts + (SAMPLE_INTERVAL_MINUTES * 60) - 1

    sql = f"""
        SELECT icao24, lat, lon, time
        FROM state_vectors_data4
        WHERE hour         = {hour_ts}
          AND time         BETWEEN {slot_ts} AND {end_ts}
          AND lat          BETWEEN {min_lat} AND {max_lat}
          AND lon          BETWEEN {min_lon} AND {max_lon}
          AND lat          IS NOT NULL
          AND lon          IS NOT NULL
          AND onground     = false
          AND baroaltitude > {MIN_ALT_M}
          AND time - lastcontact <= 15
        LIMIT {MAX_ROWS_PER_SLOT}
    """

    try:
        df = opensky.query(sql)
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--batch",    type=int, default=None)
    parser.add_argument("--start",    type=str, default=None)
    args = parser.parse_args()

    schedule = generate_schedule()
    print(f"Total schedule: {len(schedule):,} query slots")
    print(f"  = {len(SAMPLE_WEEKS_2025)} months x 7 days x {SLOTS_PER_DAY} slots x {len(LAND_REGIONS)} regions")

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

    if args.start:
        schedule = [s for s in schedule if s["day"] >= args.start + "-01"]
        print(f"Starting from {args.start}: {len(schedule):,} slots remaining")

    completed = load_progress()
    pending   = [s for s in schedule if s["key"] not in completed]
    print(f"Completed: {len(completed):,}  Remaining: {len(pending):,}")

    if not pending:
        print("All done!")
        return

    if args.batch:
        pending = pending[:args.batch]
        print(f"Running batch of {len(pending):,} slots")

    print("\nConnecting to OpenSky Trino...")
    try:
        from pyopensky.trino import Trino
        opensky = Trino()
    except ImportError:
        print("ERROR: pip install pyopensky")
        return

    flight_data = load_flights()
    print(f"Loaded {len(flight_data['tracks']):,} existing tracks\n")

    current_day = None
    accumulator = None
    new_tracks  = 0

    def flush_day():
        nonlocal new_tracks
        if accumulator and accumulator.by_aircraft:
            tracks = accumulator.to_tracks()
            flight_data["tracks"].extend(tracks)
            new_tracks += len(tracks)
            print(f"  Day complete: {len(tracks)} path segments")

    for i, slot in enumerate(pending):
        day = slot["day"]
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

        if (i + 1) % 50 == 0:
            old_ac = accumulator.by_aircraft.copy()
            flush_day()
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
