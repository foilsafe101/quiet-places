#!/usr/bin/env python3
"""
fetch_flights.py - Quiet Places Project
Fetches flight track data from OpenSky Network and appends to flights.json.

Set OPENSKY_USERNAME and OPENSKY_PASSWORD env vars for authenticated access
(required for the /flights/all endpoint).

Usage:
    python fetch_flights.py                  # fetch last 24h
    python fetch_flights.py --hours 48
    python fetch_flights.py --prune-days 90
    python fetch_flights.py --dry-run
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OPENSKY_FLIGHTS_URL = "https://opensky-network.org/api/flights/all"
OPENSKY_TRACK_URL   = "https://opensky-network.org/api/tracks/all"
OUTPUT_FILE         = Path(__file__).parent / "docs" / "flights.json"

# Read credentials from environment (set as GitHub Actions secrets)
OPENSKY_AUTH = None
_user = os.environ.get("OPENSKY_USERNAME")
_pass = os.environ.get("OPENSKY_PASSWORD")
if _user and _pass:
    OPENSKY_AUTH = (_user, _pass)
    print(f"Using authenticated OpenSky access as '{_user}'")
else:
    print("No OpenSky credentials found - unauthenticated access (limited)")

BOUNDS = {
    "north_america": [15, 72, -170, -50],
    "europe":        [35, 72, -25,  45],
    "global":        [-90, 90, -180, 180],
}
ACTIVE_BOUNDS = "global"

SAMPLE_EVERY_N     = 10
MAX_TRACKS_PER_RUN = 80
WAYPOINT_STRIDE    = 3

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ts_now():
    return int(datetime.now(timezone.utc).timestamp())

def ts_hours_ago(h):
    return int((datetime.now(timezone.utc) - timedelta(hours=h)).timestamp())

def simplify_track(waypoints, stride=WAYPOINT_STRIDE):
    if len(waypoints) <= 2:
        return waypoints
    kept = waypoints[::stride]
    if kept[-1] != waypoints[-1]:
        kept.append(waypoints[-1])
    return kept

def encode_path(waypoints):
    result = []
    for wp in waypoints:
        if len(wp) >= 3 and wp[1] is not None and wp[2] is not None:
            result.append([round(wp[1], 4), round(wp[2], 4)])
    return result

def load_existing(path):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"tracks": [], "meta": {"last_updated": None, "total_tracks": 0}}

def save(data, path, dry_run=False):
    if dry_run:
        print(f"[dry-run] Would write {len(data['tracks'])} tracks to {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    kb = path.stat().st_size / 1024
    print(f"Wrote {len(data['tracks'])} tracks to {path} ({kb:.1f} KB)")

# ---------------------------------------------------------------------------
# OpenSky API calls
# ---------------------------------------------------------------------------

def api_get(url, params, timeout=30):
    """GET with optional auth and basic rate-limit retry."""
    r = requests.get(url, params=params, auth=OPENSKY_AUTH, timeout=timeout)
    if r.status_code == 429:
        print("Rate limited - waiting 60s")
        time.sleep(60)
        r = requests.get(url, params=params, auth=OPENSKY_AUTH, timeout=timeout)
    return r

def get_flights(begin_ts, end_ts, bounds):
    min_lat, max_lat, min_lon, max_lon = bounds
    params = {"begin": begin_ts, "end": end_ts,
              "lamin": min_lat, "lamax": max_lat,
              "lomin": min_lon, "lomax": max_lon}
    print(f"Fetching flights {datetime.fromtimestamp(begin_ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ...")
    r = api_get(OPENSKY_FLIGHTS_URL, params)
    r.raise_for_status()
    flights = r.json() or []
    print(f"  -> {len(flights)} flights found")
    return flights

def get_track(icao24, begin_ts):
    params = {"icao24": icao24, "time": begin_ts}
    r = api_get(OPENSKY_TRACK_URL, params, timeout=15)
    if r.status_code in (404, 400):
        return None
    if not r.ok:
        return None
    return r.json().get("path", [])

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours",      type=int, default=24)
    parser.add_argument("--prune-days", type=int, default=180)
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--output",     type=str, default=None)
    args = parser.parse_args()

    out_path = Path(args.output) if args.output else OUTPUT_FILE
    bounds   = BOUNDS[ACTIVE_BOUNDS]
    end_ts   = ts_now()
    begin_ts = ts_hours_ago(args.hours)

    data = load_existing(out_path)
    print(f"Loaded {len(data['tracks'])} existing tracks")

    try:
        flights = get_flights(begin_ts, end_ts, bounds)
    except requests.HTTPError as e:
        print(f"Failed to fetch flights: {e}")
        print("Tip: Set OPENSKY_USERNAME and OPENSKY_PASSWORD for authenticated access.")
        return

    sampled = flights[::SAMPLE_EVERY_N][:MAX_TRACKS_PER_RUN]
    print(f"Sampling {len(sampled)} of {len(flights)} flights")

    new_tracks = []
    for i, flight in enumerate(sampled):
        icao24   = flight.get("icao24", "")
        begin    = flight.get("firstSeen", begin_ts)
        callsign = (flight.get("callsign") or "").strip()

        print(f"  [{i+1}/{len(sampled)}] {callsign or icao24} ...", end=" ", flush=True)
        waypoints = get_track(icao24, begin)

        if not waypoints or len(waypoints) < 4:
            print("skip")
            continue

        path = encode_path(simplify_track(waypoints))
        if len(path) < 2:
            print("skip (no coords)")
            continue

        new_tracks.append({"icao24": icao24, "callsign": callsign,
                           "fetched": end_ts, "path": path})
        print(f"ok ({len(path)} pts)")
        time.sleep(0.5)

    print(f"Fetched {len(new_tracks)} new tracks")
    data["tracks"].extend(new_tracks)

    if args.prune_days > 0:
        cutoff_ts = end_ts - args.prune_days * 86400
        before = len(data["tracks"])
        data["tracks"] = [t for t in data["tracks"] if t["fetched"] >= cutoff_ts]
        pruned = before - len(data["tracks"])
        if pruned:
            print(f"Pruned {pruned} tracks older than {args.prune_days} days")

    data["meta"] = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "total_tracks": len(data["tracks"]),
        "bounds": ACTIVE_BOUNDS,
    }

    save(data, out_path, dry_run=args.dry_run)
    print("Done.")

if __name__ == "__main__":
    main()
