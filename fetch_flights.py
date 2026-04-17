#!/usr/bin/env python3
"""
fetch_flights.py - Quiet Places Project
Fetches recent aircraft positions from ADS-B Exchange and saves flight paths.

ADS-B Exchange works from GitHub Actions (unlike OpenSky which blocks cloud IPs).
Get a free API key at: https://www.adsbexchange.com/data/

Set ADSBX_API_KEY as a GitHub Actions secret.

Usage:
    python fetch_flights.py                  # fetch current snapshot
    python fetch_flights.py --prune-days 90  # prune tracks older than N days
    python fetch_flights.py --dry-run        # print stats without writing
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

# ADS-B Exchange API - returns live aircraft positions
ADSBX_URL    = "https://adsbexchange.com/api/aircraft/v2/all/"
ADSBX_KEY    = os.environ.get("ADSBX_API_KEY", "")

OUTPUT_FILE  = Path(__file__).parent / "docs" / "flights.json"

# How many aircraft to sample per run (each becomes a short path segment)
MAX_AIRCRAFT = 150

# Minimum altitude in feet - filter out ground vehicles etc.
MIN_ALT_FT   = 1000

# Prune default
DEFAULT_PRUNE_DAYS = 180

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
# ADS-B Exchange fetch
# ---------------------------------------------------------------------------

def fetch_aircraft():
    """Fetch all current aircraft from ADS-B Exchange."""
    if not ADSBX_KEY:
        raise ValueError("ADSBX_API_KEY environment variable not set.")

    headers = {
        "api-auth": ADSBX_KEY,
        "Accept": "application/json",
    }
    print("Fetching aircraft from ADS-B Exchange...")
    r = requests.get(ADSBX_URL, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    aircraft = data.get("ac", [])
    print(f"  -> {len(aircraft)} aircraft in snapshot")
    return aircraft

def aircraft_to_track(ac, fetched_ts):
    """Convert a single aircraft record to our track format."""
    lat  = ac.get("lat")
    lon  = ac.get("lon")
    alt  = ac.get("alt_baro", 0)
    icao = ac.get("hex", "")

    # Skip if no position or on ground / too low
    if lat is None or lon is None:
        return None
    try:
        if float(alt) < MIN_ALT_FT:
            return None
    except (TypeError, ValueError):
        return None

    # A snapshot gives us one point per aircraft.
    # We store it as a 1-point "track" — over many runs these accumulate
    # into dense path webs as aircraft follow the same routes.
    return {
        "icao24":   icao,
        "callsign": (ac.get("flight") or "").strip(),
        "fetched":  fetched_ts,
        "path":     [[round(float(lat), 4), round(float(lon), 4)]],
    }

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prune-days", type=int, default=DEFAULT_PRUNE_DAYS)
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--output",     type=str, default=None)
    args = parser.parse_args()

    out_path  = Path(args.output) if args.output else OUTPUT_FILE
    fetched   = ts_now()

    data = load_existing(out_path)
    print(f"Loaded {len(data['tracks'])} existing tracks")

    try:
        aircraft = fetch_aircraft()
    except Exception as e:
        print(f"Failed to fetch aircraft: {e}")
        return

    # Sample evenly across the full list for global coverage
    step     = max(1, len(aircraft) // MAX_AIRCRAFT)
    sampled  = aircraft[::step][:MAX_AIRCRAFT]
    print(f"Sampling {len(sampled)} aircraft")

    new_tracks = []
    for ac in sampled:
        track = aircraft_to_track(ac, fetched)
        if track:
            new_tracks.append(track)

    print(f"Adding {len(new_tracks)} new position records")
    data["tracks"].extend(new_tracks)

    # Prune old tracks
    if args.prune_days > 0:
        cutoff = fetched - args.prune_days * 86400
        before = len(data["tracks"])
        data["tracks"] = [t for t in data["tracks"] if t["fetched"] >= cutoff]
        pruned = before - len(data["tracks"])
        if pruned:
            print(f"Pruned {pruned} tracks older than {args.prune_days} days")

    data["meta"] = {
        "last_updated":  datetime.now(timezone.utc).isoformat(),
        "total_tracks":  len(data["tracks"]),
        "source":        "adsbexchange",
    }

    save(data, out_path, dry_run=args.dry_run)
    print("Done.")

if __name__ == "__main__":
    main()
