# Shhh — Project Status

**Last updated:** 2026-08-27

## What it is
Web map identifying locations with no audible anthropogenic sound (no flights, no roads, no rail). Leaflet frontend, flight data from OpenSky Trino, quiet-zone overlay computed from flight + road/rail proximity.

- **Repo:** https://github.com/foilsafe101/shhh
- **Live:** https://foilsafe101.github.io/shhh/ (GitHub Pages from `/docs`)
- **Audience:** Mark Tribe, SVA faculty NYC, field-recording scouting (Northeast US priority)

## ⚠️ Open issue: recorded progress does not match collected data

**The progress counter says 84% done. The repo only holds flight data for about 22% of the schedule.**

Every track in the repo — across all three data files — falls between **2025-01-06 and 2025-03-07**. That is the first two sample weeks plus five days of the third. But `query_progress_paths.json` marks slots complete all the way through 2025-11-03.

| | slots |
|---|---:|
| Marked complete, data present in repo | 67,230 |
| Marked complete, **no data in repo** | 187,172 |
| Never run | 47,998 |
| **Still to collect for a complete dataset** | **235,170** |

So the real remaining work is roughly **258 hours**, not the ~53 hours the counter implies.

Two things contributed:

1. `run_worker.sh` staged `docs/flights_*.json`. That glob matches neither `flights.json.gz` nor `flights_0.json.gz` — no flight data file has an underscore *and* a bare `.json` extension. So every batch commit pushed the updated progress file while the flight data stayed behind. `docs/flights.json.gz` has not changed in git since the repo's initial import.
2. The canonical progress file was seeded from the 12-minute-aligned subset of the older 3-minute worker progress (`canonical ∩ (worker0 ∪ worker1)` = 41,602, matching the 2026-05-05 figure in CONTEXT.md). That seed counted slots as done whose tracks live in the legacy worker files, which themselves only cover 2025-01-07 to 2025-01-08.

**This has not been fixed, because the fix is a judgement call:**

- **If Mark's Mac still has the working copy** at `/Users/Shared/shhh/docs/flights.json.gz`, the missing data may simply never have been committed. Check `meta.slots_done` inside it — if it reads ~254,402 rather than 41,802, the data is intact and just needs pushing. This is by far the cheapest outcome and worth checking before anything else.
- **Otherwise the 187,172 slots need re-running**, which means resetting `query_progress_paths.json` to the slots that actually have data. The backfill workflow will then collect them like any other pending slot — no code change needed, just a different progress file.

The `docs/status.html` progress bar reads from the same counter, so it currently overstates coverage too.

## How the backfill runs now
`.github/workflows/opensky-backfill.yml` — scheduled GitHub Actions, no laptop involved.

- Runs every 3 hours; each run queries for up to 5 hours, commits what it finished, exits. (Actions caps a job at 6 hours.)
- A concurrency group keeps exactly one worker alive at a time, which also respects OpenSky's limit of 2 concurrent queries for this account.
- When no slots are pending, runs exit immediately at the pending check, so leaving it enabled costs nothing.
- Credentials come from the `OPENSKY_USERNAME` / `OPENSKY_PASSWORD` repo secrets. pyopensky uses them for a password-grant token exchange; **without both it falls back to browser OAuth and hangs**, which is what tied this to the Mac. A preflight step fails loudly rather than hanging.
- To run it by hand: Actions tab → "OpenSky backfill" → Run workflow.

`run_worker.sh` is the old Mac supervisor. It hardcodes `/Users/Shared/shhh` and `.venv`, and it carries the staging bug above — it is superseded by the workflow.

## Key files
- `docs/index.html` — Leaflet map, canvas layer (`FlightCanvas`); loads `flights.json.gz`, `flights_0.json.gz`, `flights_1.json.gz` in parallel
- `docs/quiet_overlay.png` — Web Mercator RGBA overlay (3600×2548, ~1.7 MB), dark blue = quiet
- `docs/flights.json.gz` — current single-worker output
- `docs/flights_0.json.gz`, `docs/flights_1.json.gz` — legacy 3-minute-era worker outputs, still loaded by the map
- `query_opensky_paths.py` — OpenSky Trino queries; `--worker 0|1` splits regions 0–14 / 15–29 into separate progress and output files (unused now)
- `generate_quiet_overlay.py` — cKDTree distance from flight + road/rail samples → Web Mercator PNG
- `gen_status.py` — writes `docs/status.json` from `query_progress_paths.json`

## Sampling configuration
30 uniform tiles (30°×60°, 5 lat bands × 6 lon columns, lat −60° to 90°). Replaced the older 11 hand-drawn regions on 2026-05-03.

- `SAMPLE_INTERVAL_MINUTES = 12` (was 3 until 2026-05-05)
- 120 slots/day × 7 days × 12 sample weeks of 2025 × 30 regions = **302,400 total slots**
- `PAUSE_SECONDS = 0`, `RDP_TOLERANCE = 0.1`, `MIN_ALT_M = 300`, `MAX_ROWS_PER_SLOT = 3000`

## Acoustic thresholds
- Flights audible: **0.145°** (~10 mi)
- Roads/rail audible: 0.015° (~1 mi)
- Fully quiet: 1.5° from any noise source
- Overlay resolution: **0.1°** (~11 km at equator)

Both of the changes that were gated on 80% coverage are now applied. At 0.1° the grid is 3600×2548 = 9.2M pixels — a 6.25× pixel increase that produces a 1.7 MB PNG in about 70 seconds using ~1.4 GB RAM. Well within a runner's budget, and small enough to serve.

Note that the overlay is still computed from the incomplete dataset described above, so the quiet areas it shows are optimistic — fewer flights on file means more of the map looks quiet than really is.

## Data size headroom
GitHub's hard limit is 100 MB per file. `docs/flights.json.gz` is currently ~6 MB holding ~238k tracks. The data runs at roughly 5.7 tracks per slot, so a genuinely complete 302,400-slot dataset projects to ~1.7M tracks and ~43 MB gzipped — under the limit, but large for a browser to download in one request. If it gets uncomfortable, splitting by region or sample week is the natural next step. The workflow warns at 50 MB and fails at 90 MB.

## Architecture decisions / declined options
- **Cloud VM**: declined earlier in favour of Mark's Mac; superseded by GitHub Actions (free for public repos)
- **Web Workers for rendering**: declined — canvas layer is fast enough
- **PMTiles tile-based rendering**: deferred — revisit if rendering slows again

## Still open
- Resolve the progress/data mismatch above (check the Mac first)
- `fetch_flights.py` writes `docs/flights.json`, which is in `.gitignore`, so the nightly workflow's `git add` cannot stage it. Worth confirming whether that job does anything today.
- Altitude-dependent audible radius — low-flying aircraft carry a much smaller radius than jets
