#!/usr/bin/env python3
"""
generate_quiet_overlay.py — Shhh
Generates docs/quiet_overlay.png: a world grid colored dark blue
where areas are far from all noise sources (flights, roads, railroads).

Run after each batch to keep the overlay current:
  .venv/bin/python generate_quiet_overlay.py
"""

import gzip
import json
import math
import numpy as np
from pathlib import Path

OUTPUT_FILE   = Path("docs/quiet_overlay.png")
FLIGHTS_FILE  = Path("docs/flights.json.gz")
ROADS_ZIP     = Path("data/ne_10m_roads.zip")
RAILS_ZIP     = Path("data/ne_10m_railroads.zip")

RES = 0.1           # grid resolution in degrees (~11km at equator)

# Acoustic thresholds in degrees (approximate great-circle)
# Roads/rails audible ~1 mile (0.015°), jets ~10 miles (0.145°)
NOISY_DEG_FLIGHTS = 0.145  # 10 miles
NOISY_DEG_ROADS   = 0.015  # ~1 mile
QUIET_DEG         = 1.5    # beyond this → fully opaque

LAT_MIN, LAT_MAX = -60.0, 85.0
LON_MIN, LON_MAX = -180.0, 180.0

R, G, B   = 10, 30, 120
MAX_ALPHA = 210


def lat_to_mercy(lat_deg):
    """Convert latitude (degrees) to Web Mercator Y (radians)."""
    lat_rad = math.radians(lat_deg)
    return math.log(math.tan(math.pi / 4 + lat_rad / 2))


def mercy_to_lat(y_arr):
    """Convert Web Mercator Y array (radians) to latitude (degrees)."""
    return np.degrees(2 * np.arctan(np.exp(y_arr)) - np.pi / 2)


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
    if FLIGHTS_FILE.exists():
        with gzip.open(FLIGHTS_FILE, "rt", encoding="utf-8") as f:
            data = json.load(f)
    else:
        legacy = FLIGHTS_FILE.with_suffix("")  # strip .gz
        with open(legacy) as f:
            data = json.load(f)
    tracks = data.get("tracks", [])
    flight_pts = np.array([p for t in tracks for p in t.get("path", [])], dtype=np.float32)
    print(f"  {len(tracks):,} tracks, {len(flight_pts):,} points")

    # ── Road / rail points ─────────────────────────────────────────
    print("Loading roads/railroads...")
    road_pts = load_road_rail_points().astype(np.float32)
    print(f"  {len(road_pts):,} road/rail sample points")

    # ── Web Mercator output grid ───────────────────────────────────
    # Each pixel maps to a lat/lon via inverse Mercator, so the image
    # renders without distortion in Leaflet (which uses Web Mercator).
    mercy_max = lat_to_mercy(LAT_MAX)
    mercy_min = lat_to_mercy(LAT_MIN)

    PPD    = 1.0 / RES                                          # pixels per degree = 10
    width  = int((LON_MAX - LON_MIN) * PPD)                    # 3600
    height = int((mercy_max - mercy_min) / (RES * math.pi / 180))  # ~2548

    print(f"Web Mercator grid: {width}×{height} = {width*height:,} pixels")

    # Row 0 = top (LAT_MAX), row height-1 = bottom (LAT_MIN)
    row_idx   = np.arange(height, dtype=np.float64)
    mercy_rows = mercy_max - (row_idx / height) * (mercy_max - mercy_min)
    lat_rows   = mercy_to_lat(mercy_rows)   # shape (height,)

    # Col 0 = left (LON_MIN), col width-1 = right (LON_MAX)
    col_idx  = np.arange(width, dtype=np.float64)
    lon_cols = LON_MIN + (col_idx / width) * (LON_MAX - LON_MIN)   # shape (width,)

    # Full grid of (lat, lon) query points
    merc_lat, merc_lon = np.meshgrid(lat_rows, lon_cols, indexing='ij')
    flat_lat = merc_lat.ravel().astype(np.float32)
    flat_lon = merc_lon.ravel().astype(np.float32)

    # Scale lon by cos(mean_lat) for approximate equal-distance querying
    cos_lat   = float(math.cos(math.radians(float(np.mean(lat_rows)))))
    query_pts = np.column_stack([flat_lat, flat_lon * cos_lat]).astype(np.float32)

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
    # No flipud needed: row 0 already = top (LAT_MAX) in Mercator grid
    scores_grid = scores.reshape(height, width)
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
