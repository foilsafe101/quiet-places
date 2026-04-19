#!/usr/bin/env python3
"""
generate_quiet_overlay.py — Quiet Places Project
Generates docs/quiet_overlay.png: a world grid colored dark blue
where areas are far from any flight track, transparent where noisy.

Run after each batch to keep the overlay current:
  .venv/bin/python generate_quiet_overlay.py
"""

import json
import numpy as np
from PIL import Image
from scipy.spatial import cKDTree
from pathlib import Path

FLIGHTS_FILE  = Path("docs/flights.json")
OUTPUT_FILE   = Path("docs/quiet_overlay.png")

# Grid resolution in degrees (~28km at equator)
RES = 0.25

# Acoustic thresholds (degrees, approximate)
# 20 miles ≈ 0.29°  → edge of audibility for jets at 35,000ft
# 60 miles ≈ 0.87°  → well clear
NOISY_DEG  = 0.29
QUIET_DEG  = 1.5    # beyond this = fully opaque dark blue

# Latitude range (skip poles)
LAT_MIN, LAT_MAX = -60.0, 75.0
LON_MIN, LON_MAX = -180.0, 180.0

# Overlay color: dark blue RGBA
R, G, B = 10, 30, 120
MAX_ALPHA = 210   # max opacity (not fully opaque so basemap shows through)


def main():
    print("Loading flight tracks...")
    with open(FLIGHTS_FILE) as f:
        data = json.load(f)

    tracks = data.get("tracks", [])
    print(f"  {len(tracks):,} tracks")

    # Collect all track points
    pts = []
    for t in tracks:
        for p in t.get("path", []):
            pts.append(p)   # [lat, lon]

    if not pts:
        print("No points found — nothing to render.")
        return

    pts = np.array(pts, dtype=np.float32)
    print(f"  {len(pts):,} total points")

    # Build spatial index
    print("Building KD-tree...")
    # Scale lon by cos(mean_lat) to approximate equal-distance
    mean_lat_rad = np.radians(np.mean(pts[:, 0]))
    cos_lat = np.cos(mean_lat_rad)
    scaled = pts.copy()
    scaled[:, 1] *= cos_lat
    tree = cKDTree(scaled)

    # Generate query grid
    lats = np.arange(LAT_MIN, LAT_MAX, RES)
    lons = np.arange(LON_MIN, LON_MAX, RES)
    height, width = len(lats), len(lons)
    print(f"  Grid: {width}x{height} = {width*height:,} cells")

    grid_lat, grid_lon = np.meshgrid(lats, lons, indexing='ij')
    grid_pts = np.column_stack([grid_lat.ravel(), grid_lon.ravel() * cos_lat])

    print("Querying distances...")
    dists, _ = tree.query(grid_pts, k=1, workers=-1)
    dists = dists.reshape(height, width)

    # Normalize to [0, 1] quiet score
    scores = np.clip((dists - NOISY_DEG) / (QUIET_DEG - NOISY_DEG), 0.0, 1.0)

    # Build RGBA image (flip vertically: image row 0 = top = max lat)
    scores_flipped = np.flipud(scores)
    alpha = (scores_flipped * MAX_ALPHA).astype(np.uint8)

    img_arr = np.zeros((height, width, 4), dtype=np.uint8)
    img_arr[:, :, 0] = R
    img_arr[:, :, 1] = G
    img_arr[:, :, 2] = B
    img_arr[:, :, 3] = alpha

    img = Image.fromarray(img_arr, "RGBA")
    img.save(OUTPUT_FILE, optimize=True)
    kb = OUTPUT_FILE.stat().st_size / 1024
    print(f"Saved {OUTPUT_FILE} ({width}x{height}, {kb:.0f} KB)")
    print(f"  Distance range: {dists.min():.2f}° – {dists.max():.2f}°")
    print(f"  Quiet cells (>{NOISY_DEG}°): {(dists > NOISY_DEG).mean()*100:.1f}%")


if __name__ == "__main__":
    main()
