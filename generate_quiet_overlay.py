#!/usr/bin/env python3
"""
generate_quiet_overlay.py — Quiet Places Project
Generates docs/quiet_overlay.png: a world grid colored dark blue
where areas are far from all noise sources (flights, roads, railroads).

Run after each batch to keep the overlay current:
  .venv/bin/python generate_quiet_overlay.py
"""

import json
import numpy as np
from pathlib import Path

OUTPUT_FILE   = Path("docs/quiet_overlay.png")
FLIGHTS_FILE  = Path("docs/flights.json")
ROADS_ZIP     = Path("data/ne_10m_roads.zip")
RAILS_ZIP     = Path("data/ne_10m_railroads.zip")

RES = 0.25          # grid resolution in degrees (~28km at equator)

# Acoustic thresholds in degrees (approximate great-circle)
# Roads/rails audible ~1 mile (0.015°), jets ~20 miles (0.29°)
NOISY_DEG_FLIGHTS = 0.29   # 20 miles
NOISY_DEG_ROADS   = 0.015  # ~1 mile
QUIET_DEG         = 1.5    # beyond this → fully opaque

LAT_MIN, LAT_MAX = -60.0, 85.0
LON_MIN, LON_MAX = -180.0, 180.0

R, G, B   = 10, 30, 120
MAX_ALPHA = 210


def sample_linestring(geom, spacing=0.1):
    """Sample points along a LineString or MultiLineString at ~spacing degrees."""
    from shapely.geometry import MultiLineString, LineString
    pts = []
    geoms = geom.geoms if geom.geom_type == 'MultiLineString' else [geom]
    for line in geoms:
        coords = np.array(line.coords)
        pts.append(coords)
    return np.vstack(pts) if pts else np.empty((0, 2))


def load_road_rail_points():
    try:
        import geopandas as gpd
    except ImportError:
        print("  geopandas not installed — skipping roads/rails")
        return np.empty((0, 2))

    pts = []

    if ROADS_ZIP.exists():
        print("  Loading roads...")
        roads = gpd.read_file(f"zip://{ROADS_ZIP}")
        roads = roads[~roads['type'].isin(['Ferry Route'])]
        roads = roads[roads['featurecla'] == 'Road']
        for geom in roads.geometry:
            if geom is None: continue
            coords = sample_linestring(geom)
            if len(coords):
                pts.append(coords[:, [1, 0]])  # → [lat, lon]
        print(f"    {len(roads):,} road features")
    else:
        print("  data/ne_10m_roads.zip not found — skipping roads")

    if RAILS_ZIP.exists():
        print("  Loading railroads...")
        rails = gpd.read_file(f"zip://{RAILS_ZIP}")
        rails = rails[rails['featurecla'] == 'Railroad']
        for geom in rails.geometry:
            if geom is None: continue
            coords = sample_linestring(geom)
            if len(coords):
                pts.append(coords[:, [1, 0]])  # → [lat, lon]
        print(f"    {len(rails):,} railroad features")
    else:
        print("  data/ne_10m_railroads.zip not found — skipping railroads")

    return np.vstack(pts) if pts else np.empty((0, 2))


def main():
    from PIL import Image
    from scipy.spatial import cKDTree

    # ── Flight points ──────────────────────────────────────────────
    print("Loading flight tracks...")
    with open(FLIGHTS_FILE) as f:
        data = json.load(f)
    tracks = data.get("tracks", [])
    flight_pts = np.array([p for t in tracks for p in t.get("path", [])], dtype=np.float32)
    print(f"  {len(tracks):,} tracks, {len(flight_pts):,} points")

    # ── Road / rail points ─────────────────────────────────────────
    print("Loading roads/railroads...")
    road_pts = load_road_rail_points().astype(np.float32)
    print(f"  {len(road_pts):,} road/rail sample points")

    # ── Grid ───────────────────────────────────────────────────────
    lats = np.arange(LAT_MIN, LAT_MAX, RES)
    lons = np.arange(LON_MIN, LON_MAX, RES)
    height, width = len(lats), len(lons)
    print(f"Grid: {width}×{height} = {width*height:,} cells")

    grid_lat, grid_lon = np.meshgrid(lats, lons, indexing='ij')

    # Scale lon by cos(mean_lat) for approximate equal-distance querying
    mean_lat_rad = np.radians(np.mean(lats))
    cos_lat = float(np.cos(mean_lat_rad))

    flat_lat = grid_lat.ravel()
    flat_lon = grid_lon.ravel() * cos_lat
    query_pts = np.column_stack([flat_lat, flat_lon])

    # ── Flight distances ───────────────────────────────────────────
    print("Computing flight distances...")
    f_scaled = flight_pts.copy()
    f_scaled[:, 1] *= cos_lat
    f_dists, _ = cKDTree(f_scaled).query(query_pts, k=1, workers=-1)

    # ── Road/rail distances ────────────────────────────────────────
    if len(road_pts):
        print("Computing road/rail distances...")
        r_scaled = road_pts.copy()
        r_scaled[:, 1] *= cos_lat
        r_dists, _ = cKDTree(r_scaled).query(query_pts, k=1, workers=-1)
    else:
        r_dists = np.full(len(query_pts), 999.0, dtype=np.float32)

    # ── Quiet score: min distance in "noisy units" ─────────────────
    # Normalize each source by its own threshold so 1.0 = just outside audibility
    f_norm = f_dists / NOISY_DEG_FLIGHTS
    r_norm = r_dists / NOISY_DEG_ROADS

    # A place is only quiet if it clears BOTH thresholds
    combined_norm = np.minimum(f_norm, r_norm)

    # Map to [0,1]: 0 = noisy, 1 = very quiet
    scores = np.clip((combined_norm - 1.0) / ((QUIET_DEG / NOISY_DEG_FLIGHTS) - 1.0), 0.0, 1.0)

    # ── Build RGBA image ───────────────────────────────────────────
    scores_grid = np.flipud(scores.reshape(height, width))
    alpha = (scores_grid * MAX_ALPHA).astype(np.uint8)

    img_arr = np.zeros((height, width, 4), dtype=np.uint8)
    img_arr[:, :, 0] = R
    img_arr[:, :, 1] = G
    img_arr[:, :, 2] = B
    img_arr[:, :, 3] = alpha

    img = Image.fromarray(img_arr, "RGBA")
    img.save(OUTPUT_FILE, optimize=True)
    kb = OUTPUT_FILE.stat().st_size / 1024
    print(f"Saved {OUTPUT_FILE} ({width}×{height}, {kb:.0f} KB)")
    print(f"  Flight dist range: {f_dists.min():.2f}°–{f_dists.max():.2f}°")
    print(f"  Road dist range:   {r_dists.min():.3f}°–{r_dists.max():.2f}°")
    quiet_pct = (scores > 0).mean() * 100
    print(f"  Cells with any quiet score: {quiet_pct:.1f}%")


if __name__ == "__main__":
    main()
