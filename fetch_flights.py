#!/usr/bin/env python3
"""
fetch_flights.py - Quiet Places Project
Fetches live aircraft positions from adsb.lol (free, no API key required)
and appends them to flights.json.

adsb.lol is a community-run open ADS-B tracker — no signup, no key needed.
API docs: https://api.adsb.lol/docs

Usage:
    python fetch_flights.py
    python fetch_flights.py --prune-days 90
    python fetch_flights.py --dry-run
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OUTPUT_FILE    = Path(__file__).parent / "docs" / "flights.json"
MIN_ALT_FT     = 1000   # ignore ground vehicles and very low aircraft
MAX_AIRCRAFT   = 200    # how many positions to sample per run

# adsb.lol regional endpoints — we query several to get global coverage
# Each returns aircraft within 250nm of the lat/lon
REGIONS = [
    ("North Atlantic",   51.0,  -30.0),
    ("North America E",  40.0,  -75.0),
    ("North America W",  40.0, -120.0),
    ("Europe",           51.0,   10.0),
    ("Middle East",      25.0,   50.0),
    ("Asia East",        35.0,  120.0),
    ("Asia SE",          10.0,  105.0),
    ("Australia",       -30.0,  135.0),
    ("South America",   -15.0,  -55.0),
    ("Africa",           -5.0,   25.0),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ts_now():
    return int(datetime.now(timezone.utc).timestamp())

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
# adsb.lol fetch
# ---------------------------------------------------------------------------

def fetch_region(name, lat, lon):
    """Fetch aircraft within 250nm of a lat/lon point."""
    url = f"https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/250"
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "quiet-places-art-project"})
        r.raise_for_status()
        data = r.json()
        ac = data.get("ac", [])
        print(f"  {name}: {len(ac)} aircraft")
        return ac
    except Exception as e:
        print(f"  {name}: failed ({e})")
        return []

def aircraft_to_track(ac, fetched_ts):
    lat = ac.get("lat")
    lon = ac.get("lon")
    alt = ac.get("alt_baro", 0)
    if lat is None or lon is None:
        return None
    try:
        if float(alt) < MIN_ALT_FT:
            return None
    except (TypeError, ValueError):
        return None
    return {
        "icao24":   ac.get("hex", ""),
        "callsign": (ac.get("flight") or "").strip(),
        "fetched":  fetched_ts,
        "path":     [[round(float(lat), 4), round(float(lon), 4)]],
    }

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prune-days", type=int, default=180)
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--output",     type=str, default=None)
    args = parser.parse_args()

    out_path = Path(args.output) if args.output else OUTPUT_FILE
    fetched  = ts_now()

    data = load_existing(out_path)
    print(f"Loaded {len(data['tracks'])} existing tracks")
    print("Fetching aircraft from adsb.lol (no API key required)...")

    # Collect aircraft across all regions, deduplicate by ICAO
    seen = set()
    all_aircraft = []
    for name, lat, lon in REGIONS:
        ac_list = fetch_region(name, lat, lon)
        for ac in ac_list:
            icao = ac.get("hex", "")
            if icao and icao not in seen:
                seen.add(icao)
                all_aircraft.append(ac)
        time.sleep(0.3)  # be polite

    print(f"Total unique aircraft: {len(all_aircraft)}")

    # Sample evenly across all aircraft
    step    = max(1, len(all_aircraft) // MAX_AIRCRAFT)
    sampled = all_aircraft[::step][:MAX_AIRCRAFT]

    new_tracks = []
    for ac in sampled:
        track = aircraft_to_track(ac, fetched)
        if track:
            new_tracks.append(track)

    print(f"Adding {len(new_tracks)} position records")
    data["tracks"].extend(new_tracks)

    if args.prune_days > 0:
        cutoff = fetched - args.prune_days * 86400
        before = len(data["tracks"])
        data["tracks"] = [t for t in data["tracks"] if t["fetched"] >= cutoff]
        pruned = before - len(data["tracks"])
        if pruned:
            print(f"Pruned {pruned} tracks older than {args.prune_days} days")

    data["meta"] = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "total_tracks": len(data["tracks"]),
        "source":       "adsb.lol",
    }

    save(data, out_path, dry_run=args.dry_run)
    print("Done.")

if __name__ == "__main__":
    main()
